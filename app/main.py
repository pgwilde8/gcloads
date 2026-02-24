
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import text

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import case, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, check_database_connection, engine, get_db
from app.dependencies.billing_gate import require_payment_method_if_paid
from app.models.broker import Broker, BrokerEmail
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import BrokerOverride, LoadDocument, Message, Negotiation, ScoutStatus, Transaction
from app.routes.chat import router as chat_router
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.ingest import router as ingest_router
from app.routes.ingest import scout_router as scout_ingest_router
from app.routes.notifications import router as notifications_router
from app.routes.operations import router as operations_router
from app.routes.payments import router as payments_router
from app.routes.public import router as public_router
from app.logic.negotiator import handle_broker_reply
from app.core.config import settings as core_settings
from app.services.email import send_outbound_email, send_quick_reply_email
from app.services.document_registry import get_active_documents
from app.services.packet_manager import log_packet_snapshot, register_uploaded_packet_document
from app.services.billing import current_week_ending
from app.services.packet_readiness import packet_readiness_for_driver
from app.services.packet_storage import ensure_driver_space, packet_driver_dir, packet_file_paths_for_driver, save_packet_file
from app.services.stripe_fees import StripeConfigError, create_setup_checkout_session
from app.repositories.billing_repo import (
    billing_bootstrap_for_driver,
    extend_billing_exemption,
    get_driver_stripe_info,
    go_live_clear_exemption,
    is_driver_billing_exempt,
    list_beta_drivers_with_exempt_stats,
    promote_beta_to_paid,
)


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
    session_secret_key: str = Field(default="change-this-session-secret", validation_alias="SESSION_SECRET_KEY")
    session_cookie_domain: str = ""
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
env_lower = (settings.app_env or "").strip().lower()
session_https_only = env_lower in {"production", "prod"}
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    same_site="lax",
    https_only=session_https_only,
    domain=(settings.session_cookie_domain or None),
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
app.include_router(notifications_router)
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
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS dot_number VARCHAR(20)"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS onboarding_status VARCHAR(30)"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS factor_type VARCHAR(30)"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS factor_packet_email VARCHAR(255)"))
        connection.execute(text("ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ"))
        connection.execute(text("UPDATE public.drivers SET onboarding_status = 'active' WHERE onboarding_status IS NULL"))
        connection.execute(text("UPDATE public.drivers SET factor_type = 'existing' WHERE onboarding_status = 'active' AND factor_type IS NULL"))
        connection.execute(text("UPDATE public.drivers SET email_verified_at = COALESCE(email_verified_at, created_at) WHERE email IS NOT NULL"))
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
        connection.execute(text("ALTER TABLE public.driver_documents ADD COLUMN IF NOT EXISTS source_version VARCHAR(64)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.packet_events (
                    id BIGSERIAL PRIMARY KEY,
                    negotiation_id INTEGER REFERENCES negotiations(id) ON DELETE SET NULL,
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    event_type VARCHAR(64) NOT NULL,
                    doc_type VARCHAR(64) NOT NULL,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    meta_json JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.outbound_messages (
                    id BIGSERIAL PRIMARY KEY,
                    negotiation_id INTEGER REFERENCES negotiations(id) ON DELETE SET NULL,
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    channel VARCHAR(20) NOT NULL DEFAULT 'email',
                    recipient VARCHAR(255) NOT NULL,
                    subject VARCHAR(512) NOT NULL,
                    attachment_doc_types JSONB NOT NULL DEFAULT '[]'::jsonb,
                    status VARCHAR(20) NOT NULL,
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
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
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.magic_link_tokens (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.century_referrals (
                    id SERIAL PRIMARY KEY,
                    driver_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'SUBMITTED',
                    payload JSONB NOT NULL,
                    submitted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
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
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_email ON public.magic_link_tokens (email, created_at DESC)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_hash ON public.magic_link_tokens (token_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_century_referrals_driver ON public.century_referrals (driver_id, submitted_at DESC)"))

    logger.info("Startup watermark mode: %s", "ON" if core_settings.WATERMARK_ENABLED else "OFF")
    Base.metadata.create_all(bind=engine)


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("public/home.html", {"request": request})


@app.get("/beta")
@app.get("/drivers_beta")
async def drivers_beta_page(request: Request):
    return templates.TemplateResponse("public/drivers_beta.html", {"request": request})


@app.get("/register")
async def register_page(request: Request):
    return RedirectResponse(url="/start", status_code=302)


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


def _preferred_dispatch_handle(driver: Driver | None) -> str:
    if not driver:
        return "scout"

    existing = (getattr(driver, "dispatch_handle", None) or "").strip().lower()
    if existing:
        return "".join(ch for ch in existing if ch.isalnum())[:20] or "scout"

    return _derive_dispatch_handle(driver.display_name or "", driver.email or "")


def _onboarding_gate_redirect(driver: Driver | None) -> str | None:
    if not driver:
        return "/start"

    onboarding_status = (driver.onboarding_status or "needs_profile").strip().lower()
    factor_type = (driver.factor_type or "").strip().lower()

    if onboarding_status == "pending_century":
        return "/onboarding/pending-century"
    if onboarding_status == "needs_profile":
        return "/register-trucker"
    if factor_type not in {"existing", "needs_factor"}:
        return "/onboarding/factoring"
    if onboarding_status != "active":
        return "/onboarding/factoring"
    return None


def _session_driver(request: Request, db: Session) -> Driver | None:
    session_driver_id = request.session.get("user_id")
    if not session_driver_id:
        return None
    return db.query(Driver).filter(Driver.id == session_driver_id).first()


@app.post("/register")
async def register_driver(
    email: str = Form(...),
    mc_number: str = Form(...),
    display_name: str = Form(...),
    db: Session = Depends(get_db),
):
    return RedirectResponse(url="/start", status_code=303)


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


@app.get("/admin/accounting")
async def admin_accounting_page(
    request: Request,
    admin_password: str | None = None,
    week_ending: str | None = None,
    db: Session = Depends(get_db),
):
    # Planning-only page: shows weekly totals and target allocation guide.
    # Does not move funds, write to DB, or perform any financial transactions.
    if not admin_password or not _admin_authorized(admin_password):
        return templates.TemplateResponse(
            "admin/accounting.html",
            {
                "request": request,
                "authorized": False,
                "admin_password": admin_password or "",
                "error": "Admin password required." if admin_password else None,
                "week_start": None,
                "week_ending": None,
                "total_fees_collected": 0,
                "referral_bounty_paid": 0,
                "net_revenue": 0,
                "infra_target_pct": 30,
                "marketing_target_pct": 15,
                "reserve_target_pct": 15,
                "platform_target_pct": 40,
            },
            status_code=401,
        )

    we: date
    if week_ending:
        try:
            parsed = date.fromisoformat(week_ending)
            if parsed.weekday() != 4:
                we = current_week_ending()
            else:
                we = parsed
        except ValueError:
            we = current_week_ending()
    else:
        we = current_week_ending()

    week_start = we - timedelta(days=6)

    row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(total_fee_collected), 0)::numeric AS total_fees_collected,
                COALESCE(SUM(referral_bounty_paid), 0)::numeric AS referral_bounty_paid
            FROM fee_ledger
            WHERE created_at::date >= :week_start
              AND created_at < CAST(:week_ending AS date) + INTERVAL '1 day'
        """),
        {"week_start": week_start, "week_ending": we},
    ).mappings().first()

    total = float(row["total_fees_collected"] or 0)
    referral = float(row["referral_bounty_paid"] or 0)
    net_revenue = total - referral

    infra_pct = 30
    marketing_pct = 15
    reserve_pct = 15
    platform_pct = 40

    prev_week = we - timedelta(days=7)
    next_week_val = we + timedelta(days=7)
    next_week = next_week_val if next_week_val <= current_week_ending() else None

    return templates.TemplateResponse(
        "admin/accounting.html",
        {
            "request": request,
            "authorized": True,
            "admin_password": admin_password,
            "error": None,
            "week_start": week_start,
            "week_ending": we,
            "total_fees_collected": total,
            "referral_bounty_paid": referral,
            "net_revenue": net_revenue,
            "infra_target_pct": infra_pct,
            "marketing_target_pct": marketing_pct,
            "reserve_target_pct": reserve_pct,
            "platform_target_pct": platform_pct,
            "prev_week": prev_week,
            "next_week": next_week,
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


@app.get("/admin/beta")
async def admin_beta_page(
    request: Request,
    admin_password: str | None = None,
    promoted: int = 0,
    extended: int = 0,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if not admin_password or not _admin_authorized(admin_password):
        return templates.TemplateResponse(
            "admin/beta.html",
            {
                "request": request,
                "authorized": False,
                "admin_password": admin_password or "",
                "error": "Admin password required." if admin_password else None,
                "drivers": [],
            },
            status_code=401,
        )

    drivers = list_beta_drivers_with_exempt_stats(db)

    return templates.TemplateResponse(
        "admin/beta.html",
        {
            "request": request,
            "authorized": True,
            "admin_password": admin_password,
            "error": error,
            "drivers": drivers,
            "promoted": promoted == 1,
            "extended": extended == 1,
        },
    )


@app.post("/admin/beta/promote")
async def admin_beta_promote(
    request: Request,
    admin_password: str = Form(""),
    driver_id: int = Form(...),
    db: Session = Depends(get_db),
):
    if not _admin_authorized(admin_password):
        return RedirectResponse(
            url=f"/admin/beta?error=Unauthorized",
            status_code=303,
        )

    ok = promote_beta_to_paid(db, driver_id)
    db.commit()

    if ok:
        return RedirectResponse(
            url=f"/admin/beta?admin_password={admin_password}&promoted=1",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/admin/beta?admin_password={admin_password}&error=Driver+not+found+or+not+beta",
        status_code=303,
    )


@app.post("/admin/beta/extend")
async def admin_beta_extend(
    request: Request,
    admin_password: str = Form(""),
    driver_id: int = Form(...),
    new_exempt_until: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    if not _admin_authorized(admin_password):
        return RedirectResponse(
            url=f"/admin/beta?error=Unauthorized",
            status_code=303,
        )

    try:
        parsed = date.fromisoformat(new_exempt_until.strip())
    except ValueError:
        return RedirectResponse(
            url=f"/admin/beta?admin_password={admin_password}&error=Invalid+date+format",
            status_code=303,
        )

    if parsed < date.today():
        return RedirectResponse(
            url=f"/admin/beta?admin_password={admin_password}&error=Extend+date+must+be+today+or+later",
            status_code=303,
        )

    ok = extend_billing_exemption(db, driver_id, parsed, reason.strip() or None)
    db.commit()

    if ok:
        return RedirectResponse(
            url=f"/admin/beta?admin_password={admin_password}&extended=1",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/admin/beta?admin_password={admin_password}&error=Driver+not+found",
        status_code=303,
    )


@app.get("/drivers/dashboard-active-loads")
async def get_active_negotiations(
    request: Request,
    limit: int = 5,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    limit = min(max(1, limit), 50)

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
            .limit(limit)
            .all()
        )

        # Fetch latest call log per negotiation in one query (avoids N+1)
        neg_ids = [n.id for n in negotiations]
        call_log_by_neg: dict[int, dict] = {}
        if neg_ids:
            cl_rows = db.execute(
                text("""
                    SELECT DISTINCT ON (negotiation_id)
                        negotiation_id,
                        outcome  AS last_call_outcome,
                        rate     AS last_call_rate,
                        created_at AS last_call_at,
                        next_follow_up_at
                    FROM public.call_logs
                    WHERE driver_id = :driver_id
                      AND negotiation_id = ANY(:neg_ids)
                    ORDER BY negotiation_id, created_at DESC, id DESC
                """),
                {"driver_id": selected_driver.id, "neg_ids": neg_ids},
            ).mappings().all()
            for cl in cl_rows:
                call_log_by_neg[cl["negotiation_id"]] = dict(cl)

        # Fetch broker phone/contact-preference per mc_number in one query (avoids N+1)
        mc_numbers = list({n.broker_mc_number for n in negotiations if n.broker_mc_number})
        broker_info_by_mc: dict[str, dict] = {}
        if mc_numbers:
            b_rows = db.execute(
                text("""
                    SELECT mc_number,
                           primary_phone            AS broker_phone,
                           preferred_contact_method AS broker_contact_preference
                    FROM webwise.brokers
                    WHERE mc_number = ANY(:mc_numbers)
                """),
                {"mc_numbers": mc_numbers},
            ).mappings().all()
            for b in b_rows:
                broker_info_by_mc[b["mc_number"]] = dict(b)

        # Global doc types are the same for every card — fetch once
        global_docs = get_active_documents(
            db,
            driver_id=selected_driver.id,
            negotiation_id=None,
            doc_types=["W9", "INSURANCE", "AUTHORITY"],
        )
        global_doc_types = {str(doc.get("doc_type") or "") for doc in global_docs}

        for negotiation in negotiations:
            load = db.query(Load).filter(Load.id == negotiation.load_id).first()
            broker_email = (
                db.query(BrokerEmail)
                .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
                .order_by(BrokerEmail.confidence.desc())
                .first()
            )
            negotiation_docs = get_active_documents(
                db,
                driver_id=selected_driver.id,
                negotiation_id=negotiation.id,
                doc_types=["BOL_RAW", "BOL_PDF", "BOL_PACKET", "NEGOTIATION_PACKET", "RATECON"],
            )
            doc_types = {str(doc.get("doc_type") or "") for doc in negotiation_docs}

            cl = call_log_by_neg.get(negotiation.id, {})
            bi = broker_info_by_mc.get(negotiation.broker_mc_number or "", {})

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
                    "broker_phone": bi.get("broker_phone"),
                    "broker_contact_preference": bi.get("broker_contact_preference"),
                    "is_call_required": bi.get("broker_contact_preference") == "call",
                    "driver_email": selected_driver.email,
                    "has_bol_raw": "BOL_RAW" in doc_types or "BOL_PDF" in doc_types,
                    "has_bol_packet": "BOL_PACKET" in doc_types,
                    "has_full_packet": "NEGOTIATION_PACKET" in doc_types,
                    "has_ratecon": "RATECON" in doc_types,
                    "has_w9": "W9" in global_doc_types,
                    "has_coi": "INSURANCE" in global_doc_types,
                    "has_authority": "AUTHORITY" in global_doc_types,
                    "has_pending_review": bool(negotiation.pending_review_body),
                    "pending_review_action": negotiation.pending_review_action or "",
                    "pending_review_price": float(negotiation.pending_review_price) if negotiation.pending_review_price is not None else None,
                    # call log fields
                    "last_call_outcome": cl.get("last_call_outcome"),
                    "last_call_rate": float(cl["last_call_rate"]) if cl.get("last_call_rate") is not None else None,
                    "last_call_at": cl.get("last_call_at"),
                    "next_follow_up_at": cl.get("next_follow_up_at"),
                }
            )

    return templates.TemplateResponse(
        "drivers/partials/active_loads_list.html",
        {"request": request, "negotiation_cards": cards},
    )


@app.post("/api/drivers/update-rates")
async def update_driver_rates(
    request: Request,
    min_cpm: float = Form(...),
    min_flat: float = Form(...),
    auto_negotiate: str | None = Form(default=None),
    review_before_send: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
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
    request: Request,
    action: str = Form(...),
    negotiation_id: int = Form(...),
    db: Session = Depends(get_db),
    selected_driver: Driver = Depends(require_payment_method_if_paid),
):

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
        readiness = packet_readiness_for_driver(db, selected_driver.id)
        if not readiness.get("ready"):
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error",
                    "message": "packet_readiness_required",
                    "banner": "Upload W-9, COI, and MC Authority to book loads.",
                    "redirect_url": "/onboarding/step3",
                    "missing_docs": readiness.get("missing_labels") or [],
                },
            )

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
    request: Request,
    negotiation_id: int = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

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
    request: Request,
    negotiation_id: int = Form(...),
    db: Session = Depends(get_db),
    selected_driver: Driver = Depends(require_payment_method_if_paid),
):

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
    doc_type: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    mc_auth: UploadFile | None = File(default=None),
    coi: UploadFile | None = File(default=None),
    w9: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    session_driver_id = request.session.get("user_id")
    if not session_driver_id:
        if request.headers.get("HX-Request") == "true":
            return RedirectResponse(url="/start", status_code=302)
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    selected_driver = db.query(Driver).filter(Driver.id == session_driver_id).first()
    if not selected_driver:
        request.session.pop("user_id", None)
        if request.headers.get("HX-Request") == "true":
            return RedirectResponse(url="/start", status_code=302)
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

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
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return RedirectResponse(url="/start", status_code=302)

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation or not negotiation.rate_con_path:
        return RedirectResponse(url="/drivers/dashboard", status_code=302)

    return templates.TemplateResponse(
        "drivers/rate_con_sign.html",
        {
            "request": request,
            "negotiation_id": negotiation_id,
            "viewer_url": f"/drivers/negotiations/{negotiation_id}/view-rate-con",
        },
    )


@app.get("/drivers/dashboard")
async def driver_dashboard(
    request: Request,
    assigned_handle: str | None = None,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return RedirectResponse(url="/start", status_code=302)

    gate_redirect = _onboarding_gate_redirect(selected_driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)

    display_name = selected_driver.display_name if selected_driver else "scout"
    mc_number = selected_driver.mc_number if selected_driver else "MC-PENDING"
    dispatch_handle = _preferred_dispatch_handle(selected_driver)
    packet_readiness = packet_readiness_for_driver(db, selected_driver.id) if selected_driver else {
        "docs": [],
        "uploaded_count": 0,
        "required_count": 3,
        "is_ready": False,
        "ready": False,
        "uploaded": [],
        "missing": ["w9", "coi", "mc_auth"],
        "missing_labels": ["W-9", "COI", "MC Auth"],
    }

    scout_activity: list = []
    if selected_driver:
        try:
            rows = db.execute(
                text("""
                    SELECT l.id, l.ref_id, l.origin, l.destination, l.price, s.next_step, s.created_at
                    FROM public.scout_ingest_log s
                    JOIN public.loads l ON l.id = s.load_id
                    WHERE s.driver_id = :driver_id
                    ORDER BY s.created_at DESC
                    LIMIT 5
                """),
                {"driver_id": selected_driver.id},
            ).mappings().all()
            scout_activity = [dict(r) for r in rows]
        except Exception:
            pass

    billing_bootstrap = billing_bootstrap_for_driver(db, selected_driver.id) if selected_driver else {}
    show_beta_banner = (
        (billing_bootstrap.get("billing_mode") or "").lower() == "beta"
        or billing_bootstrap.get("is_currently_billing_exempt", False)
    )

    return templates.TemplateResponse(
        "drivers/dashboard.html",
        {
            "request": request,
            "show_beta_banner": show_beta_banner,
            "billing_bootstrap": billing_bootstrap,
            "is_new_driver": selected_driver is None,
            "trucker": {
                "display_name": display_name,
                "dispatch_handle": dispatch_handle,
                "mc_number": mc_number,
            },
            "driver_email": selected_driver.email if selected_driver else "",
            "assigned_handle": assigned_handle or dispatch_handle,
            "packet_readiness": packet_readiness,
            "user": selected_driver,
            "scout_activity": scout_activity,
        },
    )


@app.get("/drivers/negotiations")
async def driver_negotiations(
    request: Request,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return RedirectResponse(url="/start", status_code=302)

    gate_redirect = _onboarding_gate_redirect(selected_driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)

    display_name = selected_driver.display_name if selected_driver else "scout"
    mc_number = selected_driver.mc_number if selected_driver else "MC-PENDING"
    dispatch_handle = _preferred_dispatch_handle(selected_driver)

    return templates.TemplateResponse(
        "drivers/negotiations.html",
        {
            "request": request,
            "trucker": {
                "display_name": display_name,
                "dispatch_handle": dispatch_handle,
                "mc_number": mc_number,
            },
        },
    )


@app.get("/drivers/uploads")
async def driver_uploads_page(
    request: Request,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return RedirectResponse(url="/start", status_code=302)

    gate_redirect = _onboarding_gate_redirect(selected_driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)

    billing_bootstrap = billing_bootstrap_for_driver(db, selected_driver.id) if selected_driver else {}
    return templates.TemplateResponse(
        "drivers/driver_uploads.html",
        {
            "request": request,
            "billing_bootstrap": billing_bootstrap,
            "user": selected_driver,
        },
    )


@app.get("/drivers/gcdtraining")
async def driver_gcd_training_page(
    request: Request,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return RedirectResponse(url="/start", status_code=302)

    return templates.TemplateResponse(
        "drivers/gcdtraining.html",
        {
            "request": request,
            "user": selected_driver,
            "driver_email": selected_driver.email if selected_driver else "",
        },
    )


@app.get("/api/drivers/billing-bootstrap")
async def get_billing_bootstrap(
    request: Request,
    db: Session = Depends(get_db),
):
    """JSON bootstrap for frontend: billing_mode, billing_exempt_until, is_currently_billing_exempt, has_payment_method."""
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"message": "auth_required"})

    bootstrap = billing_bootstrap_for_driver(db, selected_driver.id)
    # Return only fields needed by frontend; exclude billing_exempt_reason (internal)
    out = {
        "billing_mode": bootstrap.get("billing_mode"),
        "billing_exempt_until": None,
        "is_currently_billing_exempt": bootstrap.get("is_currently_billing_exempt"),
        "has_payment_method": bootstrap.get("has_payment_method"),
    }
    exempt_until = bootstrap.get("billing_exempt_until")
    if exempt_until:
        out["billing_exempt_until"] = exempt_until.isoformat() if hasattr(exempt_until, "isoformat") else str(exempt_until)
    return out


@app.post("/api/drivers/go-live")
async def go_live(
    request: Request,
    db: Session = Depends(get_db),
):
    """Driver-initiated: clear exemption, become paid. Requires payment method on file. Idempotent."""
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"message": "auth_required"})

    driver_info = get_driver_stripe_info(db, selected_driver.id)
    if not driver_info:
        return JSONResponse(status_code=403, content={"message": "Driver not found"})

    has_pm = bool(
        driver_info.get("stripe_customer_id") and driver_info.get("stripe_default_payment_method_id")
    )
    if not has_pm:
        return JSONResponse(
            status_code=402,
            content={
                "message": "Add a payment method before going live.",
                "code": "payment_method_required",
                "stripe_setup_required": True,
            },
        )

    today = date.today()
    currently_exempt = is_driver_billing_exempt(driver_info, today)
    if driver_info.get("billing_mode") == "paid" and not currently_exempt:
        return {
            "status": "ok",
            "billing_mode": "paid",
            "billing_exempt_until": None,
        }

    updated = go_live_clear_exemption(db, selected_driver.id)
    db.commit()

    return {
        "status": "ok",
        "billing_mode": "paid",
        "billing_exempt_until": None,
        "message": "You're live. Billing will begin on the next weekly run.",
    }


@app.get("/api/notifications/unread-count")
async def get_unread_count(
    request: Request,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
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
    request: Request,
    negotiation_id: int | None = None,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"updated": 0, "message": "auth_required"})

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
async def driver_scout_loads(request: Request, tab: str = "loads", db: Session = Depends(get_db)):
    from sqlalchemy import text as _text
    selected_driver = _session_driver(request, db)
    gate_redirect = _onboarding_gate_redirect(selected_driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)

    # ── Queued negotiations (approval queue) ──────────────────────────────────
    queued_items: list = []
    if selected_driver:
        queued_rows = (
            db.query(Negotiation, Load)
            .join(Load, Load.id == Negotiation.load_id)
            .filter(
                Negotiation.driver_id == selected_driver.id,
                Negotiation.status == "Queued",
            )
            .order_by(Negotiation.created_at.desc())
            .limit(50)
            .all()
        )
        for neg, load in queued_rows:
            queued_items.append({
                "negotiation_id": neg.id,
                "match_score": neg.match_score,
                "match_details": neg.match_details or {},
                "load": load,
                "created_at": neg.created_at,
            })

    # ── Filtered loads (all ingested loads tab) ───────────────────────────────
    filters = ["1=1"]
    params: dict = {}

    if selected_driver:
        if selected_driver.preferred_origin_region:
            filters.append("lower(origin) LIKE lower(:origin_filter)")
            params["origin_filter"] = f"%{selected_driver.preferred_origin_region.split(',')[0].strip()}%"
        if selected_driver.preferred_destination_region:
            filters.append("lower(destination) LIKE lower(:dest_filter)")
            params["dest_filter"] = f"%{selected_driver.preferred_destination_region.split(',')[0].strip()}%"

    where = " AND ".join(filters)
    loads = db.execute(
        _text(f"SELECT * FROM public.loads WHERE {where} ORDER BY created_at DESC LIMIT 25"),
        params,
    ).mappings().all()

    profile_complete = bool(
        selected_driver and (
            selected_driver.preferred_origin_region
            or selected_driver.preferred_destination_region
        )
    )

    return templates.TemplateResponse(
        "drivers/scout_loads.html",
        {
            "request": request,
            "loads": loads,
            "queued_items": queued_items,
            "queued_count": len(queued_items),
            "driver": selected_driver,
            "profile_complete": profile_complete,
            "active_tab": tab,
        },
    )


@app.get("/drivers/load-board")
async def driver_load_board(request: Request, db: Session = Depends(get_db)):
    selected_driver = _session_driver(request, db)
    gate_redirect = _onboarding_gate_redirect(selected_driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)

    loads = db.query(Load).order_by(Load.created_at.desc()).limit(10).all()
    return templates.TemplateResponse(
        "drivers/load_board.html",
        {
            "request": request,
            "loads": loads,
        },
    )


@app.get("/drivers/scout-status")
async def get_scout_status(request: Request, db: Session = Depends(get_db)):
    driver = _session_driver(request, db)
    profile_complete = bool(
        driver and (
            driver.preferred_origin_region
            or driver.preferred_destination_region
        )
    )
    queued_count = 0
    if driver:
        queued_count = (
            db.query(Negotiation)
            .filter(Negotiation.driver_id == driver.id, Negotiation.status == "Queued")
            .count()
        )
    return templates.TemplateResponse(
        "drivers/partials/scout_status_indicator.html",
        {
            "request": request,
            "status": "active" if (driver and driver.scout_active) else "offline",
            "profile_complete": profile_complete,
            "driver": driver,
            "queued_count": queued_count,
        },
    )


@app.get("/drivers/add-payment")
async def driver_add_payment_get(request: Request, db: Session = Depends(get_db)):
    """Page to add a payment method. Redirects unauthenticated to start."""
    driver = _session_driver(request, db)
    if not driver:
        return RedirectResponse(url="/start", status_code=302)
    gate_redirect = _onboarding_gate_redirect(driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)
    return templates.TemplateResponse(
        "drivers/add_payment.html",
        {"request": request, "driver": driver},
    )


@app.post("/drivers/add-payment")
async def driver_add_payment_post(request: Request, db: Session = Depends(get_db)):
    """Create Stripe checkout session and redirect to add card."""
    driver = _session_driver(request, db)
    if not driver or not driver.email:
        return RedirectResponse(url="/start", status_code=302)
    base = str(request.base_url).rstrip("/")
    dashboard_url = f"{base}/drivers/dashboard"
    try:
        result = create_setup_checkout_session(
            db=db,
            driver_email=driver.email,
            success_url=dashboard_url,
            cancel_url=dashboard_url,
        )
    except StripeConfigError:
        return templates.TemplateResponse(
            "drivers/add_payment.html",
            {"request": request, "driver": driver, "error": "Payment setup is not configured."},
            status_code=503,
        )
    except ValueError as e:
        return templates.TemplateResponse(
            "drivers/add_payment.html",
            {"request": request, "driver": driver, "error": str(e)},
            status_code=404,
        )
    checkout_url = result.get("checkout_url") if result else None
    if not checkout_url:
        return templates.TemplateResponse(
            "drivers/add_payment.html",
            {"request": request, "driver": driver, "error": "Could not start checkout."},
            status_code=500,
        )
    return RedirectResponse(url=checkout_url, status_code=303)


@app.get("/drivers/scout-setup")
async def driver_scout_setup_get(request: Request, db: Session = Depends(get_db)):
    driver = _session_driver(request, db)
    gate_redirect = _onboarding_gate_redirect(driver)
    if gate_redirect:
        return RedirectResponse(url=gate_redirect, status_code=302)

    return templates.TemplateResponse(
        "drivers/scout_setup.html",
        {
            "request": request,
            "driver": driver,
            "scout_api_key": driver.scout_api_key or "",
            "saved": False,
        },
    )


@app.post("/drivers/scout-setup")
async def driver_scout_setup_post(request: Request, db: Session = Depends(get_db)):
    driver = _session_driver(request, db)
    if not driver:
        return RedirectResponse(url="/drivers/dashboard", status_code=302)

    from sqlalchemy import text as _text
    db.execute(
        _text("""
            UPDATE public.drivers SET
                preferred_origin_region      = :origin,
                preferred_destination_region = :destination,
                preferred_equipment_type     = :equipment_type,
                scout_active                 = :scout_active,
                auto_negotiate               = :auto_negotiate,
                auto_send_on_perfect_match   = :auto_send_on_perfect_match,
                min_cpm                      = :min_cpm,
                min_flat_rate                = :min_flat_rate,
                updated_at                   = CURRENT_TIMESTAMP
            WHERE id = :driver_id
        """),
        {
            "origin":                    (form.get("preferred_origin_region") or "").strip() or None,
            "destination":               (form.get("preferred_destination_region") or "").strip() or None,
            "equipment_type":            (form.get("preferred_equipment_type") or "").strip() or None,
            "scout_active":              form.get("scout_active") == "on",
            "auto_negotiate":            form.get("auto_negotiate") == "on",
            "auto_send_on_perfect_match": form.get("auto_send_on_perfect_match") == "on",
            "min_cpm":                   float(form.get("min_cpm") or 0) or None,
            "min_flat_rate":             float(form.get("min_flat_rate") or 0) or None,
            "driver_id":                 driver.id,
        },
    )
    db.commit()
    db.refresh(driver)

    return templates.TemplateResponse(
        "drivers/scout_setup.html",
        {
            "request": request,
            "driver": driver,
            "scout_api_key": driver.scout_api_key or "",
            "saved": True,
        },
    )


@app.post("/api/drivers/regenerate-scout-key")
async def regenerate_scout_key(request: Request, db: Session = Depends(get_db)):
    driver = _session_driver(request, db)
    if not driver:
        raise HTTPException(status_code=401, detail="not_authenticated")
    from sqlalchemy import text as _text
    import secrets
    new_key = secrets.token_hex(32)
    db.execute(
        _text("UPDATE public.drivers SET scout_api_key = :key WHERE id = :id"),
        {"key": new_key, "id": driver.id},
    )
    db.commit()
    return {"scout_api_key": new_key}


@app.post("/drivers/negotiations/{negotiation_id}/approve")
async def approve_queued_negotiation(
    negotiation_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Driver approves a Queued negotiation — sends the email and sets status=Sent."""
    from app.services.broker_intelligence import triage_broker_contact
    import re as _re

    driver = _session_driver(request, db)
    if not driver:
        raise HTTPException(status_code=401, detail="not_authenticated")

    neg = (
        db.query(Negotiation)
        .filter(Negotiation.id == negotiation_id, Negotiation.driver_id == driver.id)
        .first()
    )
    if not neg:
        raise HTTPException(status_code=404, detail="negotiation_not_found")
    if neg.status != "Queued":
        raise HTTPException(status_code=409, detail=f"negotiation_status_is_{neg.status.lower()}")

    load = db.query(Load).filter(Load.id == neg.load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="load_not_found")

    triage = triage_broker_contact(db, load.mc_number, load.id, driver.id, load.contact_instructions)
    broker_email = triage.get("email")
    if not broker_email:
        raise HTTPException(status_code=422, detail="no_broker_email_available")

    neg.status = "Sent"
    db.commit()

    raw_identity = driver.display_name or "dispatch"
    identity = _re.sub(r"[^a-z0-9]", "", raw_identity.lower()) or "dispatch"

    from app.services.email import send_negotiation_email as _send_neg_email
    background_tasks.add_task(
        _send_neg_email,
        broker_email,
        load.ref_id,
        load.origin,
        load.destination,
        identity,
        load.source_platform,
    )

    logger.info(
        "scout_approve: driver=%s approved neg=%s load=%s broker_email=%s",
        driver.id, neg.id, load.id, broker_email,
    )

    # HTMX: swap the card out; plain request: redirect
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '<div class="text-green-400 text-xs font-bold py-2 px-3 bg-green-500/10 border border-green-500/20 rounded-lg">'
            '<i class="fas fa-check mr-1"></i>Email sent to broker</div>'
        )
    return RedirectResponse(url="/drivers/scout-loads?tab=queued", status_code=303)


@app.post("/drivers/negotiations/{negotiation_id}/dismiss")
async def dismiss_queued_negotiation(
    negotiation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Driver dismisses a Queued negotiation — sets status=Dismissed (preserves audit trail)."""
    driver = _session_driver(request, db)
    if not driver:
        raise HTTPException(status_code=401, detail="not_authenticated")

    neg = (
        db.query(Negotiation)
        .filter(Negotiation.id == negotiation_id, Negotiation.driver_id == driver.id)
        .first()
    )
    if not neg:
        raise HTTPException(status_code=404, detail="negotiation_not_found")
    if neg.status not in ("Queued", "Draft"):
        raise HTTPException(status_code=409, detail=f"cannot_dismiss_status_{neg.status.lower()}")

    neg.status = "Dismissed"
    db.commit()

    logger.info("scout_dismiss: driver=%s dismissed neg=%s", driver.id, neg.id)

    if request.headers.get("HX-Request"):
        return HTMLResponse("")  # HTMX: remove the card from DOM
    return RedirectResponse(url="/drivers/scout-loads?tab=queued", status_code=303)


@app.get("/api/drivers/queued-count")
async def get_queued_count(request: Request, db: Session = Depends(get_db)):
    """Returns the number of Queued negotiations for the current driver (for badge polling)."""
    driver = _session_driver(request, db)
    if not driver:
        return {"queued": 0}
    count = (
        db.query(Negotiation)
        .filter(Negotiation.driver_id == driver.id, Negotiation.status == "Queued")
        .count()
    )
    return {"queued": count}


@app.get("/drivers/partials/first-mission")
async def first_mission_partial(request: Request):
    return templates.TemplateResponse(
        "drivers/partials/first_mission.html",
        {"request": request},
    )


@app.post("/internal/billing/run")
async def internal_billing_run(
    request: Request,
    week_ending: str | None = None,
    dry_run: bool = True,
    db: Session = Depends(get_db),
):
    """
    Admin-protected endpoint to trigger the weekly billing job.
    week_ending: YYYY-MM-DD (defaults to current Friday in America/New_York)
    dry_run: true = preview only, no Stripe charges, no DB writes
    """
    from app.routes.admin import _admin_token_authorized
    from app.services.billing import run_weekly_billing, current_week_ending
    import datetime as _dt

    admin_token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if not _admin_token_authorized(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if week_ending:
        try:
            parsed_week_ending = _dt.date.fromisoformat(week_ending)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "week_ending must be YYYY-MM-DD"})
    else:
        parsed_week_ending = current_week_ending()

    result = run_weekly_billing(db=db, week_ending=parsed_week_ending, dry_run=dry_run)

    response: dict = {
        "week_ending": parsed_week_ending.isoformat(),
        "dry_run": result.dry_run,
        "drivers_processed": result.drivers_processed,
        "drivers_succeeded": result.drivers_succeeded,
        "drivers_failed": result.drivers_failed,
        "drivers_skipped": result.drivers_skipped,
        "drivers_exempted": result.drivers_exempted,
        "total_amount_cents": result.total_amount_cents,
        "total_amount_usd": round(result.total_amount_cents / 100, 2),
        "exempt_total_amount_cents": result.exempt_total_amount_cents,
        "exempt_total_amount_usd": round(result.exempt_total_amount_cents / 100, 2),
    }

    if dry_run:
        response["dry_run_preview"] = [
            {
                "driver_id": r.driver_id,
                "invoice_ids": r.invoice_ids,
                "total_cents": r.total_amount_cents,
                "total_usd": round(r.total_amount_cents / 100, 2),
            }
            for r in result.driver_results
        ]
    else:
        response["driver_results"] = [
            {
                "driver_id": r.driver_id,
                "status": r.status,
                "total_cents": r.total_amount_cents,
                "stripe_payment_intent_id": r.stripe_payment_intent_id,
                "error_message": r.error_message,
            }
            for r in result.driver_results
        ]

    return JSONResponse(content=response)


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
