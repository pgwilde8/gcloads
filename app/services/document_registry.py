import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def upsert_driver_document(
    db: Session,
    *,
    driver_id: int,
    doc_type: str,
    file_key: str,
    sha256_hash: str,
    negotiation_id: int | None = None,
    bucket: str | None = None,
    source_version: str | None = None,
) -> int | None:
    existing = db.execute(
        text(
            """
            SELECT id
            FROM driver_documents
            WHERE driver_id = :driver_id
              AND doc_type = :doc_type
              AND COALESCE(negotiation_id, 0) = COALESCE(:negotiation_id, 0)
              AND is_active = TRUE
              AND file_key = :file_key
              AND COALESCE(sha256_hash, '') = :sha256_hash
                            AND COALESCE(source_version, '') = COALESCE(:source_version, '')
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {
            "driver_id": driver_id,
            "doc_type": doc_type,
            "negotiation_id": negotiation_id,
            "file_key": file_key,
            "sha256_hash": sha256_hash,
            "source_version": source_version,
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
              AND COALESCE(negotiation_id, 0) = COALESCE(:negotiation_id, 0)
              AND is_active = TRUE
            """
        ),
        {
            "driver_id": driver_id,
            "doc_type": doc_type,
            "negotiation_id": negotiation_id,
        },
    )

    inserted = db.execute(
        text(
            """
            INSERT INTO driver_documents (
                driver_id,
                negotiation_id,
                doc_type,
                bucket,
                file_key,
                sha256_hash,
                source_version,
                is_active
            )
            VALUES (
                :driver_id,
                :negotiation_id,
                :doc_type,
                :bucket,
                :file_key,
                :sha256_hash,
                :source_version,
                TRUE
            )
            RETURNING id
            """
        ),
        {
            "driver_id": driver_id,
            "negotiation_id": negotiation_id,
            "doc_type": doc_type,
            "bucket": bucket,
            "file_key": file_key,
            "sha256_hash": sha256_hash,
            "source_version": source_version,
        },
    ).first()
    return int(inserted.id) if inserted else None


def deactivate_active_documents(
    db: Session,
    *,
    driver_id: int,
    doc_type: str,
    negotiation_id: int | None = None,
) -> int:
    updated = db.execute(
        text(
            """
            UPDATE driver_documents
            SET is_active = FALSE
            WHERE driver_id = :driver_id
              AND doc_type = :doc_type
              AND COALESCE(negotiation_id, 0) = COALESCE(:negotiation_id, 0)
              AND is_active = TRUE
            """
        ),
        {
            "driver_id": driver_id,
            "doc_type": doc_type,
            "negotiation_id": negotiation_id,
        },
    )
    return int(updated.rowcount or 0)


def get_active_documents(
    db: Session,
    *,
    driver_id: int,
    doc_types: list[str],
    negotiation_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT id, driver_id, negotiation_id, doc_type, bucket, file_key, uploaded_at
                                 , sha256_hash, source_version
            FROM driver_documents
            WHERE driver_id = :driver_id
              AND is_active = TRUE
              AND doc_type = ANY(:doc_types)
              AND (
                    (:negotiation_id IS NULL AND negotiation_id IS NULL)
                    OR negotiation_id = :negotiation_id
                  )
            ORDER BY id DESC
            """
        ),
        {
            "driver_id": driver_id,
            "doc_types": doc_types,
            "negotiation_id": negotiation_id,
        },
    ).mappings().all()

    return [
        {
            "id": int(row["id"]),
            "driver_id": int(row["driver_id"]),
            "negotiation_id": int(row["negotiation_id"]) if row["negotiation_id"] is not None else None,
            "doc_type": str(row["doc_type"]),
            "bucket": str(row["bucket"]) if row["bucket"] else None,
            "file_key": str(row["file_key"]),
            "uploaded_at": row["uploaded_at"].isoformat() if row["uploaded_at"] else None,
            "sha256_hash": str(row["sha256_hash"]) if row["sha256_hash"] else None,
            "source_version": str(row["source_version"]) if row["source_version"] else None,
        }
        for row in rows
    ]


def snapshot_metadata_from_docs(docs: list[dict[str, Any]]) -> str:
    metadata = {
        "doc_ids": [doc["id"] for doc in docs],
        "doc_types": [doc["doc_type"] for doc in docs],
        "file_keys": [doc["file_key"] for doc in docs],
        "buckets": [doc.get("bucket") for doc in docs],
    }
    return json.dumps(metadata)
