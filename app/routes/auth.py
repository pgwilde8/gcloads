import re
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_safe_base_url_from_request, is_beta_request, settings as core_settings
from app.database import get_db
from app.models.driver import Driver
from app.services.email import send_century_referral_email, send_magic_link_email
from app.services.magic_links import generate_magic_token, hash_magic_token, token_expiry
from app.services.packet_readiness import packet_readiness_for_driver
from app.services.packet_storage import ensure_driver_space


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


def build_unique_handle(db: Session, base_handle: str, *, exclude_driver_id: int | None = None) -> str:
    candidate = base_handle
    suffix = 1

    while True:
        handle_taken_query = db.query(Driver.id).filter(
            or_(Driver.dispatch_handle == candidate, Driver.display_name == candidate)
        )
        if exclude_driver_id is not None:
            handle_taken_query = handle_taken_query.filter(Driver.id != exclude_driver_id)
        handle_taken = handle_taken_query.first()

        if candidate not in RESERVED_HANDLES and not handle_taken:
            break

        suffix_value = str(suffix)
        candidate = f"{base_handle[: max(1, MAX_HANDLE_LENGTH - len(suffix_value))]}{suffix_value}"
        suffix += 1

    return candidate


def _is_profile_complete(driver: Driver | None) -> bool:
    if not driver:
        return False
    return bool((driver.display_name or "").strip() and (driver.mc_number or "").strip())


def _onboarding_redirect_for_driver(driver: Driver) -> str:
    status = (driver.onboarding_status or "needs_profile").strip().lower()
    if status == "pending_century":
        return "/onboarding/pending-century"
    if status == "active" and (driver.factor_type or "").strip().lower() == "existing":
        return "/drivers/dashboard"
    if _is_profile_complete(driver):
        return "/onboarding/factoring"
    return "/register-trucker"


def _require_magic_session(request: Request) -> tuple[int | None, str | None]:
    session_driver_id = request.session.get("user_id")
    pending_email = request.session.get("pending_email")
    return session_driver_id, pending_email


def _is_dev_environment() -> bool:
    return (core_settings.ENV or "").strip().lower() in {"local", "dev", "development"}


def _request_client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client.host if request.client else ""
    return (client or "unknown")[:64]


def _rate_limit_exceeded(db: Session, *, email: str, client_ip: str) -> bool:
    window_minutes = max(int(core_settings.MAGIC_LINK_SEND_WINDOW_MINUTES or 15), 1)
    email_limit = max(int(core_settings.MAGIC_LINK_SEND_EMAIL_LIMIT or 5), 1)
    ip_limit = max(int(core_settings.MAGIC_LINK_SEND_IP_LIMIT or 20), 1)

    email_attempts = db.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM magic_link_send_attempts
            WHERE email = :email
              AND created_at >= NOW() - ((:window_minutes || ' minutes')::interval)
            """
        ),
        {"email": email, "window_minutes": window_minutes},
    ).scalar_one()

    ip_attempts = db.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM magic_link_send_attempts
            WHERE client_ip = :client_ip
              AND created_at >= NOW() - ((:window_minutes || ' minutes')::interval)
            """
        ),
        {"client_ip": client_ip, "window_minutes": window_minutes},
    ).scalar_one()

    return email_attempts >= email_limit or ip_attempts >= ip_limit


