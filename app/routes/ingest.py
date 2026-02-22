import json
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import Negotiation
from app.services.broker_intelligence import triage_broker_contact
from app.services.broker_promotion import promote_scout_contact
from app.services.email import send_negotiation_email
from app.services.parser_rules import load_parsing_rules, resolve_contact_mode
from app.services.scout_matching import compute_match

logger = logging.getLogger(__name__)

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
    # auto_bid is kept for backward compat but is no longer authoritative.
    # The backend decides based on match score + driver profile.
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
    """Authenticate a Scout extension request by per-driver API key."""
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_api_key")
    driver = db.query(Driver).filter(Driver.scout_api_key == x_api_key).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key")
    return driver


# ── Bulk ingest (legacy, shared key) ──────────────────────────────────────────

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


# ── Scout single-load ingest ───────────────────────────────────────────────────

def _upsert_load(db: Session, data: ScoutIngestIn, merged_metadata: dict, contact_mode: str, driver_id: int) -> Load:
    """Insert or update a Load row.  Returns the committed Load object."""
    existing = db.query(Load).filter(Load.ref_id == data.load_id).first()
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
        if data.equipment_type:
            existing.equipment_type = data.equipment_type
        existing.load_metadata = merged_metadata
        existing.contact_instructions = contact_mode
        existing.raw_data = json.dumps(merged_metadata) if merged_metadata else None
        existing.ingested_by_driver_id = driver_id
        db.commit()
        return existing

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
        ingested_by_driver_id=driver_id,
    )
    db.add(new_load)
    db.commit()
    db.refresh(new_load)
    return new_load


def _get_or_create_negotiation(
    db: Session,
    load: Load,
    driver: Driver,
    target_status: str,
    match_score: int,
    match_details: dict,
) -> tuple[Negotiation, bool]:
    """Return (negotiation, created).

    If a negotiation already exists for this (driver, load) pair and is in a
    terminal or active state (Sent, Queued, CLOSED, WON), do not create another.
    If it exists as Dismissed or Draft, update it to the new target_status.
    """
    existing = (
        db.query(Negotiation)
        .filter(
            Negotiation.driver_id == driver.id,
            Negotiation.load_id == load.id,
        )
        .first()
    )

    if existing:
        active_statuses = {"Sent", "Queued", "CLOSED", "WON"}
        if existing.status in active_statuses:
            return existing, False
        # Re-activate a dismissed/draft negotiation
        existing.status = target_status
        existing.match_score = match_score
        existing.match_details = match_details
        db.commit()
        return existing, False

    neg = Negotiation(
        load_id=load.id,
        driver_id=driver.id,
        broker_mc_number=load.mc_number or "UNKNOWN",
        status=target_status,
        match_score=match_score,
        match_details=match_details,
    )
    db.add(neg)
    db.commit()
    db.refresh(neg)
    return neg, True


def _decide_next_step(
    driver: Driver,
    match: dict,
    triage: dict,
    broker_email: str | None,
) -> str:
    """Pure function: return the next_step string given all inputs."""
    # 1. Driver not configured or paused
    if not driver.scout_active:
        return "SCOUT_PAUSED"

    profile_set = bool(
        driver.preferred_origin_region
        or driver.preferred_destination_region
        or driver.min_cpm
        or driver.preferred_equipment_type
    )
    if not profile_set:
        return "SETUP_REQUIRED"

    # 2. Broker standing gates
    standing_status = (triage.get("standing") or {}).get("status", "NEUTRAL")
    if standing_status == "BLACKLISTED":
        return "BROKER_BLOCKED"

    triage_action = triage.get("action", "")
    if triage_action == "CALL_REQUIRED":
        return "CALL_REQUIRED"

    if not broker_email:
        return "MISSING_BROKER_EMAIL"

    # 3. Score-based routing
    score = match["score"]
    threshold = driver.approval_threshold if driver.approval_threshold is not None else 3

    if score == 4 and driver.auto_send_on_perfect_match:
        return "AUTO_SENT"

    if score >= threshold:
        return "NEEDS_APPROVAL"

    return "SAVED_ONLY"


