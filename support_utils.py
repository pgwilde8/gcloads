#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from textwrap import dedent


def _run_psql_query(sql: str, *, db_container: str, db_user: str, db_name: str) -> int:
    command = [
        "docker",
        "exec",
        "-i",
        db_container,
        "psql",
        "-U",
        db_user,
        "-d",
        db_name,
        "-c",
        sql,
    ]

    result = subprocess.run(command, check=False)
    return int(result.returncode)


def _proof_of_send_sql(negotiation_id: int) -> str:
    return dedent(
        f"""
        SELECT
            ps.id AS snapshot_id,
            ps.sent_at,
            dd.doc_type,
            dd.file_key,
            LEFT(dd.sha256_hash, 12) AS hash_prefix
        FROM packet_snapshots ps
        CROSS JOIN LATERAL jsonb_array_elements_text(ps.metadata->'doc_ids') AS d_id
        JOIN driver_documents dd ON dd.id = d_id::int
        WHERE ps.negotiation_id = {int(negotiation_id)}
        ORDER BY ps.sent_at DESC, dd.doc_type ASC;
        """
    ).strip()


def _snapshot_history_sql(negotiation_id: int) -> str:
    return dedent(
        f"""
        SELECT
            id,
            negotiation_id,
            driver_id,
            version_label,
            sent_at,
            recipient_email,
            metadata
        FROM packet_snapshots
        WHERE negotiation_id = {int(negotiation_id)}
        ORDER BY sent_at DESC
        LIMIT 20;
        """
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Support forensic utilities for packet snapshot disputes.")
    parser.add_argument("--negotiation", type=int, required=True, help="Negotiation ID to audit")
    parser.add_argument(
        "--query",
        choices=["proof", "snapshots", "all"],
        default="all",
        help="Which query bundle to run",
    )
    parser.add_argument("--db-container", default="gcloads_db")
    parser.add_argument("--db-user", default="gcd_admin")
    parser.add_argument("--db-name", default="gcloads_db")
    args = parser.parse_args()

    print(f"Support audit for negotiation {args.negotiation}\n")

    exit_code = 0

    if args.query in {"proof", "all"}:
        print("=== Proof of Send (snapshot -> documents) ===")
        exit_code |= _run_psql_query(
            _proof_of_send_sql(args.negotiation),
            db_container=args.db_container,
            db_user=args.db_user,
            db_name=args.db_name,
        )
        print()

    if args.query in {"snapshots", "all"}:
        print("=== Snapshot History ===")
        exit_code |= _run_psql_query(
            _snapshot_history_sql(args.negotiation),
            db_container=args.db_container,
            db_user=args.db_user,
            db_name=args.db_name,
        )

    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
