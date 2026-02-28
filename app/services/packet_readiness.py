from sqlalchemy.orm import Session

from app.services.document_registry import get_active_documents


REQUIRED_PACKET_DOCS: tuple[tuple[str, str], ...] = (
    ("w9", "W-9"),
    ("coi", "COI"),
    ("mc_auth", "MC Auth"),
    ("voided_check", "Voided Check"),
)

_DOC_TYPE_TO_KEY = {
    "W9": "w9",
    "INSURANCE": "coi",
    "AUTHORITY": "mc_auth",
    "VOIDED_CHECK": "voided_check",
}


def _readiness_from_uploaded_keys(uploaded_keys: set[str]) -> dict:
    docs = [
        {
            "key": key,
            "label": label,
            "present": key in uploaded_keys,
        }
        for key, label in REQUIRED_PACKET_DOCS
    ]

    uploaded_count = sum(1 for item in docs if item["present"])
    required_count = len(REQUIRED_PACKET_DOCS)
    missing_labels = [item["label"] for item in docs if not item["present"]]
    missing_keys = [item["key"] for item in docs if not item["present"]]

    return {
        "docs": docs,
        "uploaded_count": uploaded_count,
        "required_count": required_count,
        "is_ready": uploaded_count == required_count,
        "ready": uploaded_count == required_count,
        "uploaded": sorted(uploaded_keys),
        "missing": missing_keys,
        "missing_labels": missing_labels,
    }


def packet_readiness_for_uploaded(uploaded_docs: set[str] | list[str] | tuple[str, ...]) -> dict:
    uploaded = {str(doc).strip().lower() for doc in (uploaded_docs or []) if str(doc).strip()}
    return _readiness_from_uploaded_keys(uploaded)


def packet_readiness_for_driver(db: Session, driver_id: int) -> dict:
    docs = get_active_documents(
        db,
        driver_id=driver_id,
        doc_types=["W9", "INSURANCE", "AUTHORITY", "VOIDED_CHECK"],
        negotiation_id=None,
    )
    uploaded_keys: set[str] = set()
    for document in docs:
        key = _DOC_TYPE_TO_KEY.get((document.get("doc_type") or "").strip().upper())
        if key:
            uploaded_keys.add(key)
    return _readiness_from_uploaded_keys(uploaded_keys)
