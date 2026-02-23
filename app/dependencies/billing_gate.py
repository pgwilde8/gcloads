"""
Payment method enforcement for paid drivers.
Beta and exempt drivers bypass; paid drivers must have Stripe customer + default payment method.
"""
from __future__ import annotations

from datetime import date

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.driver import Driver
from app.repositories.billing_repo import (
    get_driver_stripe_info,
    has_payment_method,
    is_driver_billing_exempt,
)


def _session_driver(request: Request, db: Session) -> Driver | None:
    """Resolve driver from session. Shared with route auth."""
    session_driver_id = request.session.get("user_id")
    if not session_driver_id:
        return None
    return db.query(Driver).filter(Driver.id == session_driver_id).first()


def require_payment_method_if_paid(
    request: Request,
    db: Session = Depends(get_db),
) -> Driver:
    """
    Enforces: paid drivers must have a payment method unless billing-exempt.
    Beta drivers are exempt via is_driver_billing_exempt.
    Raises 401 if no session driver, 402 if paid and missing payment method.
    Returns the driver for route use.
    """
    driver = _session_driver(request, db)
    if not driver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "auth_required"},
        )

    driver_info = get_driver_stripe_info(db, driver.id)
    if not driver_info:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Driver not found.", "code": "driver_not_found"},
        )

    today = date.today()
    if is_driver_billing_exempt(driver_info, today):
        return driver

    billing_mode = (driver_info.get("billing_mode") or "paid").lower()
    if billing_mode == "paid" and not has_payment_method(driver_info):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "Payment method required to send or negotiate loads.",
                "code": "payment_method_required",
                "stripe_setup_required": True,
            },
        )

    return driver
