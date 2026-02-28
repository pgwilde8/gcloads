import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drivers", tags=["notifications"])


class NotificationResponse(BaseModel):
    unread_count: int
    notifications: Optional[List[dict]] = None


@router.get("/notifications/poll")
def poll_notifications(
    db: Session = Depends(get_db),
    x_driver_id: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Atomically claim and return undelivered notifications.

    Uses a single UPDATE … RETURNING CTE so two concurrent tabs cannot
    both receive the same notification and double-play sounds.
    Also bumps drivers.last_seen_at so session-suppression works correctly.
    """
    if not x_driver_id:
        raise HTTPException(status_code=401, detail="Driver ID required")

    driver_id = int(x_driver_id)

    # Bump last_seen_at so the email guard knows the driver is active
    try:
        db.execute(
            text("UPDATE drivers SET last_seen_at = NOW() WHERE id = :id"),
            {"id": driver_id},
        )
    except Exception as exc:
        logger.warning("poll: last_seen_at update failed: %s", exc)

    # Atomic claim: mark delivered and return in one statement
    rows = db.execute(
        text("""
            WITH claimed AS (
                UPDATE driver_notifications
                SET    delivered_at = NOW()
                WHERE  id IN (
                    SELECT id
                    FROM   driver_notifications
                    WHERE  driver_id    = :driver_id
                      AND  delivered_at IS NULL
                    ORDER  BY created_at DESC
                    LIMIT  10
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, notif_type, message, created_at
            )
            SELECT * FROM claimed ORDER BY created_at DESC
        """),
        {"driver_id": driver_id},
    ).fetchall()

    db.commit()

    if not rows:
        return JSONResponse({"unread_count": 0})

    notifications = []
    sound_types: set[str] = set()

    for row in rows:
        notifications.append({
            "id":         row.id,
            "type":       row.notif_type,
            "message":    row.message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
        if row.notif_type in ("LOAD_WON", "AUTO_SENT", "LOAD_MATCH"):
            sound_types.add(row.notif_type)

    payload = json.dumps({"unread_count": len(notifications), "notifications": notifications})
    response = JSONResponse(content=json.loads(payload))

    # One HX-Trigger header per distinct sound type (highest priority first)
    if sound_types:
        priority = ["LOAD_WON", "AUTO_SENT", "LOAD_MATCH"]
        chosen = next((t for t in priority if t in sound_types), None)
        if chosen:
            response.headers["HX-Trigger"] = json.dumps(
                {"playNotificationSound": {"type": chosen}}
            )

    return response


@router.get("/notifications/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    x_driver_id: Optional[str] = Header(default=None),
) -> dict:
    """Badge count — does not mark anything delivered."""
    if not x_driver_id:
        raise HTTPException(status_code=401, detail="Driver ID required")

    count = db.execute(
        text("""
            SELECT COUNT(*) AS unread_count
            FROM   driver_notifications
            WHERE  driver_id    = :driver_id
              AND  delivered_at IS NULL
        """),
        {"driver_id": int(x_driver_id)},
    ).scalar()

    return {"unread_count": count or 0}