def _record_magic_send_attempt(db: Session, *, email: str, client_ip: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO magic_link_send_attempts (email, client_ip)
            VALUES (:email, :client_ip)
            """
        ),
        {"email": email, "client_ip": client_ip},
    )


@router.get("/start")
async def start_page(request: Request):
    return templates.TemplateResponse("public/start.html", {"request": request})


@router.get("/join")
async def join_page(request: Request):
    return templates.TemplateResponse("public/start.html", {"request": request})


@router.post("/auth/magic/send")
async def send_magic_link(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    normalized_email = (email or "").strip().lower()
    if not normalized_email or "@" not in normalized_email:
        return templates.TemplateResponse(
            "public/start.html",
            {
                "request": request,
                "error": "Enter a valid email.",
            },
            status_code=400,
        )

    client_ip = _request_client_ip(request)
    is_rate_limited = _rate_limit_exceeded(db, email=normalized_email, client_ip=client_ip)
    _record_magic_send_attempt(db, email=normalized_email, client_ip=client_ip)

    debug_enabled = request.query_params.get("debug") == "1" and _is_dev_environment()
    if is_rate_limited:
        db.commit()
        notice_message = "If your email is valid, your sign-in link is on the way."
        if "application/json" in (request.headers.get("accept") or "").lower():
            payload = {
                "status": "ok",
                "message": "magic_link_sent",
            }
            return JSONResponse(content=payload, status_code=200)
        return templates.TemplateResponse(
            "public/start.html",
            {
                "request": request,
                "notice": notice_message,
            },
        )

    db.execute(
        text(
            """
            UPDATE magic_link_tokens
            SET used_at = NOW()
            WHERE email = :email
              AND used_at IS NULL
            """
        ),
        {"email": normalized_email},
    )

    token = generate_magic_token()
    token_hash = hash_magic_token(token)
    expires_at = token_expiry(core_settings.MAGIC_LINK_TOKEN_TTL_MINUTES)

    db.execute(
        text(
            """
            INSERT INTO magic_link_tokens (email, token_hash, expires_at)
            VALUES (:email, :token_hash, :expires_at)
            """
        ),
        {
            "email": normalized_email,
            "token_hash": token_hash,
            "expires_at": expires_at,
        },
    )
    db.commit()

    base_url = get_safe_base_url_from_request(
        request,
        fallback_base_url=core_settings.APP_BASE_URL,
        trusted_proxy_ips=core_settings.TRUSTED_PROXY_IPS,
    )
    verify_url = f"{base_url.rstrip('/')}/auth/magic/verify?token={token}"
    send_magic_link_email(normalized_email, verify_url)

    context = {
        "request": request,
        "notice": "If your email is valid, your sign-in link is on the way.",
    }
    if debug_enabled:
        context["debug_verify_url"] = verify_url

    accepts_json = "application/json" in (request.headers.get("accept") or "").lower()
    if accepts_json:
        payload = {
            "status": "ok",
            "message": "magic_link_sent",
        }
        if debug_enabled:
            payload["verify_url"] = verify_url
        return JSONResponse(content=payload, status_code=200)

    return templates.TemplateResponse("public/start.html", context)


@router.get("/auth/magic/verify")
async def verify_magic_link(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    token_hash = hash_magic_token(token)
    token_row = db.execute(
        text(
            """
            SELECT id, email
            FROM magic_link_tokens
            WHERE token_hash = :token_hash
              AND used_at IS NULL
              AND expires_at >= NOW()
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {"token_hash": token_hash},
    ).first()

    if not token_row:
        return templates.TemplateResponse(
            "public/start.html",
            {
                "request": request,
                "error": "This sign-in link is invalid or expired.",
            },
            status_code=400,
        )

    consumed_row = db.execute(
        text(
            """
            UPDATE magic_link_tokens
            SET used_at = NOW()
            WHERE id = :id
              AND used_at IS NULL
              AND expires_at >= NOW()
            RETURNING email
            """
        ),
        {"id": int(token_row.id)},
    ).first()

    if not consumed_row:
        db.rollback()
        return templates.TemplateResponse(
            "public/start.html",
            {
                "request": request,
                "error": "This sign-in link is invalid or expired.",
            },
            status_code=400,
        )

    selected_driver = db.query(Driver).filter(Driver.email == token_row.email).first()
    if selected_driver:
        selected_driver.email_verified_at = datetime.now(timezone.utc)
        db.add(selected_driver)
        db.commit()
        request.session["user_id"] = selected_driver.id
        request.session.pop("pending_email", None)
        return RedirectResponse(url=_onboarding_redirect_for_driver(selected_driver), status_code=302)

    db.commit()
    request.session.pop("user_id", None)
    request.session["pending_email"] = token_row.email
    return RedirectResponse(url="/register-trucker", status_code=302)


