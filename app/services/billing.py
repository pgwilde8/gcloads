"""
Weekly billing orchestration service.

Flow per driver:
  1. Load pending invoices
  2. Check for existing billing_run (idempotency)
  3. Create billing_run row
  4. Attach invoices to run
  5. Call Stripe off-session
  6. Commit results
  7. On Stripe success after DB failure: mark needs_reconcile
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytz
from sqlalchemy.orm import Session

from app.repositories import billing_repo
from app.services.stripe_billing import (
    PaymentIntentResult,
    create_payment_intent_off_session,
    retrieve_payment_intent,
)

logger = logging.getLogger(__name__)

BILLING_TIMEZONE = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class DriverRunResult:
    driver_id: int
    week_ending: date
    invoice_ids: list[int]
    total_amount_cents: int
    status: str           # success | failed | skipped | dry_run | needs_reconcile
    error_message: str | None = None
    stripe_payment_intent_id: str | None = None


@dataclass
class BillingJobResult:
    week_ending: date
    dry_run: bool
    drivers_processed: int = 0
    drivers_succeeded: int = 0
    drivers_failed: int = 0
    drivers_skipped: int = 0
    total_amount_cents: int = 0
    driver_results: list[DriverRunResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Billing window
# ---------------------------------------------------------------------------

def current_week_ending() -> date:
    """Returns the most recent Friday in America/New_York."""
    now = datetime.now(BILLING_TIMEZONE)
    days_since_friday = (now.weekday() - 4) % 7
    friday = now.date() if days_since_friday == 0 else (now - __import__("datetime").timedelta(days=days_since_friday)).date()
    return friday


# ---------------------------------------------------------------------------
# Main job entry point
# ---------------------------------------------------------------------------

def run_weekly_billing(
    db: Session,
    week_ending: date,
    dry_run: bool = False,
) -> BillingJobResult:
    """
    Execute (or preview) the weekly billing job for all active drivers
    with pending invoices up to week_ending.

    Idempotent: drivers with a successful run for this week_ending are skipped.
    """
    result = BillingJobResult(week_ending=week_ending, dry_run=dry_run)

    logger.info(
        "billing_job: starting week_ending=%s dry_run=%s",
        week_ending, dry_run,
    )

    # Reconcile any needs_reconcile runs first (from previous partial failures)
    if not dry_run:
        _reconcile_pending_runs(db)

    grouped = billing_repo.get_pending_invoices_grouped_by_driver(db, week_ending)

    if not grouped:
        logger.info("billing_job: no pending invoices found for week_ending=%s", week_ending)
        return result

    for driver_id, invoices in grouped.items():
        result.drivers_processed += 1
        driver_result = _process_driver(db, driver_id, invoices, week_ending, dry_run)
        result.driver_results.append(driver_result)

        if driver_result.status == "success":
            result.drivers_succeeded += 1
            result.total_amount_cents += driver_result.total_amount_cents
        elif driver_result.status in ("failed", "needs_reconcile"):
            result.drivers_failed += 1
        elif driver_result.status == "skipped":
            result.drivers_skipped += 1
        elif driver_result.status == "dry_run":
            result.total_amount_cents += driver_result.total_amount_cents

    logger.info(
        "billing_job: complete week_ending=%s processed=%d succeeded=%d failed=%d total_cents=%d",
        week_ending,
        result.drivers_processed,
        result.drivers_succeeded,
        result.drivers_failed,
        result.total_amount_cents,
    )
    return result


# ---------------------------------------------------------------------------
# Per-driver processing
# ---------------------------------------------------------------------------

def _process_driver(
    db: Session,
    driver_id: int,
    invoices: list[dict[str, Any]],
    week_ending: date,
    dry_run: bool,
) -> DriverRunResult:
    invoice_ids = [inv["id"] for inv in invoices]
    total_cents = sum(inv["fee_amount_cents"] for inv in invoices)

    # Dry run — no DB writes, no Stripe calls
    if dry_run:
        logger.info(
            "billing_job: dry_run driver=%d invoices=%d total_cents=%d",
            driver_id, len(invoice_ids), total_cents,
        )
        return DriverRunResult(
            driver_id=driver_id,
            week_ending=week_ending,
            invoice_ids=invoice_ids,
            total_amount_cents=total_cents,
            status="dry_run",
        )

    # Idempotency check — skip if already succeeded this week
    existing_run = billing_repo.get_billing_run(db, driver_id, week_ending)
    if existing_run and existing_run["status"] == "success":
        logger.info(
            "billing_job: skipping driver=%d already succeeded run_id=%d",
            driver_id, existing_run["id"],
        )
        return DriverRunResult(
            driver_id=driver_id,
            week_ending=week_ending,
            invoice_ids=invoice_ids,
            total_amount_cents=total_cents,
            status="skipped",
        )

    # Check driver has Stripe payment method
    driver_info = billing_repo.get_driver_stripe_info(db, driver_id)
    if not driver_info or not driver_info.get("stripe_customer_id") or not driver_info.get("stripe_default_payment_method_id"):
        logger.warning("billing_job: driver=%d missing stripe info — skipping", driver_id)
        return DriverRunResult(
            driver_id=driver_id,
            week_ending=week_ending,
            invoice_ids=invoice_ids,
            total_amount_cents=total_cents,
            status="skipped",
            error_message="missing_stripe_payment_method",
        )

    # Create billing_run row (idempotent via ON CONFLICT DO NOTHING)
    try:
        run_id = billing_repo.create_billing_run(db, driver_id, week_ending, total_cents)
        billing_repo.attach_invoices_to_run(db, run_id, invoice_ids, week_ending)
        db.commit()
    except ValueError as e:
        # Already succeeded — shouldn't reach here but be safe
        logger.info("billing_job: driver=%d %s", driver_id, str(e))
        return DriverRunResult(
            driver_id=driver_id,
            week_ending=week_ending,
            invoice_ids=invoice_ids,
            total_amount_cents=total_cents,
            status="skipped",
        )
    except Exception as e:
        db.rollback()
        logger.error("billing_job: driver=%d DB error creating run: %s", driver_id, str(e))
        return DriverRunResult(
            driver_id=driver_id,
            week_ending=week_ending,
            invoice_ids=invoice_ids,
            total_amount_cents=total_cents,
            status="failed",
            error_message=f"db_error:{str(e)}",
        )

    # Stripe idempotency key — deterministic per (driver_id, week_ending)
    idempotency_key = f"billing-{driver_id}-{week_ending.isoformat()}"

    stripe_result: PaymentIntentResult = create_payment_intent_off_session(
        customer_id=driver_info["stripe_customer_id"],
        payment_method_id=driver_info["stripe_default_payment_method_id"],
        amount_cents=total_cents,
        idempotency_key=idempotency_key,
        description=f"CoDriver Freight weekly fee — week ending {week_ending}",
    )

    if stripe_result.success:
        try:
            billing_repo.mark_run_success(db, run_id, stripe_result.payment_intent_id, invoice_ids)
            db.commit()
            logger.info(
                "billing_job: success driver=%d run_id=%d pi=%s total_cents=%d",
                driver_id, run_id, stripe_result.payment_intent_id, total_cents,
            )
            return DriverRunResult(
                driver_id=driver_id,
                week_ending=week_ending,
                invoice_ids=invoice_ids,
                total_amount_cents=total_cents,
                status="success",
                stripe_payment_intent_id=stripe_result.payment_intent_id,
            )
        except Exception as e:
            # Stripe charged successfully but DB commit failed — needs_reconcile
            db.rollback()
            logger.error(
                "billing_job: DB commit failed after Stripe success driver=%d pi=%s error=%s",
                driver_id, stripe_result.payment_intent_id, str(e),
            )
            try:
                billing_repo.mark_run_needs_reconcile(db, run_id, stripe_result.payment_intent_id)
                db.commit()
            except Exception:
                db.rollback()
            return DriverRunResult(
                driver_id=driver_id,
                week_ending=week_ending,
                invoice_ids=invoice_ids,
                total_amount_cents=total_cents,
                status="needs_reconcile",
                stripe_payment_intent_id=stripe_result.payment_intent_id,
                error_message=f"db_commit_failed_after_stripe_success:{str(e)}",
            )
    else:
        # Stripe failed — mark run + invoices failed, set driver delinquent
        try:
            billing_repo.mark_run_failed(db, run_id, stripe_result.error_message or "unknown", invoice_ids)
            billing_repo.set_driver_delinquent(db, driver_id)
            db.commit()
        except Exception as db_err:
            db.rollback()
            logger.error("billing_job: driver=%d failed to write failure state: %s", driver_id, str(db_err))

        logger.warning(
            "billing_job: failed driver=%d run_id=%d error=%s",
            driver_id, run_id, stripe_result.error_message,
        )
        return DriverRunResult(
            driver_id=driver_id,
            week_ending=week_ending,
            invoice_ids=invoice_ids,
            total_amount_cents=total_cents,
            status="failed",
            error_message=stripe_result.error_message,
        )


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _reconcile_pending_runs(db: Session) -> None:
    """
    For any billing_run with status=needs_reconcile, retrieve the Stripe PI
    and if it succeeded, mark the run and invoices as paid.
    """
    runs = billing_repo.get_needs_reconcile_runs(db)
    if not runs:
        return

    logger.info("billing_job: reconciling %d needs_reconcile runs", len(runs))

    for run in runs:
        pi_id = run.get("stripe_payment_intent_id")
        if not pi_id:
            continue

        stripe_result = retrieve_payment_intent(pi_id)
        if not stripe_result.success:
            logger.warning(
                "billing_job: reconcile run_id=%d pi=%s still not succeeded status=%s",
                run["id"], pi_id, stripe_result.stripe_status,
            )
            continue

        # Fetch invoice ids for this run
        from sqlalchemy import text as _text
        invoice_rows = db.execute(
            _text("SELECT driver_invoice_id FROM public.billing_run_items WHERE billing_run_id = :rid"),
            {"rid": run["id"]},
        ).scalars().all()

        try:
            billing_repo.mark_run_success(db, run["id"], pi_id, list(invoice_rows))
            db.commit()
            logger.info("billing_job: reconciled run_id=%d pi=%s", run["id"], pi_id)
        except Exception as e:
            db.rollback()
            logger.error("billing_job: reconcile commit failed run_id=%d: %s", run["id"], str(e))
