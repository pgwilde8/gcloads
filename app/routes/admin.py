import os

from fastapi import APIRouter, Depends, Form, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings as core_settings
from app.database import get_db
from app.models.broker import Broker
from app.services.email import send_century_approval_email


router = APIRouter(prefix="/admin", tags=["admin"])


def _admin_authorized(admin_password: str | None, admin_token: str | None) -> bool:
    configured_password = (os.getenv("ADMIN_ENRICH_PASSWORD") or "").strip()
    configured_token = (os.getenv("ADMIN_TOKEN") or "").strip()

    if configured_token and admin_token == configured_token:
        return True
    if configured_password and admin_password == configured_password:
        return True
    return False


def _admin_token_authorized(admin_token: str | None) -> bool:
    expected = (core_settings.ADMIN_TOKEN or "").strip()
    provided = (admin_token or "").strip()
    return bool(expected and provided and provided == expected)


@router.get("/api/broker-lookup/{mc_number}")
async def api_lookup(
    mc_number: str,
    admin_password: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    configured_password = os.getenv("ADMIN_ENRICH_PASSWORD", "")
    if configured_password and admin_password != configured_password:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})

    broker = db.query(Broker).filter(Broker.mc_number == mc_number.strip()).first()
    if not broker:
        return JSONResponse(status_code=404, content={"message": "Not found"})

    return {
        "company_name": broker.company_name,
        "phone": broker.primary_phone,
        "primary_email": broker.primary_email,
        "preferred_method": broker.preferred_contact_method,
        "internal_note": broker.internal_note,
    }


@router.post("/century/approve")
async def approve_century_referral(
    referral_id: int = Form(...),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    if not _admin_token_authorized(x_admin_token):
        return JSONResponse(status_code=403, content={"ok": False, "message": "forbidden"})

    referral = db.execute(
        text(
            """
            SELECT cr.id, cr.driver_id, d.email, d.display_name
            FROM century_referrals cr
            JOIN drivers d ON d.id = cr.driver_id
            WHERE cr.id = :referral_id
            LIMIT 1
            """
        ),
        {"referral_id": referral_id},
    ).mappings().first()

    if not referral:
        return JSONResponse(status_code=404, content={"ok": False, "message": "referral_not_found"})

    db.execute(
        text("UPDATE century_referrals SET status = 'APPROVED' WHERE id = :referral_id"),
        {"referral_id": referral_id},
    )
    db.execute(
        text("UPDATE drivers SET onboarding_status = 'active' WHERE id = :driver_id"),
        {"driver_id": int(referral["driver_id"])},
    )
    db.commit()

    email_sent = send_century_approval_email(
        to_email=str(referral["email"]),
        driver_name=str(referral["display_name"] or "Driver"),
    )

    return {
        "ok": True,
        "referral_id": int(referral["id"]),
        "driver_id": int(referral["driver_id"]),
        "email_sent": bool(email_sent),
    }
