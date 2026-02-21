import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import case, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, check_database_connection, engine, get_db
from app.models.broker import Broker, BrokerEmail
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import BrokerOverride, LoadDocument, Message, Negotiation, ScoutStatus, Transaction
from app.routes.chat import router as chat_router
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.ingest import router as ingest_router
from app.routes.ingest import scout_router as scout_ingest_router
from app.routes.operations import router as operations_router
from app.routes.payments import router as payments_router
from app.routes.public import router as public_router
from app.logic.negotiator import handle_broker_reply
from app.core.config import settings as core_settings
from app.services.email import send_outbound_email, send_quick_reply_email
from app.services.packet_manager import log_packet_snapshot, register_uploaded_packet_document
from app.services.packet_storage import ensure_driver_space, packet_driver_dir, packet_file_paths_for_driver, save_packet_file


class Settings(BaseSettings):
    app_name: str = "gcloads-api"
    app_env: str = "development"
    project_port: int = 8369

    base_url: str = "http://129.212.150.98:8369"
    email_domain: str = "gcdloads.com"

    db_user: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "postgres"
    db_host: str = "db"
    db_port: int = 5432

    database_url: str | None = None
    scout_api_key: str = ""
    admin_enrich_password: str = ""
    session_secret_key: str = Field(default="change-this-session-secret", validation_alias="SECRET_KEY")
    packet_storage_root: str = "/srv/gcd-data/packets"
    packet_max_total_mb: int = 5
    packet_max_file_mb: int = 5

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            "postgresql+psycopg2://"
            f"{self.db_user}:{self.db_password}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
logger = logging.getLogger(__name__)
app_root = Path(__file__).resolve().parent
templates_dir = Path(__file__).resolve().parent / "templates"
app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=str(app_root / "static")), name="static")
templates = Jinja2Templates(directory=str(templates_dir))
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    same_site="lax",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(scout_ingest_router)
app.include_router(operations_router)
app.include_router(payments_router)
app.include_router(public_router)


