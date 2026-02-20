import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.broker import Broker, BrokerEmail
from app.models.operations import BrokerOverride


def triage_broker_contact(
    db: Session,
    mc_number: str | None,
    load_id: int,
    driver_id: int | None,
    contact_instructions: str | None = "email",
) -> dict[str, Any]:
    standing: dict[str, str | None] = {"status": "NEUTRAL", "note": None}

    if not mc_number:
        logging.info("Load %s missing MC number; manual enrichment required.", load_id)
        return {
            "action": "MANUAL_ENRICHMENT_REQUIRED",
            "email": None,
            "phone": None,
            "standing": {"status": "UNKNOWN", "note": None},
            "reason": "missing_mc_number",
        }

    override = None
    if driver_id is not None:
        override = (
            db.query(BrokerOverride)
            .filter(
                BrokerOverride.driver_id == driver_id,
                BrokerOverride.broker_mc_number == mc_number,
            )
            .first()
        )
    if override is None:
        override = (
            db.query(BrokerOverride)
            .filter(BrokerOverride.broker_mc_number == mc_number)
            .order_by(BrokerOverride.updated_at.desc())
            .first()
        )

    broker_record = db.query(Broker).filter(Broker.mc_number == mc_number).first()
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
        .filter(BrokerEmail.mc_number == mc_number)
        .order_by(BrokerEmail.confidence.desc())
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