from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.stripe_fees import (
    StripeConfigError,
    create_dispatch_fee_charge,
    create_setup_checkout_session,
    handle_stripe_webhook,
)

router = APIRouter()


class SetupCheckoutRequest(BaseModel):
    driver_email: str
    success_url: str
    cancel_url: str


class ChargeDispatchFeeRequest(BaseModel):
    negotiation_id: int


@router.post("/api/payments/setup-checkout-session")
def setup_checkout_session(payload: SetupCheckoutRequest, db: Session = Depends(get_db)):
    try:
        return create_setup_checkout_session(
            db=db,
            driver_email=payload.driver_email,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StripeConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Stripe checkout error: {exc}") from exc


@router.post("/api/payments/charge-dispatch-fee")
def charge_dispatch_fee(payload: ChargeDispatchFeeRequest, db: Session = Depends(get_db)):
    try:
        return create_dispatch_fee_charge(db=db, negotiation_id=payload.negotiation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StripeConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Stripe charge error: {exc}") from exc


@router.post("/api/stripe/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
):
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    payload = await request.body()

    try:
        return handle_stripe_webhook(db=db, payload=payload, signature=stripe_signature)
    except StripeConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook error: {exc}") from exc