@app.on_event("startup")
def startup() -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS webwise"))
        connection.execute(text("ALTER TABLE webwise.brokers ADD COLUMN IF NOT EXISTS internal_note TEXT"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS min_cpm DOUBLE PRECISION"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS min_flat_rate DOUBLE PRECISION"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS auto_negotiate BOOLEAN NOT NULL DEFAULT TRUE"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS review_before_send BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS dispatch_handle VARCHAR(20)"))
        connection.execute(
            text(
                """
                UPDATE public.drivers
                SET dispatch_handle = LEFT(
                    COALESCE(NULLIF(REGEXP_REPLACE(LOWER(display_name), '[^a-z0-9]+', '', 'g'), ''),
                             NULLIF(REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)), '[^a-z0-9]+', '', 'g'), ''),
                             'driver'),
                    20
                )
                WHERE dispatch_handle IS NULL
                """
            )
        )
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS referred_by_id INTEGER"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS referral_started_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS referral_expires_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS stripe_default_payment_method_id VARCHAR(255)"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS stripe_payment_status VARCHAR(40) DEFAULT 'UNSET'"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS stripe_action_required BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_drivers_referred_by'
                    ) THEN
                        ALTER TABLE public.drivers
                            ADD CONSTRAINT fk_drivers_referred_by
                            FOREIGN KEY (referred_by_id)
                            REFERENCES public.drivers(id)
                            ON DELETE SET NULL;
                    END IF;
                END $$;
                """
            )
        )
        connection.execute(text("ALTER TABLE public.loads ADD COLUMN IF NOT EXISTS mc_number VARCHAR(20)"))
        connection.execute(text("ALTER TABLE public.loads ADD COLUMN IF NOT EXISTS source_platform VARCHAR(20)"))
        connection.execute(text("ALTER TABLE public.loads ADD COLUMN IF NOT EXISTS metadata JSONB"))
        connection.execute(text("ALTER TABLE public.loads ADD COLUMN IF NOT EXISTS contact_instructions VARCHAR(20)"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS rate_con_path VARCHAR(1024)"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS pending_review_subject VARCHAR(255)"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS pending_review_body TEXT"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS pending_review_action VARCHAR(40)"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS pending_review_price NUMERIC(12,2)"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS factoring_status VARCHAR(20)"))
        connection.execute(text("ALTER TABLE public.negotiations ADD COLUMN IF NOT EXISTS factored_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.driver_documents (
                    id SERIAL PRIMARY KEY,
                    driver_id INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
                    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE CASCADE,
                    doc_type VARCHAR(50) NOT NULL,
                    bucket VARCHAR(255),
                    file_key VARCHAR(1024) NOT NULL,
                    uploaded_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMPTZ,
                    sha256_hash VARCHAR(64),
                    is_active BOOLEAN DEFAULT TRUE
                )
                """
            )
        )
        connection.execute(text("ALTER TABLE public.driver_documents ADD COLUMN IF NOT EXISTS negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE CASCADE"))
        connection.execute(text("ALTER TABLE public.driver_documents ADD COLUMN IF NOT EXISTS bucket VARCHAR(255)"))
        connection.execute(text("ALTER TABLE public.driver_documents ALTER COLUMN file_key TYPE VARCHAR(1024)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.packet_snapshots (
                    id SERIAL PRIMARY KEY,
                    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
                    driver_id INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
                    version_label VARCHAR(20),
                    sent_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    recipient_email VARCHAR(255),
                    metadata JSONB
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.fee_ledger (
                    id SERIAL PRIMARY KEY,
                    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
                    driver_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
                    total_load_value DECIMAL(12,2) NOT NULL,
                    total_fee_collected DECIMAL(10,2) NOT NULL,
                    slice_driver_credits DECIMAL(10,2) NOT NULL,
                    slice_infra_reserve DECIMAL(10,2) NOT NULL,
                    slice_platform_profit DECIMAL(10,2) NOT NULL,
                    slice_treasury DECIMAL(10,2) NOT NULL,
                    referral_bounty_paid DECIMAL(10,2) NOT NULL DEFAULT 0.00,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.referral_earnings (
                    id SERIAL PRIMARY KEY,
                    referrer_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
                    referred_driver_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
                    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    payout_type VARCHAR(20) NOT NULL DEFAULT 'CANDLE',
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.dispatch_fee_payments (
                    id SERIAL PRIMARY KEY,
                    negotiation_id INTEGER NOT NULL REFERENCES public.negotiations(id) ON DELETE CASCADE,
                    driver_id INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
                    stripe_payment_intent_id VARCHAR(255),
                    amount_cents INTEGER NOT NULL,
                    currency VARCHAR(10) NOT NULL DEFAULT 'usd',
                    status VARCHAR(40) NOT NULL DEFAULT 'PENDING',
                    error_message TEXT,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_loads_mc_number ON public.loads (mc_number)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_loads_source_platform ON public.loads (source_platform)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_is_read ON public.messages (is_read)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_drivers_referred_by_id ON public.drivers (referred_by_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_drivers_referral_expires_at ON public.drivers (referral_expires_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_drivers_stripe_customer_id ON public.drivers (stripe_customer_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_driver_documents_driver_active ON public.driver_documents (driver_id, is_active)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_driver_documents_driver_type_active ON public.driver_documents (driver_id, doc_type, is_active)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_driver_documents_driver_neg_type_active ON public.driver_documents (driver_id, negotiation_id, doc_type, is_active)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_packet_negotiation ON public.packet_snapshots (negotiation_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_packet_snapshots_driver_sent_at ON public.packet_snapshots (driver_id, sent_at DESC)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_ledger_negotiation ON public.fee_ledger (negotiation_id) WHERE negotiation_id IS NOT NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_dispatch_fee_payments_negotiation ON public.dispatch_fee_payments (negotiation_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_dispatch_fee_payments_intent ON public.dispatch_fee_payments (stripe_payment_intent_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_referral_earnings_referrer ON public.referral_earnings (referrer_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_referral_earnings_negotiation ON public.referral_earnings (negotiation_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_drivers_dispatch_handle ON public.drivers (dispatch_handle)"))

    logger.info("Startup watermark mode: %s", "ON" if core_settings.WATERMARK_ENABLED else "OFF")
    Base.metadata.create_all(bind=engine)


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("public/home.html", {"request": request})


@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("public/register.html", {"request": request})


def _admin_authorized(password: str | None) -> bool:
    if not settings.admin_enrich_password:
        return True
    return password == settings.admin_enrich_password


def _packet_driver_dir(driver_id: int) -> Path:
    return packet_driver_dir(driver_id, settings.packet_storage_root)


def _packet_files_for_driver(driver_id: int) -> list[Path]:
    return packet_file_paths_for_driver(driver_id, settings.packet_storage_root)


def _derive_dispatch_handle(display_name: str, normalized_email: str) -> str:
    base = (display_name or "").strip().lower()
    handle = "".join(ch for ch in base if ch.isalnum())
    if handle:
        return handle[:20]

    email_local = (normalized_email.split("@", 1)[0] if normalized_email else "").strip().lower()
    email_handle = "".join(ch for ch in email_local if ch.isalnum())
    return (email_handle or "driver")[:20]


@app.post("/register")
async def register_driver(
    email: str = Form(...),
    mc_number: str = Form(...),
    display_name: str = Form(...),
    db: Session = Depends(get_db),
):
    normalized_email = email.strip().lower()
    existing_driver = db.query(Driver).filter(Driver.email == normalized_email).first()
    if existing_driver:
        return RedirectResponse(
            url=f"/drivers/dashboard?email={normalized_email}",
            status_code=303,
        )

    new_driver = Driver(
        email=normalized_email,
        mc_number=mc_number.strip(),
        display_name=display_name.strip() or "driver",
        dispatch_handle=_derive_dispatch_handle(display_name, normalized_email),
        balance=1.0,
    )
    db.add(new_driver)
    db.commit()
    db.refresh(new_driver)
    ensure_driver_space(new_driver.id)

    return RedirectResponse(
        url=f"/drivers/dashboard?email={normalized_email}",
        status_code=303,
    )


@app.get("/admin/enrich")
async def admin_enrich_page(
    request: Request,
    mc_number: str | None = None,
    admin_password: str | None = None,
    saved: int = 0,
    db: Session = Depends(get_db),
):
    broker = None
    primary_email = ""
    direct_phone = ""
    company_name = ""
    internal_note = ""
    preferred_method = "email"
    error = None

    if admin_password and not _admin_authorized(admin_password):
        error = "Invalid admin password."
    elif mc_number:
        broker = db.query(Broker).filter(Broker.mc_number == mc_number.strip()).first()
        if broker:
            primary_email = broker.primary_email or ""
            direct_phone = broker.primary_phone or ""
            company_name = broker.company_name or ""
            internal_note = broker.internal_note or ""
            preferred_method = broker.preferred_contact_method or "email"

    return templates.TemplateResponse(
        "admin/enrich.html",
        {
            "request": request,
            "saved": saved == 1,
            "error": error,
            "broker": broker,
            "mc_number": mc_number,
            "admin_password": admin_password,
            "primary_email": primary_email,
            "direct_phone": direct_phone,
            "company_name": company_name,
            "internal_note": internal_note,
            "preferred_method": preferred_method,
        },
    )


@app.post("/admin/enrich")
async def admin_enrich_save(
    request: Request,
    admin_password: str = Form(""),
    mc_number: str = Form(...),
    company_name: str = Form(""),
    internal_note: str = Form(""),
    primary_email: str = Form(""),
    direct_phone: str = Form(""),
    preferred_method: str = Form("email"),
    db: Session = Depends(get_db),
):
    mc_number = mc_number.strip()
    company_name = company_name.strip()
    internal_note = internal_note.strip()
    primary_email = primary_email.strip().lower()
    direct_phone = direct_phone.strip()
    preferred_method = preferred_method.strip().lower() or "email"

    if not _admin_authorized(admin_password):
        return templates.TemplateResponse(
            "admin/enrich.html",
            {
                "request": request,
                "saved": False,
                "error": "Invalid admin password.",
                "broker": None,
                "mc_number": mc_number,
                "admin_password": admin_password,
                "primary_email": primary_email,
                "direct_phone": direct_phone,
                "company_name": company_name,
                "internal_note": internal_note,
                "preferred_method": preferred_method,
            },
            status_code=401,
        )

    broker = db.query(Broker).filter(Broker.mc_number == mc_number).first()
    if not broker:
        broker = Broker(
            mc_number=mc_number,
            company_name=company_name or f"MC {mc_number}",
            source="manual_admin",
        )
        db.add(broker)
    elif company_name:
        broker.company_name = company_name

    if primary_email:
        broker.primary_email = primary_email
        existing_email = (
            db.query(BrokerEmail)
            .filter(
                BrokerEmail.mc_number == mc_number,
                BrokerEmail.email == primary_email,
            )
            .first()
        )
        if not existing_email:
            db.add(
                BrokerEmail(
                    mc_number=mc_number,
                    email=primary_email,
                    source="manual_admin",
                    confidence=0.95,
                    evidence="admin_enrich",
                )
            )

    if direct_phone:
        broker.primary_phone = direct_phone
    broker.preferred_contact_method = preferred_method
    broker.internal_note = internal_note or None

    db.commit()
    db.refresh(broker)

    return templates.TemplateResponse(
        "admin/enrich.html",
        {
            "request": request,
            "saved": True,
            "error": None,
            "broker": broker,
            "mc_number": mc_number,
            "admin_password": admin_password,
            "primary_email": broker.primary_email or "",
            "direct_phone": broker.primary_phone or "",
            "company_name": broker.company_name or "",
            "internal_note": broker.internal_note or "",
            "preferred_method": broker.preferred_contact_method or "email",
        },
    )

@app.get("/drivers/dashboard-active-loads")
async def get_active_negotiations(
    request: Request,
    email: str | None = None,
    db: Session = Depends(get_db),
):
    selected_driver: Driver | None = None
    if email:
        selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        selected_driver = db.query(Driver).order_by(Driver.created_at.desc()).first()

    cards: list[dict[str, object]] = []
    if selected_driver:
        negotiations = (
            db.query(Negotiation)
            .filter(Negotiation.driver_id == selected_driver.id)
            .order_by(
                case(
                    (Negotiation.status == "RATE_CON_RECEIVED", 0),
                    else_=1,
                ),
                Negotiation.created_at.desc(),
            )
            .limit(5)
            .all()
        )

        for negotiation in negotiations:
            load = db.query(Load).filter(Load.id == negotiation.load_id).first()
            broker_email = (
                db.query(BrokerEmail)
                .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
                .order_by(BrokerEmail.confidence.desc())
                .first()
            )
            cards.append(
                {
                    "negotiation_id": negotiation.id,
                    "status": negotiation.status,
                    "load_ref": load.ref_id if load else "unknown",
                    "origin": load.origin if load else "N/A",
                    "destination": load.destination if load else "N/A",
                    "price": load.price if load else "",
                    "equipment_type": load.equipment_type if load else "",
                    "broker_email": broker_email.email if broker_email else None,
                    "driver_email": selected_driver.email,
                    "has_pending_review": bool(negotiation.pending_review_body),
                    "pending_review_action": negotiation.pending_review_action or "",
                    "pending_review_price": float(negotiation.pending_review_price) if negotiation.pending_review_price is not None else None,
                }
            )

    return templates.TemplateResponse(
        "drivers/partials/active_loads_list.html",
        {"request": request, "negotiation_cards": cards},
    )


@app.post("/api/drivers/update-rates")
async def update_driver_rates(
    min_cpm: float = Form(...),
    min_flat: float = Form(...),
    auto_negotiate: str | None = Form(default=None),
    review_before_send: str | None = Form(default=None),
    email: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    selected_driver: Driver | None = None
    if email:
        selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        selected_driver = db.query(Driver).order_by(Driver.created_at.desc()).first()
    if not selected_driver:
        return HTMLResponse(content="", status_code=404)

    selected_driver.min_cpm = min_cpm
    selected_driver.min_flat_rate = min_flat
    selected_driver.auto_negotiate = auto_negotiate is not None
    selected_driver.review_before_send = review_before_send is not None
    db.commit()

    return HTMLResponse(content="", headers={"HX-Trigger": "rateSettingsUpdated"})


@app.post("/api/negotiations/quick-reply")
async def quick_reply_action(
    action: str = Form(...),
    negotiation_id: int = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}

    load = db.query(Load).filter(Load.id == negotiation.load_id).first()
    if not load:
        return {"status": "error", "message": "load_not_found"}

    broker_email_record = (
        db.query(BrokerEmail)
        .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
        .order_by(BrokerEmail.confidence.desc())
        .first()
    )
    if not broker_email_record or not broker_email_record.email:
        return {"status": "error", "message": "broker_email_not_found"}

    digits = "".join(char for char in (load.price or "") if char.isdigit())
    base_price = int(digits) if digits else 0
    counter_value = base_price + 200 if base_price > 0 else 0
    load_ref = load.ref_id or str(load.id)

    if action == "packet":
        packet_attachments = _packet_files_for_driver(selected_driver.id)
        if not packet_attachments:
            return {"status": "error", "message": "packet_files_missing"}

        watermark_footer_text = (
            f"Sent via Green Candle Dispatch | Driver: {selected_driver.display_name} "
            f"| MC: {selected_driver.mc_number} "
            f"| {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}"
        )

        subject = f"Carrier Packet for Load #{load_ref}"
        body = (
            "Hello,\n\n"
            "Please find our carrier packet attached, including:\n"
            "- MC Authority\n"
            "- Certificate of Insurance\n"
            "- W9\n\n"
            "If you need anything else to finalize this load, please let us know.\n\n"
            "Thank you."
        )
        user_label = "Packet sent"
    elif action == "counter":
        subject = f"Counter Offer for Load #{load_ref}"
        counter_text = f"${counter_value:,}" if counter_value > 0 else "$200 above posted rate"
        body = (
            "Hello,\n\n"
            f"We can cover this load at {counter_text}.\n"
            "Please confirm if this works and we can lock it in immediately.\n\n"
            "Thank you."
        )
        user_label = "Counter sent"
        watermark_footer_text = None
    elif action == "finalize":
        subject = f"Ready to Finalize Load #{load_ref}"
        body = (
            "Hello,\n\n"
            "We are ready to move forward. Please send the rate confirmation and we will finalize now.\n\n"
            "Thank you."
        )
        user_label = "Finalize sent"
        watermark_footer_text = None
    else:
        return {"status": "error", "message": "invalid_action"}

    sent = send_quick_reply_email(
        broker_email=broker_email_record.email,
        load_ref=load_ref,
        driver_handle=selected_driver.dispatch_handle or selected_driver.display_name,
        subject=subject,
        body=body,
        attachment_paths=packet_attachments if action == "packet" else None,
        load_source=load.source_platform,
        negotiation_id=negotiation.id,
        watermark_footer_text=watermark_footer_text,
    )
    if not sent:
        return {"status": "error", "message": "email_send_failed"}

    if action == "packet":
        log_packet_snapshot(
            db,
            negotiation_id=negotiation.id,
            driver_id=selected_driver.id,
            recipient_email=broker_email_record.email,
            attachment_paths=packet_attachments,
            storage_root=settings.packet_storage_root,
        )

    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="Driver",
            body=f"[{action.upper()}] {body}",
            is_read=True,
        )
    )
    if action == "finalize":
        negotiation.status = "Finalizing"
    db.commit()

    return {"status": "ok", "message": user_label}


@app.post("/api/negotiations/manual-mode")
async def set_negotiation_manual_mode(
    negotiation_id: int = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}

    negotiation.status = "Manual"
    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="System",
            body="AI stopped by driver. Negotiation set to manual mode.",
            is_read=True,
        )
    )
    db.commit()

    return {"status": "ok", "message": "manual_mode_enabled"}


@app.post("/api/negotiations/approve-draft")
async def approve_draft_send(
    negotiation_id: int = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}

    if not negotiation.pending_review_subject or not negotiation.pending_review_body:
        return {"status": "error", "message": "no_pending_draft"}

    load = db.query(Load).filter(Load.id == negotiation.load_id).first()
    if not load:
        return {"status": "error", "message": "load_not_found"}

    broker_email_record = (
        db.query(BrokerEmail)
        .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
        .order_by(BrokerEmail.confidence.desc())
        .first()
    )
    if not broker_email_record or not broker_email_record.email:
        return {"status": "error", "message": "broker_email_not_found"}

    load_ref = load.ref_id or str(load.id)
    sent = await send_outbound_email(
        recipient=broker_email_record.email,
        subject=negotiation.pending_review_subject,
        body=negotiation.pending_review_body,
        load_ref=load_ref,
        driver_handle=selected_driver.dispatch_handle or selected_driver.display_name,
        load_source=load.source_platform,
        negotiation_id=negotiation.id,
    )
    if not sent:
        return {"status": "error", "message": "email_send_failed"}

    action = negotiation.pending_review_action or "SEND_COUNTER"
    price_value = float(negotiation.pending_review_price) if negotiation.pending_review_price is not None else None
    if price_value is not None:
        negotiation.current_offer = price_value

    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="System",
            body=f"AI SENT (APPROVED): {action} at ${price_value:,.0f}" if price_value is not None else f"AI SENT (APPROVED): {action}",
            is_read=False,
        )
    )

    negotiation.pending_review_subject = None
    negotiation.pending_review_body = None
    negotiation.pending_review_action = None
    negotiation.pending_review_price = None
    db.commit()

    return {"status": "ok", "message": "draft_approved_and_sent"}


@app.post("/api/test/simulate-broker")
async def simulate_broker_reply(
    negotiation_id: int = Form(...),
    message_text: str = Form(...),
    dry_run: bool = Form(default=True),
    admin_password: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if settings.app_env != "development" and not _admin_authorized(admin_password):
        return {"status": "error", "message": "forbidden"}

    negotiation = db.query(Negotiation).filter(Negotiation.id == negotiation_id).first()
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}

    driver = db.query(Driver).filter(Driver.id == negotiation.driver_id).first()
    if not driver:
        return {"status": "error", "message": "driver_not_found"}

    original_review_before_send = bool(getattr(driver, "review_before_send", False))

    if dry_run:
        driver.review_before_send = True
        db.commit()

    try:
        action_taken = await handle_broker_reply(
            negotiation,
            message_text,
            driver,
            db,
        )
        db.refresh(negotiation)
        latest_message = (
            db.query(Message)
            .filter(Message.negotiation_id == negotiation.id)
            .order_by(Message.id.desc())
            .first()
        )
    finally:
        if dry_run:
            driver.review_before_send = original_review_before_send
            db.commit()

    return {
        "status": "ok",
        "action_taken": action_taken,
        "dry_run": dry_run,
        "negotiation_id": negotiation.id,
        "pending_review_action": negotiation.pending_review_action,
        "pending_review_price": float(negotiation.pending_review_price) if negotiation.pending_review_price is not None else None,
        "latest_message": latest_message.body if latest_message else None,
    }


@app.post("/drivers/upload-packet")
async def upload_packet(
    request: Request,
    email: str = Form(default=""),
    doc_type: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    mc_auth: UploadFile | None = File(default=None),
    coi: UploadFile | None = File(default=None),
    w9: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    selected_driver = None

    session_driver_id = request.session.get("user_id")
    if session_driver_id:
        selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()

    normalized_email = email.strip().lower() if email else ""
    if not selected_driver and normalized_email:
        selected_driver = db.query(Driver).filter(Driver.email == normalized_email).first()
        if selected_driver:
            request.session["user_id"] = selected_driver.id

    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

    upload_map = {
        "mc_auth.pdf": mc_auth,
        "coi.pdf": coi,
        "w9.pdf": w9,
    }
    provided = {name: file for name, file in upload_map.items() if file is not None}

    max_file_bytes = settings.packet_max_file_mb * 1024 * 1024
    max_total_bytes = settings.packet_max_total_mb * 1024 * 1024

    doc_key_map = {
        "mc_auth": "mc_auth.pdf",
        "coi": "coi.pdf",
        "w9": "w9.pdf",
    }

    if doc_type and file is not None:
        doc_key = doc_type.strip().lower()
        canonical_name = doc_key_map.get(doc_key)
        if not canonical_name:
            return {"status": "error", "message": "invalid_doc_type"}

        original_name = (file.filename or "").lower()
        content_type = (file.content_type or "").lower()
        if not original_name.endswith(".pdf") and content_type != "application/pdf":
            return {"status": "error", "message": "invalid_file_type", "file": canonical_name}

        file_bytes = await file.read()
        size_bytes = len(file_bytes)
        if size_bytes == 0:
            return {"status": "error", "message": "empty_file", "file": canonical_name}
        if size_bytes > max_file_bytes:
            return {
                "status": "error",
                "message": "file_too_large",
                "file": canonical_name,
                "max_mb": settings.packet_max_file_mb,
            }

        save_result = save_packet_file(
            selected_driver.id,
            canonical_name,
            file_bytes,
            storage_root=settings.packet_storage_root,
            content_type=content_type or "application/pdf",
        )
        if not save_result["local_saved"] and not save_result["spaces_saved"]:
            return {"status": "error", "message": "storage_write_failed", "file": canonical_name}

        register_uploaded_packet_document(
            db,
            driver_id=selected_driver.id,
            filename=canonical_name,
            file_bytes=file_bytes,
            spaces_saved=bool(save_result["spaces_saved"]),
            storage_root=settings.packet_storage_root,
        )
        db.commit()

        driver_dir = _packet_driver_dir(selected_driver.id)

        if request.headers.get("HX-Request") == "true":
            label_map = {
                "mc_auth": "MC Authority",
                "coi": "Insurance (COI)",
                "w9": "W9 Form",
            }
            label = label_map.get(doc_key, canonical_name)
            html = (
                f'<div id="slot-{doc_key}" class="p-4 bg-green-900/20 border border-green-500/40 rounded-2xl flex items-center justify-between">'
                '<div class="flex items-center gap-4">'
                '<div class="w-10 h-10 rounded-xl bg-green-900/40 flex items-center justify-center text-green-400">'
                '<i class="fas fa-check-circle"></i>'
                '</div>'
                '<div>'
                f'<p class="text-sm font-bold text-green-300">{label}</p>'
                '<p class="text-[10px] text-green-500 uppercase tracking-widest">Uploaded</p>'
                '</div>'
                '</div>'
                '</div>'
            )
            return HTMLResponse(content=html)

        return {
            "status": "ok",
            "driver_id": selected_driver.id,
            "saved_files": [canonical_name],
            "storage_path": str(driver_dir),
            "spaces_saved": save_result["spaces_saved"],
        }

    if not provided:
        return {"status": "error", "message": "no_files_uploaded"}

    staged: dict[str, bytes] = {}
    total_bytes = 0
    for canonical_name, uploaded in provided.items():
        original_name = (uploaded.filename or "").lower()
        content_type = (uploaded.content_type or "").lower()
        if not original_name.endswith(".pdf") and content_type != "application/pdf":
            return {
                "status": "error",
                "message": "invalid_file_type",
                "file": canonical_name,
            }

        file_bytes = await uploaded.read()
        size_bytes = len(file_bytes)
        if size_bytes == 0:
            return {"status": "error", "message": "empty_file", "file": canonical_name}
        if size_bytes > max_file_bytes:
            return {
                "status": "error",
                "message": "file_too_large",
                "file": canonical_name,
                "max_mb": settings.packet_max_file_mb,
            }

        total_bytes += size_bytes
        if total_bytes > max_total_bytes:
            return {
                "status": "error",
                "message": "packet_too_large",
                "max_total_mb": settings.packet_max_total_mb,
            }

        staged[canonical_name] = file_bytes

    driver_dir = _packet_driver_dir(selected_driver.id)
    driver_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    spaces_saved_files: list[str] = []
    for canonical_name, file_bytes in staged.items():
        save_result = save_packet_file(
            selected_driver.id,
            canonical_name,
            file_bytes,
            storage_root=settings.packet_storage_root,
        )
        if not save_result["local_saved"] and not save_result["spaces_saved"]:
            return {"status": "error", "message": "storage_write_failed", "file": canonical_name}

        register_uploaded_packet_document(
            db,
            driver_id=selected_driver.id,
            filename=canonical_name,
            file_bytes=file_bytes,
            spaces_saved=bool(save_result["spaces_saved"]),
            storage_root=settings.packet_storage_root,
        )

        saved_files.append(canonical_name)
        if save_result["spaces_saved"]:
            spaces_saved_files.append(canonical_name)

    db.commit()

    return {
        "status": "ok",
        "driver_id": selected_driver.id,
        "saved_files": saved_files,
        "storage_path": str(driver_dir),
        "spaces_saved_files": spaces_saved_files,
    }


@app.get("/drivers/negotiations/{negotiation_id}/sign")
async def review_rate_con(
    request: Request,
    negotiation_id: int,
    email: str | None = None,
    db: Session = Depends(get_db),
):
    if not email:
        selected_driver = db.query(Driver).order_by(Driver.created_at.desc()).first()
        if not selected_driver:
            return RedirectResponse(url="/drivers/dashboard", status_code=302)
        email = selected_driver.email

    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return RedirectResponse(url="/drivers/dashboard", status_code=302)

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation or not negotiation.rate_con_path:
        return RedirectResponse(url=f"/drivers/dashboard?email={selected_driver.email}", status_code=302)

    return templates.TemplateResponse(
        "drivers/rate_con_sign.html",
        {
            "request": request,
            "negotiation_id": negotiation_id,
            "email": selected_driver.email,
            "viewer_url": f"/drivers/negotiations/{negotiation_id}/view-rate-con?email={selected_driver.email}",
        },
    )


@app.get("/drivers/dashboard")
async def driver_dashboard(
    request: Request,
    email: str | None = None,
    assigned_handle: str | None = None,
    db: Session = Depends(get_db),
):
    selected_driver: Driver | None = None
    if email:
        selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        selected_driver = db.query(Driver).order_by(Driver.created_at.desc()).first()

    balance = float(selected_driver.balance) if selected_driver else 25.0
    display_name = selected_driver.display_name if selected_driver else "scout"
    mc_number = selected_driver.mc_number if selected_driver else "MC-PENDING"

    return templates.TemplateResponse(
        "drivers/dashboard.html",
        {
            "request": request,
            "balance": balance,
            "show_beta_banner": False,
            "is_new_driver": selected_driver is None,
            "trucker": {
                "display_name": display_name,
                "mc_number": mc_number,
            },
            "driver_email": selected_driver.email if selected_driver else "",
            "assigned_handle": assigned_handle,
            "user": selected_driver,
        },
    )


@app.get("/api/notifications/unread-count")
async def get_unread_count(
    email: str | None = None,
    db: Session = Depends(get_db),
):
    selected_driver: Driver | None = None
    if email:
        selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        selected_driver = db.query(Driver).order_by(Driver.created_at.desc()).first()
    if not selected_driver:
        return {"unread_count": 0}

    count = (
        db.query(Message)
        .join(Negotiation, Message.negotiation_id == Negotiation.id)
        .filter(
            Negotiation.driver_id == selected_driver.id,
            Message.is_read.is_(False),
            Message.sender == "Broker",
        )
        .count()
    )

    return {"unread_count": count}


@app.post("/api/notifications/mark-read")
async def mark_notifications_read(
    email: str,
    negotiation_id: int | None = None,
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"updated": 0}

    query = (
        db.query(Message)
        .join(Negotiation, Message.negotiation_id == Negotiation.id)
        .filter(
            Negotiation.driver_id == selected_driver.id,
            Message.sender == "Broker",
            Message.is_read.is_(False),
        )
    )
    if negotiation_id is not None:
        query = query.filter(Message.negotiation_id == negotiation_id)

    updated = query.update({Message.is_read: True}, synchronize_session=False)
    db.commit()
    return {"updated": updated}


@app.get("/drivers/scout-loads")
async def driver_scout_loads(request: Request, db: Session = Depends(get_db)):
    loads = db.query(Load).order_by(Load.created_at.desc()).limit(10).all()
    return templates.TemplateResponse(
        "drivers/scout_loads.html",
        {
            "request": request,
            "balance": 25.0,
            "loads": loads,
        },
    )


@app.get("/drivers/load-board")
async def driver_load_board(request: Request, db: Session = Depends(get_db)):
    loads = db.query(Load).order_by(Load.created_at.desc()).limit(10).all()
    return templates.TemplateResponse(
        "drivers/load_board.html",
        {
            "request": request,
            "balance": 25.0,
            "loads": loads,
        },
    )


@app.get("/drivers/scout-status")
async def get_scout_status(request: Request):
    return templates.TemplateResponse(
        "drivers/partials/scout_status_indicator.html",
        {"request": request, "status": "active"},
    )


@app.get("/drivers/partials/first-mission")
async def first_mission_partial(request: Request):
    return templates.TemplateResponse(
        "drivers/partials/first_mission.html",
        {"request": request},
    )


@app.get("/health")
def health() -> dict[str, str]:
    database = "connected"
    status_value = "healthy"
    try:
        check_database_connection()
    except Exception:
        database = "disconnected"
        status_value = "degraded"

    return {
        "status": status_value,
        "database": database,
        "domain": settings.email_domain,
        "environment": settings.app_env,
        "base_url": settings.base_url,
        "email_domain": settings.email_domain,
    }


@app.get("/heartbeat")
def heartbeat() -> dict[str, str | int]:
    return {
        "service": settings.app_name,
        "status": "alive",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "db_host": settings.db_host,
        "db_port": settings.db_port,
    }
