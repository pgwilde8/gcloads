import os

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.broker import Broker


router = APIRouter(prefix="/admin", tags=["admin"])


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
