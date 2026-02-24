"""
Billing repository — all DB reads/writes for the weekly billing job.
Uses SQLAlchemy text() + Session, matching the existing codebase pattern.
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pending invoice queries
# ---------------------------------------------------------------------------

def get_pending_invoices_grouped_by_driver(
    db: Session,
    up_to_week_ending: date,
) -> dict[int, list[dict[str, Any]]]:
    """
    Returns all pending driver_invoices with no billed_week_ending yet,
    grouped by driver_id. Only includes drivers whose billing_state = 'active'.
    """
    rows = db.execute(
        text("""
            SELECT
                di.id,
                di.driver_id,
                di.negotiation_id,
                di.gross_amount_cents,
                di.fee_amount_cents,
                di.fee_rate,
                di.status,
                di.created_at
            FROM public.driver_invoices di
            JOIN public.drivers d ON d.id = di.driver_id
            WHERE di.status = 'pending'
              AND di.billed_week_ending IS NULL
              AND di.created_at::date <= :up_to
              AND d.billing_state = 'active'
            ORDER BY di.driver_id, di.created_at
        """),
        {"up_to": up_to_week_ending},
    ).mappings().all()

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        r = dict(row)
        grouped.setdefault(r["driver_id"], []).append(r)
    return grouped


def has_payment_method(driver_info: dict[str, Any] | None) -> bool:
    """True if driver has Stripe customer and default payment method."""
    return bool(
        driver_info
        and driver_info.get("stripe_customer_id")
        and driver_info.get("stripe_default_payment_method_id")
    )


def get_driver_stripe_info(db: Session, driver_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text("""
            SELECT id, stripe_customer_id, stripe_default_payment_method_id,
                   billing_state, stripe_payment_status,
                   billing_mode, billing_exempt_until, billing_exempt_reason
            FROM public.drivers
            WHERE id = :driver_id
        """),
        {"driver_id": driver_id},
    ).mappings().first()
    return dict(row) if row else None


def billing_bootstrap_for_driver(db: Session, driver_id: int) -> dict[str, Any]:
    """
    Compute billing flags for frontend/bootstrap. All server-side so frontend stays dumb.
    Returns: billing_mode, billing_exempt_until, billing_exempt_reason,
             is_currently_billing_exempt, has_payment_method.
    """
    driver_info = get_driver_stripe_info(db, driver_id)
    if not driver_info:
        return {
            "billing_mode": "paid",
            "billing_exempt_until": None,
            "billing_exempt_reason": None,
            "is_currently_billing_exempt": False,
            "has_payment_method": False,
        }
    today = date.today()
    exempt = is_driver_billing_exempt(driver_info, today)
    return {
        "billing_mode": driver_info.get("billing_mode") or "paid",
        "billing_exempt_until": driver_info.get("billing_exempt_until"),
        "billing_exempt_reason": driver_info.get("billing_exempt_reason"),
        "is_currently_billing_exempt": exempt,
        "has_payment_method": has_payment_method(driver_info),
    }


def go_live_clear_exemption(db: Session, driver_id: int) -> dict[str, Any] | None:
    """
    Driver-initiated: requires card on file (checked by caller).
    Clears exemption immediately so weekly billing will charge next run.
    Idempotent: if already paid + not exempt, caller can treat as success.
    """
    row = db.execute(
        text("""
            UPDATE public.drivers
            SET
                billing_mode = 'paid',
                billing_exempt_until = NULL,
                billing_exempt_reason = NULL
            WHERE id = :driver_id
            RETURNING id, billing_mode, billing_exempt_until
        """),
        {"driver_id": driver_id},
    ).mappings().first()
    return dict(row) if row else None


def is_driver_billing_exempt(driver_info: dict[str, Any] | None, week_ending: date) -> bool:
    """
    True if driver should not be charged (beta or exempt_until covers this week).
    Exempt for billing weeks with week_ending <= exempt_until (both DATE).
    """
    if not driver_info:
        return False
    if (driver_info.get("billing_mode") or "").lower() == "beta":
        return True
    exempt_until = driver_info.get("billing_exempt_until")
    if exempt_until is None:
        return False
    # Normalize to date for comparison (handles datetime from DB)
    exempt_date = exempt_until.date() if isinstance(exempt_until, datetime) else exempt_until
    return week_ending <= exempt_date


def get_billing_run(db: Session, driver_id: int, week_ending: date) -> dict[str, Any] | None:
    row = db.execute(
        text("""
            SELECT id, driver_id, week_ending, status,
                   total_amount_cents, stripe_payment_intent_id, error_message
            FROM public.billing_runs
            WHERE driver_id = :driver_id AND week_ending = :week_ending
        """),
        {"driver_id": driver_id, "week_ending": week_ending},
    ).mappings().first()
    return dict(row) if row else None


def get_needs_reconcile_runs(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text("""
            SELECT id, driver_id, week_ending, stripe_payment_intent_id, total_amount_cents
            FROM public.billing_runs
            WHERE status = 'needs_reconcile'
            ORDER BY created_at
        """),
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_billing_run(
    db: Session,
    driver_id: int,
    week_ending: date,
    total_amount_cents: int,
    dry_run: bool = False,
) -> int:
    """
    Insert a billing_run row. Returns the new run id.
    Uses INSERT ... ON CONFLICT DO NOTHING to be idempotent on the unique key.
    Raises if a successful run already exists for this (driver_id, week_ending).
    """
    status = "dry_run" if dry_run else "pending"
    row = db.execute(
        text("""
            INSERT INTO public.billing_runs
                (driver_id, week_ending, status, total_amount_cents)
            VALUES (:driver_id, :week_ending, :status, :total_amount_cents)
            ON CONFLICT (driver_id, week_ending) DO NOTHING
            RETURNING id
        """),
        {
            "driver_id": driver_id,
            "week_ending": week_ending,
            "status": status,
            "total_amount_cents": total_amount_cents,
        },
    ).mappings().first()

    if row is None:
        # Row already existed — fetch it
        existing = get_billing_run(db, driver_id, week_ending)
        if existing and existing["status"] == "success":
            raise ValueError(
                f"Billing run for driver {driver_id} week {week_ending} already succeeded — skipping."
            )
        return existing["id"]  # type: ignore[index]

    return row["id"]


def attach_invoices_to_run(
    db: Session,
    billing_run_id: int,
    invoice_ids: list[int],
    week_ending: date,
) -> None:
    """Mark invoices as belonging to this run and set their billed_week_ending."""
    if not invoice_ids:
        return
    db.execute(
        text("""
            INSERT INTO public.billing_run_items (billing_run_id, driver_invoice_id)
            SELECT :run_id, unnest(:invoice_ids::int[])
            ON CONFLICT (driver_invoice_id) DO NOTHING
        """),
        {"run_id": billing_run_id, "invoice_ids": invoice_ids},
    )
    db.execute(
        text("""
            UPDATE public.driver_invoices
            SET billed_week_ending = :week_ending
            WHERE id = ANY(:invoice_ids)
        """),
        {"week_ending": week_ending, "invoice_ids": invoice_ids},
    )


def mark_run_exempt_success(
    db: Session,
    billing_run_id: int,
    invoice_ids: list[int],
) -> None:
    """
    Mark run as exempt_success (no Stripe charge).
    Settles invoices via billing_run_items join — only updates pending invoices
    that belong to this run. Sets is_exempt=TRUE so revenue reports exclude them.
    """
    db.execute(
        text("""
            UPDATE public.billing_runs
            SET status = 'exempt_success',
                stripe_payment_intent_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
        """),
        {"run_id": billing_run_id},
    )
    if invoice_ids:
        db.execute(
            text("""
                UPDATE public.driver_invoices di
                SET status = 'paid',
                    stripe_payment_intent_id = NULL,
                    paid_at = CURRENT_TIMESTAMP,
                    is_exempt = TRUE
                FROM public.billing_run_items bri
                WHERE bri.billing_run_id = :run_id
                  AND bri.driver_invoice_id = di.id
                  AND di.status = 'pending'
            """),
            {"run_id": billing_run_id},
        )


def mark_run_success(
    db: Session,
    billing_run_id: int,
    stripe_payment_intent_id: str,
    invoice_ids: list[int],
) -> None:
    """Mark run and invoices as paid via Stripe. Sets is_exempt=FALSE (cash payment)."""
    db.execute(
        text("""
            UPDATE public.billing_runs
            SET status = 'success',
                stripe_payment_intent_id = :pi_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
        """),
        {"pi_id": stripe_payment_intent_id, "run_id": billing_run_id},
    )
    if invoice_ids:
        db.execute(
            text("""
                UPDATE public.driver_invoices di
                SET status = 'paid',
                    stripe_payment_intent_id = :pi_id,
                    paid_at = CURRENT_TIMESTAMP,
                    is_exempt = FALSE
                FROM public.billing_run_items bri
                WHERE bri.billing_run_id = :run_id
                  AND bri.driver_invoice_id = di.id
                  AND di.status = 'pending'
            """),
            {"pi_id": stripe_payment_intent_id, "run_id": billing_run_id},
        )


def mark_run_failed(
    db: Session,
    billing_run_id: int,
    error_message: str,
    invoice_ids: list[int],
) -> None:
    db.execute(
        text("""
            UPDATE public.billing_runs
            SET status = 'failed',
                error_message = :error,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
        """),
        {"error": error_message, "run_id": billing_run_id},
    )
    if invoice_ids:
        db.execute(
            text("""
                UPDATE public.driver_invoices
                SET status = 'failed'
                WHERE id = ANY(:invoice_ids)
            """),
            {"invoice_ids": invoice_ids},
        )


def mark_run_needs_reconcile(
    db: Session,
    billing_run_id: int,
    stripe_payment_intent_id: str,
) -> None:
    """
    Called when Stripe succeeded but the DB commit failed.
    Stores the PI id so reconciliation can confirm payment later.
    """
    db.execute(
        text("""
            UPDATE public.billing_runs
            SET status = 'needs_reconcile',
                stripe_payment_intent_id = :pi_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
        """),
        {"pi_id": stripe_payment_intent_id, "run_id": billing_run_id},
    )


# ---------------------------------------------------------------------------
# Beta admin: list, promote, extend
# ---------------------------------------------------------------------------


def list_beta_drivers_with_exempt_stats(db: Session) -> list[dict[str, Any]]:
    """
    List drivers with billing_mode='beta', plus exempt invoice counts and amounts.
    """
    rows = db.execute(
        text("""
            SELECT
                d.id,
                d.display_name,
                d.email,
                d.mc_number,
                d.billing_mode,
                d.billing_exempt_until,
                d.billing_exempt_reason,
                d.stripe_customer_id,
                d.stripe_default_payment_method_id,
                d.created_at,
                COALESCE(stats.exempt_count, 0)::int AS exempt_invoice_count,
                COALESCE(stats.exempt_amount_cents, 0)::int AS exempt_amount_cents,
                stats.last_exempt_date AS last_exempt_invoice_date
            FROM public.drivers d
            LEFT JOIN (
                SELECT driver_id,
                       COUNT(*) AS exempt_count,
                       SUM(fee_amount_cents) AS exempt_amount_cents,
                       MAX(COALESCE(paid_at, created_at)::date) AS last_exempt_date
                FROM public.driver_invoices
                WHERE is_exempt = TRUE
                GROUP BY driver_id
            ) stats ON stats.driver_id = d.id
            WHERE d.billing_mode = 'beta'
            ORDER BY d.created_at DESC
        """),
    ).mappings().all()

    result = []
    today = date.today()
    for row in rows:
        r = dict(row)
        # Beta drivers are always exempt; others use exempt_until >= today
        if (r.get("billing_mode") or "").lower() == "beta":
            r["currently_exempt"] = True
        else:
            exempt_until = r.get("billing_exempt_until")
            exempt_date = exempt_until.date() if isinstance(exempt_until, datetime) else exempt_until
            r["currently_exempt"] = exempt_date is not None and exempt_date >= today
        # Payment method presence: promotion will immediately gate until PM added
        r["has_payment_method"] = has_payment_method(r)
        result.append(r)
    return result


def promote_beta_to_paid(db: Session, driver_id: int) -> bool:
    """
    Set billing_mode='paid', clear exempt fields. Past invoices remain is_exempt.
    Returns True if updated, False if driver not found or not beta.
    """
    row = db.execute(
        text("""
            UPDATE public.drivers
            SET billing_mode = 'paid',
                billing_exempt_until = NULL,
                billing_exempt_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :driver_id AND billing_mode = 'beta'
            RETURNING id
        """),
        {"driver_id": driver_id},
    ).mappings().first()
    return row is not None


def extend_billing_exemption(
    db: Session,
    driver_id: int,
    new_exempt_until: date,
    reason: str | None = None,
) -> bool:
    """
    Set billing_exempt_until = GREATEST(existing, new_date).
    Only overwrite billing_exempt_reason if reason is non-empty.
    Caller must validate new_exempt_until >= today.
    Returns True if updated.
    """
    # Normalize to date (handles datetime if passed)
    normalized_date = new_exempt_until.date() if isinstance(new_exempt_until, datetime) else new_exempt_until

    row = db.execute(
        text("""
            UPDATE public.drivers
            SET billing_exempt_until = GREATEST(COALESCE(billing_exempt_until, :new_date), :new_date),
                billing_exempt_reason = COALESCE(NULLIF(TRIM(:reason), ''), billing_exempt_reason),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :driver_id
            RETURNING id
        """),
        {"driver_id": driver_id, "new_date": normalized_date, "reason": (reason or "").strip()},
    ).mappings().first()
    return row is not None


def set_driver_delinquent(db: Session, driver_id: int) -> None:
    db.execute(
        text("""
            UPDATE public.drivers
            SET billing_state = 'delinquent',
                stripe_action_required = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :driver_id
        """),
        {"driver_id": driver_id},
    )


def create_driver_invoice(
    db: Session,
    driver_id: int,
    negotiation_id: int,
    gross_amount_cents: int,
    fee_rate: Decimal = Decimal("0.0250"),
) -> int:
    """
    Create a pending driver_invoice when a load is marked delivered.
    Returns the new invoice id. Idempotent on negotiation_id.
    """
    fee_amount_cents = int(gross_amount_cents * fee_rate)
    row = db.execute(
        text("""
            INSERT INTO public.driver_invoices
                (driver_id, negotiation_id, gross_amount_cents, fee_rate, fee_amount_cents, status)
            VALUES
                (:driver_id, :negotiation_id, :gross, :fee_rate, :fee_cents, 'pending')
            ON CONFLICT (negotiation_id) DO NOTHING
            RETURNING id
        """),
        {
            "driver_id": driver_id,
            "negotiation_id": negotiation_id,
            "gross": gross_amount_cents,
            "fee_rate": str(fee_rate),
            "fee_cents": fee_amount_cents,
        },
    ).mappings().first()
    db.commit()
    if row is None:
        existing = db.execute(
            text("SELECT id FROM public.driver_invoices WHERE negotiation_id = :nid"),
            {"nid": negotiation_id},
        ).scalar()
        return existing  # type: ignore[return-value]
    return row["id"]