@router.get("/register-trucker")
async def register_trucker_page(request: Request, db: Session = Depends(get_db)):
    session_driver_id, pending_email = _require_magic_session(request)
    if not session_driver_id and not pending_email:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = None
    if session_driver_id:
        selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
        if not selected_driver:
            request.session.pop("user_id", None)
            return RedirectResponse(url="/start", status_code=302)
        next_url = _onboarding_redirect_for_driver(selected_driver)
        if next_url != "/register-trucker":
            return RedirectResponse(url=next_url, status_code=302)

    prefill_email = pending_email or (selected_driver.email if selected_driver else "")
    return templates.TemplateResponse(
        "public/register-trucker.html",
        {
            "request": request,
            "prefill_email": prefill_email,
            "driver": selected_driver,
        },
    )


@router.get("/onboarding/step3")
async def onboarding_step3(
    request: Request,
    assigned_handle: str | None = None,
    db: Session = Depends(get_db),
):
    driver_id = request.session.get("user_id")
    if not driver_id:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/start", status_code=302)

    packet_readiness = packet_readiness_for_driver(db, selected_driver.id)
    uploaded_docs = set(packet_readiness.get("uploaded") or [])

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
    carrier_name: str = Form(default=""),
    display_name: str = Form(...),
    dispatch_handle: str = Form(default=""),
    email: str = Form(default=""),
    authority_type: str = Form("MC"),
    mc_number: str = Form(""),
    dot_number: str = Form(""),
    db: Session = Depends(get_db),
):
    session_driver_id, pending_email = _require_magic_session(request)
    if not session_driver_id and not pending_email:
        return RedirectResponse(url="/start", status_code=302)

    normalized_email = (pending_email or email).strip().lower()
    human_name = display_name.strip() or "Driver"

    selected_driver = None
    if session_driver_id:
        selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
        if not selected_driver:
            request.session.pop("user_id", None)
            return RedirectResponse(url="/start", status_code=302)

    if not selected_driver:
        existing_driver = db.query(Driver).filter(Driver.email == normalized_email).first()
        if existing_driver:
            selected_driver = existing_driver
            request.session["user_id"] = existing_driver.id
            request.session.pop("pending_email", None)

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

    clean_handle = build_unique_handle(db, base_handle, exclude_driver_id=selected_driver.id if selected_driver else None)

    normalized_authority = authority_type.strip().upper()
    normalized_mc = mc_number.strip()
    normalized_dot = dot_number.strip()
    authority_value = normalized_mc if normalized_authority == "MC" else normalized_dot
    if not authority_value:
        return templates.TemplateResponse(
            "public/register-trucker.html",
            {
                "request": request,
                "error": "Provide a valid MC or DOT number.",
                "prefill_email": normalized_email,
            },
            status_code=400,
        )

    if selected_driver:
        selected_driver.display_name = human_name
        selected_driver.dispatch_handle = clean_handle
        selected_driver.email = normalized_email
        selected_driver.mc_number = normalized_mc or authority_value
        selected_driver.dot_number = normalized_dot or (authority_value if normalized_authority == "DOT" else None)
        selected_driver.email_verified_at = selected_driver.email_verified_at or datetime.now(timezone.utc)
        selected_driver.onboarding_status = "needs_profile"
        db.add(selected_driver)
        db.commit()
        db.refresh(selected_driver)
        ensure_driver_space(selected_driver.id)
        request.session["user_id"] = selected_driver.id
        request.session.pop("pending_email", None)
    else:
        beta_host = is_beta_request(
            request,
            core_settings.BETA_HOSTS,
            core_settings.TRUSTED_PROXY_IPS,
        )
        driver_kw: dict = {
            "display_name": human_name,
            "dispatch_handle": clean_handle,
            "email": normalized_email,
            "mc_number": normalized_mc or authority_value,
            "dot_number": normalized_dot or (authority_value if normalized_authority == "DOT" else None),
            "onboarding_status": "needs_profile",
            "factor_type": None,
            "email_verified_at": datetime.now(timezone.utc),
        }
        if beta_host:
            driver_kw["billing_mode"] = "beta"
            driver_kw["billing_exempt_reason"] = "beta_host"
            # 60 calendar days. When setting programmatically elsewhere (e.g. admin extend),
            # use max(existing, new_date) to avoid shortening an already-longer exemption.
            driver_kw["billing_exempt_until"] = date.today() + timedelta(days=60)
        new_driver = Driver(**driver_kw)
        db.add(new_driver)
        db.commit()
        db.refresh(new_driver)
        ensure_driver_space(new_driver.id)
        request.session["user_id"] = new_driver.id
        request.session.pop("pending_email", None)

    return RedirectResponse(
        url="/onboarding/factoring",
        status_code=302,
    )


