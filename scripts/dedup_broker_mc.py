#!/usr/bin/env python3
"""
Broker vault deduplication script.

Finds MC numbers that are padding variants of the same broker
(e.g. "42910" and "042910") and merges them into the canonical form
(the row with a non-empty company_name, or the longest/most-padded MC
if both have names).

Safe to run repeatedly — it's idempotent.

Usage (run inside the app container):
    docker exec gcloads_api python3 /code/dedup_broker_mc.py --dry-run
    docker exec gcloads_api python3 /code/dedup_broker_mc.py

Or with an explicit DATABASE_URL:
    DATABASE_URL=postgresql://... python3 scripts/dedup_broker_mc.py --dry-run
"""
import argparse
import os
import sys

import psycopg2
import psycopg2.extras


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Fall back to .env in the project root
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DATABASE_URL="):
                        url = line.split("=", 1)[1].strip()
                        break
    if not url:
        print("ERROR: DATABASE_URL not set (checked env + .env)", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(url)


def find_duplicate_groups(cur) -> list[dict]:
    """Return groups where multiple broker rows share the same 7-digit zero-padded canonical."""
    cur.execute("""
        SELECT
            LPAD(mc_number, 7, '0')          AS canonical,
            array_agg(mc_number ORDER BY
                CASE WHEN company_name IS NOT NULL AND company_name != '' THEN 0 ELSE 1 END,
                LENGTH(mc_number) DESC,
                mc_number
            )                                AS variants,
            array_agg(company_name ORDER BY
                CASE WHEN company_name IS NOT NULL AND company_name != '' THEN 0 ELSE 1 END,
                LENGTH(mc_number) DESC,
                mc_number
            )                                AS names
        FROM webwise.brokers
        WHERE mc_number ~ '^\\d{4,8}$'
        GROUP BY LPAD(mc_number, 7, '0')
        HAVING count(*) > 1
        ORDER BY canonical
    """)
    return cur.fetchall()


def merge_group(cur, variants: list[str], dry_run: bool) -> dict:
    """
    Merge all variants into variants[0] (the preferred canonical).
    variants[0] is already ordered: real-name row first, then longest MC, then alpha.
    """
    keeper = variants[0]
    artifacts = variants[1:]
    moved_emails = 0
    deleted_brokers = 0

    for artifact in artifacts:
        # Re-parent broker_emails — skip if email already exists on keeper (ON CONFLICT)
        if not dry_run:
            cur.execute("""
                UPDATE webwise.broker_emails
                SET mc_number = %s
                WHERE mc_number = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM webwise.broker_emails
                      WHERE mc_number = %s AND email = broker_emails.email
                  )
            """, (keeper, artifact, keeper))
            moved_emails += cur.rowcount

            # Delete any remaining emails on artifact (duplicates that already exist on keeper)
            cur.execute("DELETE FROM webwise.broker_emails WHERE mc_number = %s", (artifact,))

            # broker_overrides: re-parent if keeper doesn't already have one for same driver
            cur.execute("""
                UPDATE public.broker_overrides
                SET broker_mc_number = %s
                WHERE broker_mc_number = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM public.broker_overrides
                      WHERE broker_mc_number = %s AND driver_id = broker_overrides.driver_id
                  )
            """, (keeper, artifact, keeper))

            # Delete remaining overrides on artifact (duplicates)
            cur.execute(
                "DELETE FROM public.broker_overrides WHERE broker_mc_number = %s", (artifact,)
            )

            # negotiations: re-parent (FK allows any valid mc_number)
            cur.execute("""
                UPDATE public.negotiations
                SET broker_mc_number = %s
                WHERE broker_mc_number = %s
            """, (keeper, artifact))

            cur.execute("DELETE FROM webwise.brokers WHERE mc_number = %s", (artifact,))
            deleted_brokers += 1
        else:
            print(f"  [dry-run] would merge {artifact!r} → {keeper!r}")

    return {"keeper": keeper, "artifacts": artifacts, "moved_emails": moved_emails, "deleted_brokers": deleted_brokers}


def main():
    parser = argparse.ArgumentParser(description="Deduplicate padded/unpadded MC variants in webwise.brokers")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen without making changes")
    args = parser.parse_args()

    conn = get_conn()
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            groups = find_duplicate_groups(cur)

        if not groups:
            print("No duplicate MC groups found. Vault is clean.")
            return

        print(f"Found {len(groups)} duplicate group(s):")
        total_deleted = 0

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for group in groups:
                variants = list(group["variants"])
                names = list(group["names"])
                print(f"\n  canonical={group['canonical']!r}  variants={variants}  names={names}")
                result = merge_group(cur, variants, dry_run=args.dry_run)
                if not args.dry_run:
                    print(f"  → kept {result['keeper']!r}, deleted {result['deleted_brokers']} artifact(s), moved {result['moved_emails']} email(s)")
                    total_deleted += result["deleted_brokers"]

        if not args.dry_run:
            conn.commit()
            print(f"\nDone. {total_deleted} artifact broker row(s) removed.")
        else:
            conn.rollback()
            print("\n[dry-run] No changes made.")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
