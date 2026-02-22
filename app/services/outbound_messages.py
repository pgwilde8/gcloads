import json

from sqlalchemy import text
from sqlalchemy.orm import Session


def log_outbound_message(
    db: Session,
    *,
    negotiation_id: int | None,
    driver_id: int,
    recipient: str,
    subject: str,
    attachment_doc_types: list[str],
    status: str,
    error_message: str | None = None,
) -> int | None:
    inserted = db.execute(
        text(
            """
            INSERT INTO public.outbound_messages (
                negotiation_id,
                driver_id,
                channel,
                recipient,
                subject,
                attachment_doc_types,
                status,
                error_message
            )
            VALUES (
                :negotiation_id,
                :driver_id,
                'email',
                :recipient,
                :subject,
                CAST(:attachment_doc_types AS JSONB),
                :status,
                :error_message
            )
            RETURNING id
            """
        ),
        {
            "negotiation_id": negotiation_id,
            "driver_id": driver_id,
            "recipient": recipient,
            "subject": subject,
            "attachment_doc_types": json.dumps(attachment_doc_types or []),
            "status": status,
            "error_message": error_message,
        },
    ).first()
    return int(inserted.id) if inserted else None