@router.get("/onboarding/factoring")
async def onboarding_factoring_page(
    request: Request,
    db: Session = Depends(get_db),
):
    session_driver_id, _ = _require_magic_session(request)
    if not session_driver_id:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/start", status_code=302)

    if not _is_profile_complete(selected_driver):
        return RedirectResponse(url="/register-trucker", status_code=302)
    if (selected_driver.onboarding_status or "").lower() == "pending_century":
        return RedirectResponse(url="/onboarding/pending-century", status_code=302)
    if (selected_driver.factor_type or "").lower() == "existing" and (selected_driver.onboarding_status or "").lower() == "active":
        return RedirectResponse(url="/drivers/dashboard", status_code=302)

    return templates.TemplateResponse(
        "drivers/onboarding_factoring.html",
        {
            "request": request,
            "driver": selected_driver,
        },
    )


@router.post("/onboarding/factoring")
async def onboarding_factoring_submit(
    request: Request,
    has_factoring: str = Form(...),
    factor_packet_email: str = Form(default=""),
    db: Session = Depends(get_db),
):
    session_driver_id, _ = _require_magic_session(request)
    if not session_driver_id:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/start", status_code=302)

    choice = (has_factoring or "").strip().lower()
    if choice == "yes":
        packet_email = factor_packet_email.strip().lower()
        if not packet_email or "@" not in packet_email:
            return templates.TemplateResponse(
                "drivers/onboarding_factoring.html",
                {
                    "request": request,
                    "driver": selected_driver,
                    "error": "Factor packet email is required when you already have factoring.",
                },
                status_code=400,
            )
        selected_driver.factor_type = "existing"
        selected_driver.factor_packet_email = packet_email
        selected_driver.onboarding_status = "active"
        db.add(selected_driver)
        db.commit()
        return RedirectResponse(url="/drivers/dashboard", status_code=302)

    if choice == "no":
        selected_driver.factor_type = "needs_factor"
        selected_driver.factor_packet_email = None
        selected_driver.onboarding_status = "pending_century"
        db.add(selected_driver)
        db.commit()
        return RedirectResponse(url="/century/apply", status_code=302)

    return templates.TemplateResponse(
        "drivers/onboarding_factoring.html",
        {
            "request": request,
            "driver": selected_driver,
            "error": "Choose Yes or No.",
        },
        status_code=400,
    )


@router.get("/century/apply")
async def century_apply_page(
    request: Request,
    db: Session = Depends(get_db),
):
    session_driver_id, _ = _require_magic_session(request)
    if not session_driver_id:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/start", status_code=302)

    return templates.TemplateResponse(
        "drivers/century_apply.html",
        {
            "request": request,
            "driver": selected_driver,
        },
    )


