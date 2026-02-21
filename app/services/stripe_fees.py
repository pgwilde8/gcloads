from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import stripe
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings as core_settings
from app.models.driver import Driver


DISPATCH_FEE_RATE = Decimal(str(core_settings.DISPATCH_FEE_RATE))


class StripeConfigError(RuntimeError):
    pass


def _init_stripe() -> None:
    if not core_settings.STRIPE_SECRET_KEY:
        raise StripeConfigError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = core_settings.STRIPE_SECRET_KEY


def _money_to_cents(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _load_driver_by_email(db: Session, email: str) -> Optional[Driver]:
    if not email:
        return None
    return db.query(Driver).filter(Driver.email.ilike(email.strip())).first()


def ensure_customer_for_driver(db: Session, driver: Driver) -> str:
    _init_stripe()

    if driver.stripe_customer_id:
        return driver.stripe_customer_id

    customer = stripe.Customer.create(
        email=driver.email,
        name=driver.display_name,
        metadata={"driver_id": str(driver.id)},
    )
    driver.stripe_customer_id = customer["id"]
    if not driver.stripe_payment_status:
        driver.stripe_payment_status = "UNSET"
    db.commit()
    db.refresh(driver)
    return driver.stripe_customer_id


def create_setup_checkout_session(
    db: Session,
    driver_email: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    _init_stripe()

    driver = _load_driver_by_email(db, driver_email)
    if not driver:
        raise ValueError("Driver not found")

    customer_id = ensure_customer_for_driver(db, driver)

    session = stripe.checkout.Session.create(
        mode="setup",
        customer=customer_id,
        success_url=success_url,
        cancel_url=cancel_url,
        payment_method_types=["card"],
        metadata={"driver_id": str(driver.id), "driver_email": driver.email or ""},
    )

    return {"checkout_url": session.get("url"), "session_id": session.get("id")}


def create_dispatch_fee_charge(db: Session, negotiation_id: int) -> dict:
    _init_stripe()

    row = db.execute(
        text(
            """
            SELECT
                n.id AS negotiation_id,
                n.driver_email,
                l.id AS load_id,
                l.price,
                d.id AS driver_id,
                d.stripe_customer_id,
                d.stripe_default_payment_method_id,
                d.stripe_action_required
            FROM negotiations n
            JOIN loads l ON l.id = n.load_id
            LEFT JOIN drivers d ON lower(d.email) = lower(n.driver_email)
            WHERE n.id = :negotiation_id
            """
        ),
        {"negotiation_id": negotiation_id},
    ).mappings().first()

    if not row:
        raise ValueError("Negotiation not found")
    if not row["driver_id"]:
        raise ValueError("Driver not found for negotiation email")
    if not row["stripe_customer_id"] or not row["stripe_default_payment_method_id"]:
        raise ValueError("Driver payment method not set up")

    existing = db.execute(
        text(
            """
            SELECT id, stripe_payment_intent_id, status
            FROM dispatch_fee_payments
            WHERE negotiation_id = :negotiation_id
            """
        ),
        {"negotiation_id": negotiation_id},
    ).mappings().first()

    if existing and existing["status"] in {"SUCCEEDED", "PROCESSING", "PENDING"}:
        return {
            "status": existing["status"],
            "payment_intent_id": existing["stripe_payment_intent_id"],
            "message": "Charge already exists for negotiation",
        }

    price = _to_decimal(row["price"])
    fee_amount = (price * DISPATCH_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_cents = _money_to_cents(fee_amount)

    payment_intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        customer=row["stripe_customer_id"],
        payment_method=row["stripe_default_payment_method_id"],
        confirm=True,
        off_session=True,
        metadata={
            "negotiation_id": str(negotiation_id),
            "driver_id": str(row["driver_id"]),
            "load_id": str(row["load_id"]),
            "fee_type": "dispatch_fee",
        },
        idempotency_key=f"dispatch-fee:{negotiation_id}",
    )

    status = (payment_intent.get("status") or "requires_payment_method").upper()
    if status == "SUCCEEDED":
        internal_status = "SUCCEEDED"
    elif status in {"PROCESSING", "REQUIRES_CAPTURE"}:
        internal_status = "PROCESSING"
    else:
        internal_status = "FAILED"

    if existing:
        db.execute(
            text(
                """
                UPDATE dispatch_fee_payments
                SET stripe_payment_intent_id = :intent_id,
                    amount_cents = :amount_cents,
                    status = :status,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE negotiation_id = :negotiation_id
                """
            ),
            {
                "intent_id": payment_intent.get("id"),
                "amount_cents": amount_cents,
                "status": internal_status,
                "negotiation_id": negotiation_id,
            },
        )
    else:
        db.execute(
            text(
                """
                INSERT INTO dispatch_fee_payments (
                    negotiation_id, driver_id, stripe_payment_intent_id, amount_cents, currency, status
                )
                VALUES (
                    :negotiation_id, :driver_id, :intent_id, :amount_cents, 'usd', :status
                )
                """
            ),
            {
                "negotiation_id": negotiation_id,
                "driver_id": row["driver_id"],
                "intent_id": payment_intent.get("id"),
                "amount_cents": amount_cents,
                "status": internal_status,
            },
        )
    db.commit()

    return {
        "status": internal_status,
        "payment_intent_id": payment_intent.get("id"),
        "amount_cents": amount_cents,
    }


def handle_stripe_webhook(db: Session, payload: bytes, signature: str) -> dict:
    _init_stripe()

    if not core_settings.STRIPE_WEBHOOK_SECRET:
        raise StripeConfigError("STRIPE_WEBHOOK_SECRET is not configured")

    event = stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=core_settings.STRIPE_WEBHOOK_SECRET,
    )

    event_type = event.get("type")
    data_obj = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed" and data_obj.get("mode") == "setup":
        driver_id = (data_obj.get("metadata") or {}).get("driver_id")
        customer_id = data_obj.get("customer")
        setup_intent_id = data_obj.get("setup_intent")

        payment_method_id = None
        if setup_intent_id:
            setup_intent = stripe.SetupIntent.retrieve(setup_intent_id)
            payment_method_id = setup_intent.get("payment_method")

        if driver_id:
            db.execute(
                text(
                    """
                    UPDATE drivers
                    SET stripe_customer_id = COALESCE(:customer_id, stripe_customer_id),
                        stripe_default_payment_method_id = COALESCE(:payment_method_id, stripe_default_payment_method_id),
                        stripe_payment_status = 'READY',
                        stripe_action_required = FALSE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :driver_id
                    """
                ),
                {
                    "driver_id": int(driver_id),
                    "customer_id": customer_id,
                    "payment_method_id": payment_method_id,
                },
            )
            db.commit()

    elif event_type in {"payment_intent.succeeded", "payment_intent.payment_failed"}:
        payment_intent_id = data_obj.get("id")
        if payment_intent_id:
            status = "SUCCEEDED" if event_type == "payment_intent.succeeded" else "FAILED"
            error_message = None
            if status == "FAILED":
                last_error = data_obj.get("last_payment_error") or {}
                error_message = last_error.get("message")

            db.execute(
                text(
                    """
                    UPDATE dispatch_fee_payments
                    SET status = :status,
                        error_message = :error_message,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE stripe_payment_intent_id = :payment_intent_id
                    """
                ),
                {
                    "status": status,
                    "error_message": error_message,
                    "payment_intent_id": payment_intent_id,
                },
            )

            if status == "FAILED":
                driver_id = (data_obj.get("metadata") or {}).get("driver_id")
                if driver_id:
                    db.execute(
                        text(
                            """
                            UPDATE drivers
                            SET stripe_payment_status = 'PAYMENT_FAILED',
                                stripe_action_required = TRUE,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :driver_id
                            """
                        ),
                        {"driver_id": int(driver_id)},
                    )
            db.commit()

    return {"received": True, "event_type": event_type}
