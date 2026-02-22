"""
Stripe billing client wrapper.
Handles off-session PaymentIntent creation for weekly driver fee charges.
Stripe customer creation at onboarding is handled elsewhere (routes/payments.py).
"""
import logging
from dataclasses import dataclass

import stripe
from stripe import StripeError

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class PaymentIntentResult:
    success: bool
    payment_intent_id: str | None
    error_message: str | None
    stripe_status: str | None  # e.g. 'succeeded', 'requires_action', 'requires_payment_method'


def _get_stripe_client() -> None:
    stripe.api_key = settings.STRIPE_SECRET_KEY


def create_payment_intent_off_session(
    customer_id: str,
    payment_method_id: str,
    amount_cents: int,
    idempotency_key: str,
    description: str = "CoDriver Freight weekly dispatch fee",
) -> PaymentIntentResult:
    """
    Create and confirm a Stripe PaymentIntent off-session.

    idempotency_key must be unique per (driver_id, week_ending) — e.g.
        f"billing-{driver_id}-{week_ending.isoformat()}"

    Returns PaymentIntentResult with success=True if status == 'succeeded'.
    """
    _get_stripe_client()

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="usd",
            customer=customer_id,
            payment_method=payment_method_id,
            off_session=True,
            confirm=True,
            description=description,
            idempotency_key=idempotency_key,
        )
        succeeded = intent.status == "succeeded"
        if not succeeded:
            logger.warning(
                "stripe_billing: PI %s status=%s customer=%s",
                intent.id, intent.status, customer_id,
            )
        return PaymentIntentResult(
            success=succeeded,
            payment_intent_id=intent.id,
            error_message=None if succeeded else f"Stripe status: {intent.status}",
            stripe_status=intent.status,
        )

    except stripe.error.CardError as e:
        err = e.error
        logger.warning(
            "stripe_billing: card_error code=%s customer=%s idempotency=%s",
            err.code, customer_id, idempotency_key,
        )
        return PaymentIntentResult(
            success=False,
            payment_intent_id=getattr(err, "payment_intent", {}).get("id") if err else None,
            error_message=f"card_error:{err.code}:{err.message}" if err else str(e),
            stripe_status="card_error",
        )

    except StripeError as e:
        logger.error(
            "stripe_billing: stripe_error customer=%s idempotency=%s error=%s",
            customer_id, idempotency_key, str(e),
        )
        return PaymentIntentResult(
            success=False,
            payment_intent_id=None,
            error_message=f"stripe_error:{str(e)}",
            stripe_status="stripe_error",
        )


def retrieve_payment_intent(payment_intent_id: str) -> PaymentIntentResult:
    """
    Retrieve a PI by id — used during needs_reconcile recovery.
    """
    _get_stripe_client()
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        succeeded = intent.status == "succeeded"
        return PaymentIntentResult(
            success=succeeded,
            payment_intent_id=intent.id,
            error_message=None if succeeded else f"Stripe status: {intent.status}",
            stripe_status=intent.status,
        )
    except StripeError as e:
        return PaymentIntentResult(
            success=False,
            payment_intent_id=payment_intent_id,
            error_message=f"stripe_error:{str(e)}",
            stripe_status="stripe_error",
        )
