import json
import os
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import Negotiation
from app.services.broker_intelligence import triage_broker_contact
from app.services.broker_promotion import promote_scout_contact
from app.services.email import send_negotiation_email
from app.services.parser_rules import load_parsing_rules, resolve_contact_mode


router = APIRouter(prefix="/api/ingest", tags=["ingest"])
scout_router = APIRouter(prefix="/api/scout", tags=["scout"])


class LoadIn(BaseModel):
    ref_id: str
    origin: str
    destination: str
    mc_number: str | None = None
    price: str
    equipment_type: str
    metadata: dict | None = None
    raw_data: dict | None = None


class ScoutIngestIn(BaseModel):
    load_id: str
    source: str | None = None
    mc_number: str | None = None
    dot_number: str | None = None
    email: str | None = None
    phone: str | None = None
    origin: str = ""
    destination: str = ""
    price: str | None = None
    equipment_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    contact_info: dict[str, Any] | None = None
    raw_notes: str | None = None
    contact_instructions: str = "email"
    driver_id: int | None = None
    auto_bid: bool = False


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Legacy shared-key check used only for the bulk /api/ingest/loads endpoint."""
    scout_api_key = os.getenv("SCOUT_API_KEY", "")
    if not scout_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scout_api_key_not_configured",
        )
    if x_api_key != scout_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_api_key",
        )


def _resolve_driver_by_key(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Driver:
    """Authenticate a Scout extension request by per-driver API key.

    Returns the Driver row so the ingest handler never needs to trust
    a client-supplied driver_id field.
    """
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_api_key")
    driver = db.query(Driver).filter(Driver.scout_api_key == x_api_key).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key")
    return driver


@router.post("/loads")
def ingest_loads(
    loads: list[LoadIn],
    _: None = Depends(_require_api_key),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    inserted = 0
    skipped = 0

    for item in loads:
        existing = db.query(Load.id).filter(Load.ref_id == item.ref_id).first()
        if existing:
            skipped += 1
            continue

        load = Load(
            ref_id=item.ref_id,
            origin=item.origin,
            destination=item.destination,
            mc_number=item.mc_number,
            price=item.price,
            equipment_type=item.equipment_type,
            load_metadata=item.metadata,
            raw_data=json.dumps(item.raw_data) if item.raw_data is not None else None,
        )
        db.add(load)
        inserted += 1

    db.commit()
    return {"received": len(loads), "inserted": inserted, "skipped": skipped}


@scout_router.post("/ingest")
async def ingest_load(
    data: ScoutIngestIn = Body(...),
    background_tasks: BackgroundTasks = None,
    driver: Driver = Depends(_resolve_driver_by_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    merged_metadata = dict(data.metadata)
    contact_info = dict(data.contact_info or {})
    if data.email and "email" not in contact_info:
        contact_info["email"] = data.email
    if data.phone and "phone" not in contact_info:
        contact_info["phone"] = data.phone

    if data.raw_notes:
        merged_metadata["notes"] = data.raw_notes
    if contact_info:
        merged_metadata["contact_info"] = contact_info
    if data.dot_number:
        merged_metadata["dot_number"] = data.dot_number

    contact_mode = resolve_contact_mode(data.contact_instructions, merged_metadata)

    existing = db.query(Load).filter(Load.ref_id == data.load_id).first()
    # driver is now resolved from the API key — no longer trusted from payload
    raw_identity = driver.display_name if driver else "dispatch"
    identity = re.sub(r"[^a-z0-9]", "", (raw_identity or "").lower()) or "dispatch"

    def should_send_auto_bid(
        load_obj: Load,
        result_payload: dict[str, Any],
        broker_email: str | None,
    ) -> tuple[bool, str | None]:
        standing = result_payload.get("standing") or {}
        if standing.get("status") == "BLACKLISTED":
            return False, "blacklisted"
        if not data.auto_bid:
            return False, "auto_bid_disabled"
        if not broker_email:
            return False, "missing_broker_email"
        if not driver:
            return False, "missing_driver"
        if not load_obj.mc_number:
            return False, "missing_mc_number"

        existing_neg = (
            db.query(Negotiation.id)
            .filter(
                Negotiation.load_id == load_obj.id,
                Negotiation.driver_id == driver.id,
            )
            .first()
        )
        if existing_neg:
            return False, "negotiation_exists"
        return True, None

    def enqueue_auto_bid(load_obj: Load, result_payload: dict[str, Any]) -> tuple[bool, str | None]:
        broker_email = result_payload.get("email") or contact_info.get("email")
        should_send, skip_reason = should_send_auto_bid(load_obj, result_payload, broker_email)
        if not should_send:
            return False, skip_reason

        negotiation = Negotiation(
            load_id=load_obj.id,
            driver_id=driver.id,
            broker_mc_number=load_obj.mc_number,
            status="Sent",
        )
        db.add(negotiation)
        db.commit()

        if background_tasks is not None:
            background_tasks.add_task(
                send_negotiation_email,
                broker_email,
                load_obj.ref_id,
                load_obj.origin,
                load_obj.destination,
                identity,
                load_obj.source_platform,
            )
            return True, None
        return False, "background_tasks_unavailable"

    if existing:
        if data.price:
            existing.price = data.price
        if data.origin:
            existing.origin = data.origin
        if data.destination:
            existing.destination = data.destination
        if data.source:
            existing.source_platform = data.source.lower()
        if data.mc_number:
            existing.mc_number = data.mc_number

        existing.load_metadata = merged_metadata
        existing.contact_instructions = contact_mode
        existing.raw_data = json.dumps(merged_metadata) if merged_metadata else None
        db.commit()

        promote_scout_contact(
            db,
            mc_number=existing.mc_number or data.mc_number,
            dot_number=data.dot_number,
            contact_info=contact_info,
            contact_mode=contact_mode,
            source_platform=data.source,
        )
        db.commit()

        result = triage_broker_contact(
            db,
            existing.mc_number or data.mc_number,
            existing.id,
            data.driver_id,
            contact_mode,
        )
        email_sent, skipped_reason = enqueue_auto_bid(existing, result)
        return {
            "status": "success",
            "load_id": existing.id,
            "next_step": result["action"],
            "reason": result.get("reason"),
            "contact_mode": contact_mode,
            "broker_email": result.get("email"),
            "broker_phone": result.get("phone"),
            "standing": result.get("standing"),
            "identity_used": f"{identity}@{os.getenv('EMAIL_DOMAIN', 'gcdloads.com')}",
            "email_sent": email_sent,
            "email_skipped_reason": skipped_reason,
        }

    new_load = Load(
        ref_id=data.load_id,
        mc_number=data.mc_number,
        source_platform=(data.source or "unknown").lower(),
        origin=data.origin,
        destination=data.destination,
        price=data.price or "",
        equipment_type=data.equipment_type or "",
        load_metadata=merged_metadata,
        contact_instructions=contact_mode,
        raw_data=json.dumps(merged_metadata) if merged_metadata else None,
    )
    db.add(new_load)
    db.commit()
    db.refresh(new_load)

    promote_scout_contact(
        db,
        mc_number=new_load.mc_number,
        dot_number=data.dot_number,
        contact_info=contact_info,
        contact_mode=contact_mode,
        source_platform=data.source,
    )
    db.commit()

    result = triage_broker_contact(
        db,
        new_load.mc_number,
        new_load.id,
        data.driver_id,
        contact_mode,
    )
    email_sent, skipped_reason = enqueue_auto_bid(new_load, result)

    return {
        "status": "success",
        "load_id": new_load.id,
        "next_step": result["action"],
        "reason": result.get("reason"),
        "contact_mode": contact_mode,
        "broker_email": result.get("email"),
        "broker_phone": result.get("phone"),
        "standing": result.get("standing"),
        "identity_used": f"{identity}@{os.getenv('EMAIL_DOMAIN', 'gcdloads.com')}",
        "email_sent": email_sent,
        "email_skipped_reason": skipped_reason,
    }


@scout_router.get("/parsing-rules")
def get_parsing_rules(
    _driver: Driver = Depends(_resolve_driver_by_key),
) -> dict[str, Any]:
    return load_parsing_rules()