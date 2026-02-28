import logging
import re
from typing import Any

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models.broker import Broker, BrokerEmail
from app.models.operations import BrokerOverride

_MC_STRIP_RE = re.compile(r"[^0-9]")
_MC_DIGITS_RE = re.compile(r"^\d{4,8}$")
_MC_FF_RE = re.compile(r"^FF\d+$")  # always matched against uppercased input


def normalize_mc(raw: str | None) -> str | None:
    """Normalize an MC number string to its canonical form.

    Handles two valid formats:
      - Standard motor carrier: digits only, 4–8 chars (e.g. "009153", "1234567")
        Strips "MC"/"mc" prefix and whitespace/dashes before validating.
      - Freight forwarder: FF prefix + digits (e.g. "FF003723")
        Uppercased and returned as-is.

    Returns None for anything that doesn't match either pattern (DOT numbers,
    carrier MCs misidentified as broker MCs, garbage values).
    """
    if not raw:
        return None
    stripped = raw.strip()

    # Freight forwarder MC — normalize separators before testing (handles "FF-003723", "ff 003723")
    ff_candidate = re.sub(r"[^A-Za-z0-9]", "", stripped).upper()
    if _MC_FF_RE.match(ff_candidate):
        return ff_candidate

    digits = _MC_STRIP_RE.sub("", stripped)
    return digits if _MC_DIGITS_RE.match(digits) else None


def _mc_candidates(mc: str) -> list[str]:
    """Return lookup candidates to handle zero-padding mismatches.

    Load boards sometimes strip leading zeros (e.g. "9153" instead of "009153").
    For standard digit-only MCs we generate the as-is value plus zero-padded
    variants up to 6 and 7 digits so a short unpadded MC can still resolve
    against a padded DB record.

    FF-prefix freight forwarder MCs are returned as a single-element list —
    they have no zero-padding ambiguity.

    Duplicates are removed while preserving order (as-is first).
    """
    if mc.startswith("FF"):
        return [mc]

    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in [mc, mc.zfill(6), mc.zfill(7)]:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def triage_broker_contact(
    db: Session,
    mc_number: str | None,
    load_id: int,
    driver_id: int | None,
    contact_instructions: str | None = "email",
) -> dict[str, Any]:
    standing: dict[str, str | None] = {"status": "NEUTRAL", "note": None}

    mc_number = normalize_mc(mc_number)

    if not mc_number:
        logging.info("Load %s missing or malformed MC number; manual enrichment required.", load_id)
        return {
            "action": "MANUAL_ENRICHMENT_REQUIRED",
            "email": None,
            "phone": None,
            "standing": {"status": "UNKNOWN", "note": None},
            "reason": "missing_mc_number",
        }

    candidates = _mc_candidates(mc_number)

    override = None
    if driver_id is not None:
        override = (
            db.query(BrokerOverride)
            .filter(
                BrokerOverride.driver_id == driver_id,
                BrokerOverride.broker_mc_number.in_(candidates),
            )
            .order_by(BrokerOverride.updated_at.desc())
            .first()
        )
    if override is None:
        override = (
            db.query(BrokerOverride)
            .filter(BrokerOverride.broker_mc_number.in_(candidates))
            .order_by(BrokerOverride.updated_at.desc())
            .first()
        )

    # Prefer: non-empty company_name first, then most recently updated, then stable by mc_number
    broker_record = (
        db.query(Broker)
        .filter(Broker.mc_number.in_(candidates))
        .order_by(
            case(
                (Broker.company_name.is_(None), 1),
                (Broker.company_name == "", 1),
                else_=0,
            ),
            Broker.updated_at.desc(),
            Broker.mc_number,
        )
        .first()
    )
    phone = None
    if broker_record:
        phone = broker_record.primary_phone or broker_record.secondary_phone
        if broker_record.internal_note:
            standing = {"status": "NOTE", "note": broker_record.internal_note}

    if override:
        override_note = override.notes or "DO NOT BOOK"
        if override.is_blocked:
            standing = {"status": "BLACKLISTED", "note": override_note}
        elif getattr(override, "is_preferred", False):
            standing = {"status": "PREFERRED", "note": "Top Tier Broker"}
        elif override.notes:
            standing = {"status": "NOTE", "note": override.notes}

    if standing["status"] == "BLACKLISTED":
        logging.info("Load %s blocked by broker standing BLACKLISTED for MC %s.", load_id, mc_number)
        return {
            "action": "BROKER_BLOCKED",
            "email": None,
            "phone": phone,
            "standing": standing,
            "reason": "driver_override_blacklist",
        }

    if (contact_instructions or "email").lower() == "call":
        logging.info("Load %s requires call workflow for MC %s.", load_id, mc_number)
        return {
            "action": "CALL_REQUIRED",
            "email": None,
            "phone": phone,
            "standing": standing,
            "reason": "contact_instruction_call",
        }

    broker_email_record = (
        db.query(BrokerEmail)
        .filter(BrokerEmail.mc_number.in_(candidates))
        .order_by(BrokerEmail.confidence.desc(), BrokerEmail.created_at.desc(), BrokerEmail.id.desc())
        .first()
    )
    if broker_email_record:
        logging.info("Direct email hit for MC %s: %s", mc_number, broker_email_record.email)
        return {
            "action": "EMAIL_BROKER",
            "email": broker_email_record.email,
            "phone": phone,
            "standing": standing,
            "reason": "broker_email_found",
        }

    if broker_record:
        logging.info("MC %s found in broker directory without email; call workflow.", mc_number)
        return {
            "action": "CALL_REQUIRED",
            "email": None,
            "phone": phone,
            "standing": standing,
            "reason": "broker_found_no_email",
        }

    logging.info("MC %s not found; enrichment queued.", mc_number)
    return {
        "action": "ENRICHMENT_QUEUED",
        "email": None,
        "phone": None,
        "standing": standing,
        "reason": "broker_not_found",
    }