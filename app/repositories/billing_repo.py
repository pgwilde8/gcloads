"""
Billing repository — all DB reads/writes for the weekly billing job.
Uses SQLAlchemy text() + Session, matching the existing codebase pattern.
"""
import logging
from datetime import date
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


def get_driver_stripe_info(db: Session, driver_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text("""
            SELECT id, stripe_customer_id, stripe_default_payment_method_id,
                   billing_state, stripe_payment_status
            FROM public.drivers
            WHERE id = :driver_id
        """),
        {"driver_id": driver_id},
    ).mappings().first()
    return dict(row) if row else None


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


def mark_run_success(
    db: Session,
    billing_run_id: int,
    stripe_payment_intent_id: str,
    invoice_ids: list[int],
) -> None:
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
                UPDATE public.driver_invoices
                SET status = 'paid',
                    stripe_payment_intent_id = :pi_id,
                    paid_at = CURRENT_TIMESTAMP
                WHERE id = ANY(:invoice_ids)
            """),
            {"pi_id": stripe_payment_intent_id, "invoice_ids": invoice_ids},
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
