import hashlib
from io import BytesIO
from pathlib import Path

from PyPDF2 import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.services.document_registry import get_active_documents, upsert_driver_document
from app.services.packet_readiness import packet_readiness_for_driver
from app.services.packet_storage import generate_presigned_get_url, read_bytes_by_key, save_bytes_by_key
from app.services.storage_keys import bol_processed_key, negotiation_packet_key


def _sha256_hex(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _read_document_payload(doc: dict) -> bytes | None:
    file_key = str(doc.get("file_key") or "")
    if not file_key:
        return None
    return read_bytes_by_key(file_key, bucket=doc.get("bucket"))


def _merge_pdf_bytes(parts: list[tuple[str, bytes]]) -> bytes | None:
    writer = PdfWriter()

    for _, payload in parts:
        try:
            reader = PdfReader(BytesIO(payload))
        except Exception:
            return None

        for page in reader.pages:
            writer.add_page(page)

    if not writer.pages:
        return None

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _doc_presigned_url(bucket: str | None, file_key: str | None) -> str | None:
    if not bucket or not file_key or file_key.startswith("/"):
        return None
    return generate_presigned_get_url(bucket, file_key)


def _local_path_for_key(file_key: str | None) -> str | None:
    if not file_key:
        return None
    path_candidate = Path(file_key)
    if path_candidate.is_absolute():
        return str(path_candidate) if path_candidate.exists() else None

    local_candidate = Path("/srv/gcd-data") / file_key
    return str(local_candidate) if local_candidate.exists() else None


def _active_doc(
    db: Session,
    *,
    driver_id: int,
    negotiation_id: int | None,
    doc_type: str,
) -> dict | None:
    docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=[doc_type],
    )
    return docs[0] if docs else None


def compose_bol_packet(db: Session, *, driver_id: int, negotiation_id: int, force: bool = False) -> dict:
    if not force:
        existing = _active_doc(
            db,
            driver_id=driver_id,
            negotiation_id=negotiation_id,
            doc_type="BOL_PACKET",
        )
        if existing:
            return {
                "ok": True,
                "bucket": existing.get("bucket"),
                "file_key": existing.get("file_key"),
                "local_path": _local_path_for_key(str(existing.get("file_key") or "")),
                "presigned_url": _doc_presigned_url(existing.get("bucket"), existing.get("file_key")),
                "reused": True,
            }

    bol_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=["BOL_PDF", "BOL_RAW"],
    )
    if not bol_docs:
        return {"ok": False, "message": "upload_bol_first"}

    selected_bol = bol_docs[0]
    bol_bytes = _read_document_payload(selected_bol)
    if not bol_bytes:
        return {"ok": False, "message": "bol_not_readable"}

    output_key = bol_processed_key(driver_id, negotiation_id)
    saved = save_bytes_by_key(output_key, bol_bytes, content_type="application/pdf")
    if not saved["local_saved"] and not saved["spaces_saved"]:
        return {"ok": False, "message": "bol_packet_storage_write_failed"}

    stored_key = output_key if saved["spaces_saved"] else str(saved["local_path"])
    stored_bucket = str(saved["bucket"]) if saved["spaces_saved"] and saved["bucket"] else None
    upsert_driver_document(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_type="BOL_PACKET",
        bucket=stored_bucket,
        file_key=stored_key,
        sha256_hash=_sha256_hex(bol_bytes),
    )

    return {
        "ok": True,
        "bucket": stored_bucket,
        "file_key": stored_key,
        "local_path": str(saved["local_path"]) if saved.get("local_path") else None,
        "presigned_url": _doc_presigned_url(stored_bucket, output_key),
        "reused": False,
    }


