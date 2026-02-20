import json
import hashlib
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

_DOC_TYPE_MAP = {
    "mc_auth.pdf": "AUTHORITY",
    "coi.pdf": "INSURANCE",
    "w9.pdf": "W9",
}


def _normalize_filename(filename: str) -> str:
    return (filename or "").strip().lower()


def _doc_type_for_filename(filename: str) -> str | None:
    return _DOC_TYPE_MAP.get(_normalize_filename(filename))


def _sha256_hex(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _file_key_for_storage(driver_id: int, filename: str, storage_root: str, spaces_saved: bool) -> str:
    if spaces_saved:
        return f"drivers/{driver_id}/packet/{filename}"
    return str((Path(storage_root) / f"driver_{driver_id}" / filename).resolve())


def register_uploaded_packet_document(
    db: Session,
    *,
    driver_id: int,
    filename: str,
    file_bytes: bytes,
    spaces_saved: bool,
    storage_root: str,
) -> int | None:
    doc_type = _doc_type_for_filename(filename)
    if not doc_type:
        return None

    file_key = _file_key_for_storage(driver_id, filename, storage_root, spaces_saved)
    sha256_hash = _sha256_hex(file_bytes)

    existing = db.execute(
        text(
            """
            SELECT id
            FROM driver_documents
            WHERE driver_id = :driver_id
              AND doc_type = :doc_type
              AND is_active = TRUE
              AND file_key = :file_key
              AND COALESCE(sha256_hash, '') = :sha256_hash
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {
            "driver_id": driver_id,
            "doc_type": doc_type,
            "file_key": file_key,
            "sha256_hash": sha256_hash,
        },
    ).first()
    if existing:
        return int(existing.id)

    db.execute(
        text(
            """
            UPDATE driver_documents
            SET is_active = FALSE
            WHERE driver_id = :driver_id
              AND doc_type = :doc_type
              AND is_active = TRUE
            """
        ),
        {
            "driver_id": driver_id,
            "doc_type": doc_type,
        },
    )

    inserted = db.execute(
        text(
            """
            INSERT INTO driver_documents (driver_id, doc_type, file_key, sha256_hash, is_active)
            VALUES (:driver_id, :doc_type, :file_key, :sha256_hash, TRUE)
            RETURNING id
            """
        ),
        {
            "driver_id": driver_id,
            "doc_type": doc_type,
            "file_key": file_key,
            "sha256_hash": sha256_hash,
        },
    ).first()
    return int(inserted.id) if inserted else None


def _read_active_docs(db: Session, driver_id: int) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT id, doc_type, file_key
            FROM driver_documents
            WHERE driver_id = :driver_id
              AND is_active = TRUE
            ORDER BY id ASC
            """
        ),
        {"driver_id": driver_id},
    ).mappings().all()

    return [
        {
            "id": int(row["id"]),
            "doc_type": str(row["doc_type"]),
            "file_key": str(row["file_key"]),
        }
        for row in rows
    ]


def _ensure_docs_from_attachments(
    db: Session,
    *,
    driver_id: int,
    attachment_paths: list[Path],
    storage_root: str,
) -> list[dict]:
    active_docs = _read_active_docs(db, driver_id)
    by_doc_type = {doc["doc_type"]: doc for doc in active_docs}

    for attachment in attachment_paths or []:
        filename = _normalize_filename(attachment.name)
        doc_type = _doc_type_for_filename(filename)
        if not doc_type:
            continue

        try:
            file_bytes = attachment.read_bytes()
        except OSError:
            logger.warning("Packet snapshot skipped unreadable attachment: %s", attachment)
            continue

        file_key = _file_key_for_storage(driver_id, filename, storage_root, spaces_saved=False)
        sha256_hash = _sha256_hex(file_bytes)

        existing = by_doc_type.get(doc_type)
        if existing and existing["file_key"] == file_key:
            continue

        inserted_id = register_uploaded_packet_document(
            db,
            driver_id=driver_id,
            filename=filename,
            file_bytes=file_bytes,
            spaces_saved=False,
            storage_root=storage_root,
        )
        if inserted_id:
            by_doc_type[doc_type] = {
                "id": inserted_id,
                "doc_type": doc_type,
                "file_key": file_key,
                "sha256_hash": sha256_hash,
            }

    return list(by_doc_type.values())


def _next_version_label(db: Session, driver_id: int) -> str:
    count_row = db.execute(
        text("SELECT COUNT(*) AS count_value FROM packet_snapshots WHERE driver_id = :driver_id"),
        {"driver_id": driver_id},
    ).first()
    count_value = int(count_row.count_value) if count_row else 0
    return f"v1.0.{count_value + 1}"


def log_packet_snapshot(
    db: Session,
    *,
    negotiation_id: int | None,
    driver_id: int,
    recipient_email: str,
    attachment_paths: list[Path],
    storage_root: str,
    version_label: str | None = None,
) -> int | None:
    docs = _ensure_docs_from_attachments(
        db,
        driver_id=driver_id,
        attachment_paths=attachment_paths,
        storage_root=storage_root,
    )

    metadata = {
        "doc_ids": [doc["id"] for doc in docs],
        "doc_types": [doc["doc_type"] for doc in docs],
        "file_keys": [doc["file_key"] for doc in docs],
    }

    snapshot_version = version_label or _next_version_label(db, driver_id)

    inserted = db.execute(
        text(
            """
            INSERT INTO packet_snapshots (
                negotiation_id,
                driver_id,
                version_label,
                recipient_email,
                metadata
            )
            VALUES (
                :negotiation_id,
                :driver_id,
                :version_label,
                :recipient_email,
                CAST(:metadata AS JSONB)
            )
            RETURNING id
            """
        ),
        {
            "negotiation_id": negotiation_id,
            "driver_id": driver_id,
            "version_label": snapshot_version,
            "recipient_email": recipient_email,
            "metadata": json.dumps(metadata),
        },
    ).first()

    return int(inserted.id) if inserted else None
