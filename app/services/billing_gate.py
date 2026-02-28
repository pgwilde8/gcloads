"""billing_gate.py

Single source of truth for trial / activation gating.

Allowed during trial:
  - Scout ingest, load scoring, saving loads
  - NEEDS_APPROVAL queueing (internal record only)
  - Notifications (in-app + email)
  - Dashboard browsing

Blocked unless billing_status == 'active':
  - Any outbound broker email (auto-bid AND manual send)
  - Packet compose
  - Factoring submission
  - Secure-load / mark negotiation CLOSED / WON

Does NOT touch: ledger, weekly billing job, Stripe charge code,
                billing_mode, billing_exempt_until, billing_state.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from app.models.driver import Driver

logger = logging.getLogger(__name__)

TRIAL_DAYS = 7

# ── Internal helpers ──────────────────────────────────────────────────────────

def _billing_status(driver: "Driver") -> str:
    return (getattr(driver, "billing_status", None) or "trial").strip().lower()


def _is_beta(driver: "Driver") -> bool:
    return (getattr(driver, "billing_mode", None) or "").strip().lower() == "beta"


def maybe_flip_trial_expired(driver: "Driver", db) -> bool:
    """If the driver's trial has expired, flip billing_status to 'card_required'.

    Call this on every authenticated page load (cheap: one attribute read).
    Returns True if the status was changed (so the caller can commit).
    Beta drivers are always skipped — they never expire.
    """
    if _is_beta(driver):
        return False
    if _billing_status(driver) != "trial":
        return False

    trial_ends_at = getattr(driver, "trial_ends_at", None)
    if trial_ends_at is None:
        return False

    now = datetime.now(timezone.utc)
    # Make trial_ends_at tz-aware if stored naive
    if trial_ends_at.tzinfo is None:
        trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)

    if now > trial_ends_at:
        driver.billing_status = "card_required"
        db.add(driver)
        logger.info("billing_gate: trial expired for driver=%s → card_required", driver.id)
        return True

    return False


# ── Public API ────────────────────────────────────────────────────────────────

def is_active(driver: "Driver") -> bool:
    """True for active billing OR beta drivers (beta is always active)."""
    return _is_beta(driver) or _billing_status(driver) == "active"


def is_trial(driver: "Driver") -> bool:
    """True only for public trial drivers — beta drivers are never in trial."""
    return not _is_beta(driver) and _billing_status(driver) == "trial"


def trial_days_remaining(driver: "Driver") -> int | None:
    """Return days left in trial (ceiling, minimum 1 if still active), or None.

    Uses ceiling so "4 hours left" shows as "1 day" rather than "0 days".
    Returns None when not in trial or trial_ends_at is missing.
    """
    if not is_trial(driver):
        return None
    trial_ends_at = getattr(driver, "trial_ends_at", None)
    if trial_ends_at is None:
        return None
    now = datetime.now(timezone.utc)
    if trial_ends_at.tzinfo is None:
        trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)
    seconds_left = (trial_ends_at - now).total_seconds()
    if seconds_left <= 0:
        return 0
    # Ceiling: any partial day counts as a full day (minimum 1)
    return max(1, math.ceil(seconds_left / 86400))


def require_active(driver: "Driver", action: str = "this action") -> None:
    """Raise HTTP 402 if the driver is not on an active billing plan.

    Use at the top of any route or service that does real work.
    Beta drivers (billing_mode='beta') always pass — they are never gated.
    """
    if _is_beta(driver):
        return
    status = _billing_status(driver)
    if status == "active":
        return

    if status == "trial":
        days = trial_days_remaining(driver)
        if days is not None and days > 0:
            days_str = f"Your trial ends in {days} day{'s' if days != 1 else ''}. "
        else:
            days_str = "Your trial ends today. "
        detail = (
            f"Activate dispatch automation to {action}. "
            f"{days_str}"
            "No charge today — pay only 2.5% per paid load."
        )
    elif status == "card_required":
        detail = (
            f"Your trial has ended. Activate dispatch automation to {action}. "
            "No charge today — pay only 2.5% per paid load."
        )
    else:  # suspended
        detail = f"Your account is suspended. Contact support to {action}."

    raise HTTPException(status_code=402, detail=detail)
