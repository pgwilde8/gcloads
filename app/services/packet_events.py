import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def log_packet_event(
    db: Session,
    *,
    negotiation_id: int | None,
    driver_id: int,
    event_type: str,
    doc_type: str,
    success: bool,
    meta: dict[str, Any] | None = None,
) -> int | None:
    inserted = db.execute(
        text(
            """
            INSERT INTO public.packet_events (
                negotiation_id,
                driver_id,
                event_type,
                doc_type,
                success,
                meta_json
            )
            VALUES (
                :negotiation_id,
                :driver_id,
                :event_type,
                :doc_type,
                :success,
                CAST(:meta_json AS JSONB)
            )
            RETURNING id
            """
        ),
        {
            "negotiation_id": negotiation_id,
            "driver_id": driver_id,
            "event_type": event_type,
            "doc_type": doc_type,
            "success": bool(success),
            "meta_json": json.dumps(meta or {}),
        },
    ).first()
    return int(inserted.id) if inserted else None