@scout_router.post("/ingest")
async def ingest_load(
    data: ScoutIngestIn = Body(...),
    background_tasks: BackgroundTasks = None,
    driver: Driver = Depends(_resolve_driver_by_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # ── Build merged metadata ──────────────────────────────────────────────────
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

    # ── Upsert load ────────────────────────────────────────────────────────────
    load = _upsert_load(db, data, merged_metadata, contact_mode, driver.id)

    # ── Broker enrichment ──────────────────────────────────────────────────────
    promote_scout_contact(
        db,
        mc_number=load.mc_number,
        dot_number=data.dot_number,
        contact_info=contact_info,
        contact_mode=contact_mode,
        source_platform=data.source,
    )
    db.commit()

    # ── Broker triage ──────────────────────────────────────────────────────────
    triage = triage_broker_contact(
        db,
        load.mc_number,
        load.id,
        driver.id,
        contact_mode,
    )
    broker_email: str | None = triage.get("email") or contact_info.get("email")

    # ── Match scoring ──────────────────────────────────────────────────────────
    match = compute_match(driver, load, merged_metadata)

    # ── Decide action ──────────────────────────────────────────────────────────
    next_step = _decide_next_step(driver, match, triage, broker_email)

    # ── Identity string for email sender ──────────────────────────────────────
    raw_identity = driver.display_name or "dispatch"
    identity = re.sub(r"[^a-z0-9]", "", raw_identity.lower()) or "dispatch"

    queued_negotiation_id: int | None = None

    if next_step == "AUTO_SENT":
        neg, _ = _get_or_create_negotiation(
            db, load, driver, "Sent", match["score"], match
        )
        queued_negotiation_id = neg.id
        if background_tasks is not None:
            background_tasks.add_task(
                send_negotiation_email,
                broker_email,
                load.ref_id,
                load.origin,
                load.destination,
                identity,
                load.source_platform,
            )
        logger.info(
            "scout_ingest: AUTO_SENT load=%s driver=%s score=%s/4 broker_email=%s",
            load.id, driver.id, match["score"], broker_email,
        )

    elif next_step == "NEEDS_APPROVAL":
        neg, created = _get_or_create_negotiation(
            db, load, driver, "Queued", match["score"], match
        )
        queued_negotiation_id = neg.id
        logger.info(
            "scout_ingest: NEEDS_APPROVAL load=%s driver=%s score=%s/4 neg=%s created=%s",
            load.id, driver.id, match["score"], neg.id, created,
        )

    else:
        logger.info(
            "scout_ingest: %s load=%s driver=%s score=%s/4",
            next_step, load.id, driver.id, match["score"],
        )

    broker_phone = triage.get("phone") or contact_info.get("phone")

    try:
        db.execute(
            text("""
                INSERT INTO public.scout_ingest_log (driver_id, load_id, next_step)
                VALUES (:driver_id, :load_id, :next_step)
            """),
            {"driver_id": driver.id, "load_id": load.id, "next_step": next_step},
        )
        db.commit()
    except Exception as e:
        logger.warning("scout_ingest_log insert failed: %s", e)
        db.rollback()

    return {
        "status": "success",
        "load_id": load.id,
        "next_step": next_step,
        "reason": triage.get("reason"),
        "match_score": match["score"],
        "match_total": match["total"],
        "matched": match["matched"],
        "missed": match["missed"],
        "computed_rpm": match.get("computed_rpm"),
        "queued_negotiation_id": queued_negotiation_id,
        "contact_mode": contact_mode,
        "broker_email": broker_email,
        "broker_phone": broker_phone,
        "standing": triage.get("standing"),
        "identity_used": f"{identity}@{os.getenv('EMAIL_DOMAIN', 'gcdloads.com')}",
    }


@scout_router.get("/parsing-rules")
def get_parsing_rules(
    _driver: Driver = Depends(_resolve_driver_by_key),
) -> dict[str, Any]:
    return load_parsing_rules()
