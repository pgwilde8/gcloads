import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.driver import Driver
from app.services.packet_storage import ensure_driver_space, list_uploaded_packet_docs


router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

RESERVED_HANDLES = {
    "admin",
    "support",
    "dispatch",
    "root",
    "webmaster",
    "billing",
    "safety",
    "compliance",
    "referrals",
    "century",
    "factoring",
    "greencandle",
    "gcd",
    "scout",
    "bot",
}
MIN_HANDLE_LENGTH = 3
MAX_HANDLE_LENGTH = 20


def slugify_handle(raw_name: str, fallback_email: str = "") -> str:
    normalized = (raw_name or "").strip().lower()
    handle = re.sub(r"[^a-z0-9]", "", normalized)
    if handle:
        return handle[:MAX_HANDLE_LENGTH]

    email_local = (fallback_email.split("@")[0] if fallback_email else "").strip().lower()
    email_handle = re.sub(r"[^a-z0-9]", "", email_local)
    return (email_handle or "driver")[:MAX_HANDLE_LENGTH]


def validate_handle(handle: str) -> str | None:
    if len(handle) < MIN_HANDLE_LENGTH:
        return f"Dispatch handle must be at least {MIN_HANDLE_LENGTH} characters."
    if len(handle) > MAX_HANDLE_LENGTH:
        return f"Dispatch handle must be at most {MAX_HANDLE_LENGTH} characters."
    if not re.fullmatch(r"[a-z0-9]+", handle):
        return "Dispatch handle can only contain lowercase letters and numbers."
    if handle in RESERVED_HANDLES:
        return "That dispatch handle is reserved. Pick a different name."
    return None


def build_unique_handle(db: Session, base_handle: str) -> str:
    candidate = base_handle
    suffix = 1

    while candidate in RESERVED_HANDLES or db.query(Driver.id).filter(
        or_(Driver.dispatch_handle == candidate, Driver.display_name == candidate)
    ).first():
        suffix_value = str(suffix)
        candidate = f"{base_handle[: max(1, MAX_HANDLE_LENGTH - len(suffix_value))]}{suffix_value}"
        suffix += 1

    return candidate


@router.get("/register-trucker")
async def register_trucker_page(request: Request):
    return templates.TemplateResponse("public/register-trucker.html", {"request": request})


@router.get("/onboarding/step3")
async def onboarding_step3(
    request: Request,
    assigned_handle: str | None = None,
    db: Session = Depends(get_db),
):
    driver_id = request.session.get("user_id")
    if not driver_id:
        return RedirectResponse(url="/register-trucker", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/register-trucker", status_code=302)

    uploaded_docs = list_uploaded_packet_docs(selected_driver.id)

    return templates.TemplateResponse(
        "drivers/onboarding_step3.html",
        {
            "request": request,
            "email": selected_driver.email,
            "assigned_handle": assigned_handle or selected_driver.dispatch_handle or selected_driver.display_name,
            "uploaded_docs": uploaded_docs,
        },
    )


@router.post("/register-trucker")
async def register_trucker(
    request: Request,
    display_name: str = Form(...),
    dispatch_handle: str = Form(default=""),
    email: str = Form(...),
    authority_type: str = Form("MC"),
    mc_number: str = Form(""),
    dot_number: str = Form(""),
    db: Session = Depends(get_db),
):
    normalized_email = email.strip().lower()
    human_name = display_name.strip() or "Driver"
    existing_driver = db.query(Driver).filter(Driver.email == normalized_email).first()
    if existing_driver:
        ensure_driver_space(existing_driver.id)
        request.session["user_id"] = existing_driver.id
        return RedirectResponse(url="/onboarding/step3", status_code=302)

    seed_handle = dispatch_handle.strip() or human_name
    base_handle = slugify_handle(seed_handle, normalized_email)
    validation_error = validate_handle(base_handle)
    if validation_error:
        return templates.TemplateResponse(
            "public/register-trucker.html",
            {
                "request": request,
                "error": validation_error,
            },
            status_code=400,
        )

    clean_handle = build_unique_handle(db, base_handle)

    normalized_authority = authority_type.strip().upper()
    authority_value = mc_number.strip() if normalized_authority == "MC" else dot_number.strip()
    if not authority_value:
        return templates.TemplateResponse(
            "public/register-trucker.html",
            {
                "request": request,
                "error": "Provide a valid MC or DOT number.",
            },
            status_code=400,
        )

    new_driver = Driver(
        display_name=human_name,
        dispatch_handle=clean_handle,
        email=normalized_email,
        mc_number=authority_value,
        balance=1.0,
    )
    db.add(new_driver)
    db.commit()
    db.refresh(new_driver)
    ensure_driver_space(new_driver.id)
    request.session["user_id"] = new_driver.id

    return RedirectResponse(
        url="/onboarding/step3",
        status_code=302,
    )