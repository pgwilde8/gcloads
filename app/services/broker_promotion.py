"""
Broker promotion service.

Promotes contact intelligence harvested by Scout into the webwise broker vault.
Rules:
  - Never overwrite existing non-empty values (fill gaps only)
  - Email → webwise.broker_emails with confidence=0.8, source='scout' (ON CONFLICT DO NOTHING)
  - primary_email on broker → only if currently empty
  - primary_phone → only if currently empty
  - dot_number → only if currently empty
  - preferred_contact_method → only if currently empty
"""
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_DIGITS_RE = re.compile(r"\D")


def _normalize_email(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned if _EMAIL_RE.match(cleaned) else None


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = _DIGITS_RE.sub("", value)
    return digits if len(digits) >= 10 else None


def promote_scout_contact(
    db: Session,
    *,
    mc_number: str | None,
    dot_number: str | None,
    contact_info: dict[str, Any],
    contact_mode: str,
    source_platform: str | None,
) -> None:
    """
    Promote Scout-harvested contact data into webwise broker vault.
    Called inside ingest_load() after contact_mode is resolved, before triage.
    No-ops silently if mc_number is absent or broker is not in the vault.
    All writes are gap-fills only — existing values are never overwritten.
    """
    if not mc_number:
        return

    broker_row = db.execute(
        text("""
            SELECT mc_number, primary_email, primary_phone, dot_number,
                   preferred_contact_method
            FROM webwise.brokers
            WHERE mc_number = :mc
        """),
        {"mc": mc_number},
    ).mappings().first()

    if not broker_row:
        # Broker not in vault — Scout data alone is not enough to create a broker row
        # (we'd be missing company_name, rating, etc.). Log at INFO so "why no email?" is debuggable.
        logger.info(
            "broker_promotion: broker_not_in_vault mc=%s source=%s — skipping enrichment",
            mc_number,
            source_platform or "unknown",
        )
        return

    email = _normalize_email(contact_info.get("email"))
    phone = _normalize_phone(contact_info.get("phone"))
    updates: dict[str, Any] = {}

    # --- Email into broker_emails table (always attempt, idempotent) ---
    if email:
        result = db.execute(
            text("""
                INSERT INTO webwise.broker_emails
                    (mc_number, email, source, confidence)
                VALUES
                    (:mc, :email, 'scout', 0.80)
                ON CONFLICT (mc_number, email) DO NOTHING
                RETURNING id
            """),
            {"mc": mc_number, "email": email},
        ).first()
        if result:
            logger.info("broker_promotion: new_email inserted email=%s mc=%s source=%s", email, mc_number, source_platform or "unknown")
        else:
            logger.debug("broker_promotion: email_already_exists email=%s mc=%s (no-op)", email, mc_number)

        # Also fill primary_email on broker row if empty
        if not broker_row["primary_email"]:
            updates["primary_email"] = email

    # --- Phone ---
    if phone and not broker_row["primary_phone"]:
        updates["primary_phone"] = phone

    # --- DOT number ---
    if dot_number and not broker_row["dot_number"]:
        updates["dot_number"] = dot_number.strip()

    # --- Preferred contact method ---
    if contact_mode in ("call", "email") and not broker_row["preferred_contact_method"]:
        updates["preferred_contact_method"] = contact_mode

    if updates:
        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        updates["mc"] = mc_number
        db.execute(
            text(f"UPDATE webwise.brokers SET {set_clause} WHERE mc_number = :mc"),
            updates,
        )
        logger.info(
            "broker_promotion: enriched mc=%s fields=%s source=%s",
            mc_number,
            list(updates.keys() - {"mc"}),
            source_platform or "unknown",
        )