@router.post("/century/apply")
async def century_apply_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(default=""),
    cell_phone: str = Form(...),
    mc_number: str = Form(...),
    dot_number: str = Form(...),
    number_of_trucks: str = Form(...),
    secondary_phone: str = Form(default=""),
    company_name: str = Form(default=""),
    interested_fuel_card: str | None = Form(default=None),
    estimated_monthly_volume: str = Form(default=""),
    current_factoring_company: str = Form(default=""),
    preferred_funding_speed: str = Form(default=""),
    db: Session = Depends(get_db),
):
    session_driver_id, _ = _require_magic_session(request)
    if not session_driver_id:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/start", status_code=302)

    required_fields = {
        "full_name": full_name,
        "cell_phone": cell_phone,
        "mc_number": mc_number,
        "dot_number": dot_number,
        "number_of_trucks": number_of_trucks,
    }
    missing = [label for label, value in required_fields.items() if not (value or "").strip()]
    if missing:
        return templates.TemplateResponse(
            "drivers/century_apply.html",
            {
                "request": request,
                "driver": selected_driver,
                "error": f"Missing required fields: {', '.join(missing)}",
            },
            status_code=400,
        )

    payload = {
        "full_name": full_name.strip(),
        "email": (selected_driver.email or "").strip().lower(),
        "cell_phone": cell_phone.strip(),
        "secondary_phone": secondary_phone.strip(),
        "company_name": company_name.strip(),
        "mc_number": mc_number.strip(),
        "dot_number": dot_number.strip(),
        "number_of_trucks": number_of_trucks.strip(),
        "interested_fuel_card": bool(interested_fuel_card),
        "estimated_monthly_volume": estimated_monthly_volume.strip(),
        "current_factoring_company": current_factoring_company.strip(),
        "preferred_funding_speed": preferred_funding_speed.strip(),
    }

    db.execute(
        text(
            """
            INSERT INTO century_referrals (driver_id, status, payload)
            VALUES (:driver_id, 'SUBMITTED', CAST(:payload AS JSONB))
            """
        ),
        {
            "driver_id": selected_driver.id,
            "payload": json.dumps(payload),
        },
    )

    selected_driver.factor_type = "needs_factor"
    selected_driver.onboarding_status = "pending_century"
    selected_driver.display_name = selected_driver.display_name or payload["full_name"]
    selected_driver.mc_number = payload["mc_number"]
    selected_driver.dot_number = payload["dot_number"]
    db.add(selected_driver)

    email_sent = send_century_referral_email(
        to_email=core_settings.CENTURY_REFERRAL_TO_EMAIL,
        full_name=payload["full_name"],
        email=payload["email"],
        cell_phone=payload["cell_phone"],
        mc_number=payload["mc_number"],
        dot_number=payload["dot_number"],
        number_of_trucks=payload["number_of_trucks"],
        secondary_phone=payload["secondary_phone"],
        company_name=payload["company_name"],
        interested_fuel_card=payload["interested_fuel_card"],
        estimated_monthly_volume=payload["estimated_monthly_volume"],
        current_factoring_company=payload["current_factoring_company"],
        preferred_funding_speed=payload["preferred_funding_speed"],
    )

    if not email_sent:
        db.rollback()
        return templates.TemplateResponse(
            "drivers/century_apply.html",
            {
                "request": request,
                "driver": selected_driver,
                "error": "Century referral email failed to send. Please retry.",
            },
            status_code=502,
        )

    db.commit()

    return RedirectResponse(url="/onboarding/pending-century", status_code=302)


@router.get("/onboarding/pending-century")
async def onboarding_pending_century(
    request: Request,
    db: Session = Depends(get_db),
):
    session_driver_id, _ = _require_magic_session(request)
    if not session_driver_id:
        return RedirectResponse(url="/start", status_code=302)

    selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        return RedirectResponse(url="/start", status_code=302)

    packet_readiness = packet_readiness_for_driver(db, selected_driver.id)
    uploaded_docs = set(packet_readiness.get("uploaded") or [])
    return templates.TemplateResponse(
        "drivers/onboarding_pending_century.html",
        {
            "request": request,
            "driver": selected_driver,
            "uploaded_docs": uploaded_docs,
            "packet_readiness": packet_readiness,
        },
    )