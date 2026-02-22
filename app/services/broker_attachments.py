from pathlib import Path

from sqlalchemy.orm import Session

from app.services.document_registry import get_active_documents
from app.services.packet_compose import compose_bol_packet
from app.services.packet_storage import read_bytes_by_key


_DOC_FILENAME = {
    "BOL_PACKET": "bol_packet.pdf",
    "RATECON": "ratecon.pdf",
    "W9": "w9.pdf",
    "INSURANCE": "coi.pdf",
    "AUTHORITY": "mc_authority.pdf",
}

_ALLOWED_TYPES = tuple(_DOC_FILENAME.keys())


def _read_doc_payload(doc: dict) -> bytes | None:
    file_key = str(doc.get("file_key") or "")
    if not file_key:
        return None

    if file_key.startswith("/"):
        local_path = Path(file_key)
        if not local_path.exists() or not local_path.is_file():
            return None
        try:
            return local_path.read_bytes()
        except OSError:
            return None

    return read_bytes_by_key(file_key, bucket=doc.get("bucket"))


def _normalize_selection(selections: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw in selections or []:
        doc_type = str(raw or "").strip().upper()
        if doc_type in _ALLOWED_TYPES and doc_type not in normalized:
            normalized.append(doc_type)
    return normalized


def _map_doc_for_selection(
    db: Session,
    *,
    driver_id: int,
    negotiation_id: int,
    doc_type: str,
) -> dict | None:
    scoped_negotiation_id = negotiation_id if doc_type in {"BOL_PACKET", "RATECON"} else None
    docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=scoped_negotiation_id,
        doc_types=[doc_type],
    )
    return docs[0] if docs else None


def build_broker_email_attachments(
    db: Session,
    *,
    negotiation_id: int,
    driver_id: int,
    selections: list[str] | None,
) -> dict:
    selected_types = _normalize_selection(selections)
    if not selected_types:
        selected_types = ["BOL_PACKET"]

    if "BOL_PACKET" in selected_types:
        bol_packet_doc = _map_doc_for_selection(
            db,
            driver_id=driver_id,
            negotiation_id=negotiation_id,
            doc_type="BOL_PACKET",
        )
        if not bol_packet_doc:
            bol_raw_exists = bool(
                get_active_documents(
                    db,
                    driver_id=driver_id,
                    negotiation_id=negotiation_id,
                    doc_types=["BOL_RAW", "BOL_PDF"],
                )
            )
            if bol_raw_exists:
                composed = compose_bol_packet(
                    db,
                    driver_id=driver_id,
                    negotiation_id=negotiation_id,
                )
                if not composed.get("ok"):
                    return {"ok": False, **composed}
            else:
                return {"ok": False, "message": "upload_bol_first"}

    attachments: list[tuple[str, bytes, str]] = []
    included_doc_types: list[str] = []

    for doc_type in selected_types:
        doc = _map_doc_for_selection(
            db,
            driver_id=driver_id,
            negotiation_id=negotiation_id,
            doc_type=doc_type,
        )
        if not doc:
            return {"ok": False, "message": f"{doc_type.lower()}_missing"}

        payload = _read_doc_payload(doc)
        if not payload:
            return {"ok": False, "message": f"{doc_type.lower()}_not_readable"}

        attachments.append((_DOC_FILENAME[doc_type], payload, "application/pdf"))
        included_doc_types.append(doc_type)

    return {
        "ok": True,
        "attachments": attachments,
        "included_doc_types": included_doc_types,
    }