def compose_negotiation_packet(
    db: Session,
    *,
    driver_id: int,
    negotiation_id: int,
    include_full_packet: bool = False,
    force: bool = False,
) -> dict:
    readiness = packet_readiness_for_driver(db, driver_id)
    if not readiness.get("ready"):
        return {
            "ok": False,
            "message": "packet_readiness_required",
            "missing_docs": readiness.get("missing_labels") or [],
            "redirect_url": "/onboarding/step3",
        }

    bol_compose = compose_bol_packet(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        force=force,
    )
    if not bol_compose.get("ok"):
        return bol_compose

    result: dict = {
        "ok": True,
        "message": "bol_packet_composed",
        "included_docs": ["BOL_PACKET"],
        "missing_docs": [],
        "readiness": readiness,
        "bol_packet_bucket": bol_compose.get("bucket"),
        "bol_packet_key": bol_compose.get("file_key"),
        "bol_packet_url": bol_compose.get("presigned_url"),
        "bol_local_path": bol_compose.get("local_path"),
        "full_packet_included": False,
    }

    if not include_full_packet:
        return result

    if not force:
        existing_packet = _active_doc(
            db,
            driver_id=driver_id,
            negotiation_id=negotiation_id,
            doc_type="NEGOTIATION_PACKET",
        )
        if existing_packet:
            existing_included_docs: list[str] = ["BOL_PACKET"]
            ratecon_doc = _active_doc(
                db,
                driver_id=driver_id,
                negotiation_id=negotiation_id,
                doc_type="RATECON",
            )
            if ratecon_doc:
                existing_included_docs.append("RATECON")

            for doc_type in ("W9", "INSURANCE", "AUTHORITY"):
                if driver_doc_map.get(doc_type):
                    existing_included_docs.append(doc_type)

            result.update(
                {
                    "message": "packet_composed",
                    "full_packet_included": True,
                    "included_docs": existing_included_docs,
                    "packet_bucket": existing_packet.get("bucket"),
                    "packet_key": existing_packet.get("file_key"),
                    "local_path": _local_path_for_key(str(existing_packet.get("file_key") or "")),
                    "presigned_url": _doc_presigned_url(existing_packet.get("bucket"), existing_packet.get("file_key")),
                }
            )
            return result

    driver_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=None,
        doc_types=["W9", "INSURANCE", "AUTHORITY"],
    )
    driver_doc_map = {str(doc.get("doc_type") or "").upper(): doc for doc in driver_docs}

    ordered: list[tuple[str, bytes]] = []
    included_docs: list[str] = []

    bol_packet_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=["BOL_PACKET"],
    )
    bol_payload = _read_document_payload(bol_packet_docs[0]) if bol_packet_docs else None
    if bol_payload:
        ordered.append(("BOL_PACKET", bol_payload))
        included_docs.append("BOL_PACKET")

    ratecon_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=["RATECON"],
    )
    if ratecon_docs:
        ratecon_payload = _read_document_payload(ratecon_docs[0])
        if ratecon_payload:
            ordered.append(("RATECON", ratecon_payload))
            included_docs.append("RATECON")

    for doc_type in ("W9", "INSURANCE", "AUTHORITY"):
        payload = _read_document_payload(driver_doc_map.get(doc_type, {}))
        if payload:
            ordered.append((doc_type, payload))
            included_docs.append(doc_type)

    merged = _merge_pdf_bytes(ordered)
    if not merged:
        return {"ok": False, "message": "packet_merge_failed", "included_docs": included_docs}

    packet_key = negotiation_packet_key(driver_id, negotiation_id)
    saved = save_bytes_by_key(packet_key, merged, content_type="application/pdf")
    if not saved["local_saved"] and not saved["spaces_saved"]:
        return {"ok": False, "message": "negotiation_packet_storage_write_failed"}

    stored_key = packet_key if saved["spaces_saved"] else str(saved["local_path"])
    stored_bucket = str(saved["bucket"]) if saved["spaces_saved"] and saved["bucket"] else None

    upsert_driver_document(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_type="NEGOTIATION_PACKET",
        bucket=stored_bucket,
        file_key=stored_key,
        sha256_hash=_sha256_hex(merged),
    )

    presigned_url = _doc_presigned_url(stored_bucket, packet_key)

    result.update(
        {
            "message": "packet_composed",
            "full_packet_included": True,
            "included_docs": ["BOL_PACKET", *[doc for doc in included_docs if doc != "BOL_PACKET"]],
            "packet_bucket": stored_bucket,
            "packet_key": stored_key,
            "local_path": str(saved["local_path"]) if saved.get("local_path") else None,
            "presigned_url": presigned_url,
        }
    )
    return result
