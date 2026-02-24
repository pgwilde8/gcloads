from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel

from app.database import get_db
from app.models.driver import Driver

router = APIRouter(prefix="/api/drivers", tags=["notifications"])

class NotificationResponse(BaseModel):
    unread_count: int
    notifications: Optional[List[dict]] = None

@router.get("/notifications/poll")
def poll_notifications(
    db: Session = Depends(get_db),
    x_driver_id: Optional[str] = Header(default=None)
) -> dict:
    """Poll for new notifications and trigger audio alerts via HTMX."""
    
    if not x_driver_id:
        raise HTTPException(status_code=401, detail="Driver ID required")
    
    # Get undelivered notifications
    result = db.execute(
        text("""
            SELECT id, driver_id, notif_type, message, created_at
            FROM driver_notifications 
            WHERE driver_id = :driver_id 
            AND delivered_at IS NULL
            ORDER BY created_at DESC
            LIMIT 5
        """),
        {"driver_id": int(x_driver_id)}
    ).fetchall()
    
    if not result:
        return {"unread_count": 0}
    
    # Mark as delivered to prevent repeats
    notification_ids = [row.id for row in result]
    db.execute(
        text("""
            UPDATE driver_notifications 
            SET delivered_at = NOW() 
            WHERE id = ANY(:ids)
        """),
        {"ids": notification_ids}
    )
    db.commit()
    
    # Build response with HTMX triggers
    notifications = []
    triggers = []
    
    for row in result:
        notif = {
            "id": row.id,
            "type": row.notif_type,
            "message": row.message,
            "created_at": row.created_at.isoformat() if row.created_at else None
        }
        notifications.append(notif)
        
        # Set trigger for audio
        if row.notif_type == "LOAD_WON":
            triggers.append("playNotificationSound,type:LOAD_WON")
        elif row.notif_type == "LOAD_MATCH":
            triggers.append("playNotificationSound,type:LOAD_MATCH")
    
    # Return response that will trigger audio
    from fastapi.responses import Response
    
    response_content = {
        "unread_count": len(notifications),
        "notifications": notifications
    }
    
    response = Response(
        content=str(response_content).replace("'", '"'),  # Quick JSON string
        media_type="application/json"
    )
    
    # Add HTMX trigger headers
    if triggers:
        response.headers["HX-Trigger"] = ", ".join(triggers)
    
    return response

@router.get("/notifications/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    x_driver_id: Optional[str] = Header(default=None)
) -> dict:
    """Simple endpoint for badge count."""
    
    if not x_driver_id:
        raise HTTPException(status_code=401, detail="Driver ID required")
    
    count = db.execute(
        text("""
            SELECT COUNT(*) as unread_count
            FROM driver_notifications 
            WHERE driver_id = :driver_id 
            AND delivered_at IS NULL
        """),
        {"driver_id": int(x_driver_id)}
    ).scalar()
    
    return {"unread_count": count or 0}
