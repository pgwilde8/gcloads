"""notification_guard.py

Single entry point for deciding whether to fire a driver alert email and for
inserting the driver_notifications row idempotently.

Rules enforced here (in order):
  1. notif_email_enabled must be True.
  2. Session suppression: if driver was active in-app within the last
     SESSION_SUPPRESS_MINUTES, skip email (in-app toast is enough).
  3. Quiet hours: evaluate in driver's local timezone; midnight-crossing windows
     handled correctly.  BROKER_REPLY bypasses quiet hours if driver opted in.
  4. Hourly cap: max EMAIL_CAP_PER_HOUR emails per driver per rolling hour.
  5. Digest mode: if notif_email_digest is True, only AUTO_SENT fires immediately;
     NEEDS_APPROVAL is suppressed (digest delivery is a separate job, not here).
  6. Deduplication: insert into driver_notifications with a dedupe_key; if the
     key already exists the insert is a no-op (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.driver import Driver

logger = logging.getLogger(__name__)

SESSION_SUPPRESS_MINUTES = 5
EMAIL_CAP_PER_HOUR = 3

# next_step values that bypass quiet hours (driver opted in to urgent alerts)
URGENT_TYPES = {"BROKER_REPLY"}


# ── Quiet-hours helper ────────────────────────────────────────────────────────

def _in_quiet_window(driver: "Driver") -> bool:
    """Return True if the current moment falls inside the driver's quiet window.

    Handles midnight-crossing windows (e.g. quiet_start=22, quiet_end=6).
    Falls back to UTC if the driver's timezone is invalid or unset.
    """
    quiet_start = driver.notif_quiet_start  # hour 0-23
    quiet_end = driver.notif_quiet_end      # hour 0-23

    if quiet_start is None or quiet_end is None:
        return False

    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(driver.timezone or "America/Chicago")
    except Exception:
        tz = timezone.utc

    local_hour = datetime.now(tz).hour

    if quiet_start < quiet_end:
        # Simple window: e.g. 08:00–20:00
        return quiet_start <= local_hour < quiet_end
    elif quiet_start > quiet_end:
        # Midnight-crossing: e.g. 22:00–06:00
        return local_hour >= quiet_start or local_hour < quiet_end
    else:
        # quiet_start == quiet_end → no quiet window configured
        return False


# ── Hourly cap ────────────────────────────────────────────────────────────────

def _emails_sent_last_hour(db: Session, driver_id: int) -> int:
    """Count driver_notifications rows created in the last 60 minutes that
    represent emails (not SAVED_ONLY, not digest-suppressed)."""
    row = db.execute(
        text("""
            SELECT COUNT(*) AS cnt
            FROM driver_notifications
            WHERE driver_id  = :driver_id
              AND created_at >= NOW() - INTERVAL '1 hour'
              AND notif_type  IN ('LOAD_MATCH', 'AUTO_SENT', 'BROKER_REPLY')
        """),
        {"driver_id": driver_id},
    ).fetchone()
    return int(row.cnt) if row else 0


# ── Main guard ────────────────────────────────────────────────────────────────

def should_email(
    db: Session,
    driver: "Driver",
    next_step: str,
) -> bool:
    """Return True if an alert email should be sent right now.

    Does NOT insert the notification row — call record_notification() for that.
    """
    if not getattr(driver, "notif_email_enabled", True):
        return False

    # Session suppression: driver is actively looking at the dashboard
    if driver.last_seen_at is not None:
        age_seconds = (
            datetime.now(timezone.utc) - driver.last_seen_at
        ).total_seconds()
        if age_seconds < SESSION_SUPPRESS_MINUTES * 60:
            logger.debug(
                "notif_guard: suppress email for driver=%s (active %ds ago)",
                driver.id, int(age_seconds),
            )
            return False

    # Quiet hours (urgent types bypass)
    if next_step not in URGENT_TYPES and _in_quiet_window(driver):
        logger.debug(
            "notif_guard: suppress email for driver=%s (quiet hours)", driver.id
        )
        return False

    # Digest mode: NEEDS_APPROVAL is batched, only AUTO_SENT fires immediately
    if getattr(driver, "notif_email_digest", False) and next_step == "NEEDS_APPROVAL":
        return False

    # Hourly cap
    if _emails_sent_last_hour(db, driver.id) >= EMAIL_CAP_PER_HOUR:
        logger.debug(
            "notif_guard: suppress email for driver=%s (hourly cap reached)", driver.id
        )
        return False

    return True


def record_notification(
    db: Session,
    *,
    driver_id: int,
    notif_type: str,
    message: str,
    payload: dict | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """Insert a driver_notifications row.

    If dedupe_key is provided and a row with that key already exists, the insert
    is silently skipped (ON CONFLICT DO NOTHING) and False is returned.
    Returns True if a new row was inserted.
    """
    try:
        result = db.execute(
            text("""
                INSERT INTO driver_notifications
                    (driver_id, notif_type, message, payload, dedupe_key)
                VALUES
                    (:driver_id, :notif_type, :message, :payload::jsonb, :dedupe_key)
                ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL
                DO NOTHING
                RETURNING id
            """),
            {
                "driver_id":   driver_id,
                "notif_type":  notif_type,
                "message":     message,
                "payload":     __import__("json").dumps(payload or {}),
                "dedupe_key":  dedupe_key,
            },
        )
        db.commit()
        return result.fetchone() is not None
    except Exception as exc:
        logger.warning("record_notification failed: %s", exc)
        db.rollback()
        return False
