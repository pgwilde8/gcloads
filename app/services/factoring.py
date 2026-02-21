import re
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings as core_settings
from app.services.document_registry import get_active_documents
from app.services.packet_storage import generate_presigned_get_url


def _money_to_float(raw_value: Any) -> float:
    if raw_value is None:
        return 0.0
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(raw_value))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _attachment_url(bucket: str | None, file_key: str) -> str | None:
    if bucket and file_key and not file_key.startswith("/"):
        return generate_presigned_get_url(bucket, file_key)
    return None


def _factoring_auth_headers() -> dict[str, str]:
    api_key = (core_settings.FACTORING_API_KEY or "").strip()
    auth_header = (core_settings.FACTORING_API_AUTH_HEADER or "Authorization").strip()
    auth_scheme = (core_settings.FACTORING_API_AUTH_SCHEME or "Bearer").strip()

    if not api_key:
        return {}

    if auth_header.lower() == "authorization":
        value = f"{auth_scheme} {api_key}" if auth_scheme else api_key
    else:
        value = api_key

    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        auth_header: value,
    }


def _post_to_factoring_api(payload: dict[str, Any]) -> dict[str, Any]:
    api_url = (core_settings.FACTORING_API_URL or "").strip()
    if not api_url:
        return {"ok": False, "message": "factoring_api_url_not_configured"}

    headers = _factoring_auth_headers()
    if not headers:
        return {"ok": False, "message": "factoring_api_key_not_configured"}

    timeout_seconds = max(int(core_settings.FACTORING_API_TIMEOUT_SECONDS or 20), 1)

    try:
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "message": "factoring_api_request_failed",
            "error": str(exc),
        }

    response_body: Any
    try:
        response_body = response.json()
    except ValueError:
        response_body = {"raw": response.text[:1000]}

    if response.status_code < 200 or response.status_code >= 300:
        return {
            "ok": False,
            "message": "factoring_api_rejected_request",
            "http_status": response.status_code,
            "response": response_body,
        }

    external_id = None
    if isinstance(response_body, dict):
        external_id = response_body.get("id") or response_body.get("transaction_id") or response_body.get("invoice_id")

    return {
        "ok": True,
        "http_status": response.status_code,
        "response": response_body,
        "external_id": external_id,
    }


def send_negotiation_to_factoring(
    db: Session,
    *,
    negotiation_id: int,
    driver_id: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    negotiation_row = db.execute(
        text(
            """
            SELECT n.id, n.driver_id, n.load_id, n.status, n.current_offer, n.factoring_status,
                   l.ref_id, l.origin, l.destination, l.price
            FROM negotiations n
            JOIN loads l ON l.id = n.load_id
            WHERE n.id = :negotiation_id
              AND n.driver_id = :driver_id
            LIMIT 1
            """
        ),
        {
            "negotiation_id": negotiation_id,
            "driver_id": driver_id,
        },
    ).mappings().first()
    if not negotiation_row:
        return {"ok": False, "message": "negotiation_not_found"}

    if (negotiation_row.get("factoring_status") or "").upper() == "SENT":
        return {"ok": True, "message": "already_sent_to_factoring", "attachments": []}

    bol_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=["BOL_PDF"],
    )
    if not bol_docs:
        return {"ok": False, "message": "upload_bol_first"}

    packet_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=None,
        doc_types=["W9", "INSURANCE", "AUTHORITY"],
    )
    packet_map = {doc["doc_type"]: doc for doc in packet_docs}
    missing_packet_types = [doc_type for doc_type in ("W9", "INSURANCE", "AUTHORITY") if doc_type not in packet_map]
    if missing_packet_types:
        return {
            "ok": False,
            "message": "packet_files_missing",
            "missing": missing_packet_types,
        }

    ratecon_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=["RATECON"],
    )

    attachments: list[dict[str, str]] = []

    composed_packet_docs = get_active_documents(
        db,
        driver_id=driver_id,
        negotiation_id=negotiation_id,
        doc_types=["NEGOTIATION_PACKET", "FACTOR_PACKET"],
    )
    if composed_packet_docs:
        packet_url = _attachment_url(composed_packet_docs[0].get("bucket"), composed_packet_docs[0]["file_key"])
        if packet_url:
            attachments.append({"type": "PACKET", "url": packet_url, "note": "Composed negotiation packet"})
            ratecon_docs = []
            packet_map = {}

    bol_url = _attachment_url(bol_docs[0].get("bucket"), bol_docs[0]["file_key"])
    if not bol_url:
        return {"ok": False, "message": "bol_not_accessible_in_spaces"}
    if not attachments:
        attachments.append({"type": "BOL", "url": bol_url, "note": "Processed bill of lading"})

    if not attachments or attachments[0].get("type") != "PACKET":
        for packet_doc_type in ("W9", "INSURANCE", "AUTHORITY"):
            packet_doc = packet_map[packet_doc_type]
            url = _attachment_url(packet_doc.get("bucket"), packet_doc["file_key"])
            if not url:
                return {"ok": False, "message": f"{packet_doc_type.lower()}_not_accessible_in_spaces"}
            attachments.append({"type": packet_doc_type, "url": url, "note": "Carrier packet document"})

    if ratecon_docs:
        ratecon_url = _attachment_url(ratecon_docs[0].get("bucket"), ratecon_docs[0]["file_key"])
        if ratecon_url:
            attachments.append({"type": "RATECON", "url": ratecon_url, "note": "Rate confirmation"})

    final_rate = _money_to_float(negotiation_row.get("current_offer") or negotiation_row.get("price"))
    if final_rate <= 0:
        return {"ok": False, "message": "invalid_load_rate"}

    dispatch_fee = round(final_rate * core_settings.DISPATCH_FEE_RATE, 2)
    payload = {
        "invoice_date": datetime.now(timezone.utc).isoformat(),
        "reference_number": negotiation_row.get("ref_id") or str(negotiation_row["load_id"]),
        "debtor": {
            "name": "Broker",
            "mc_number": "UNKNOWN",
        },
        "items": [
            {
                "description": f"Freight Charge - {negotiation_row.get('origin') or 'Unknown'} to {negotiation_row.get('destination') or 'Unknown'}",
                "amount": final_rate,
                "quantity": 1,
            }
        ],
        "attachments": attachments,
        "payment_instructions": {
            "carrier_payout": round(final_rate - dispatch_fee, 2),
            "dispatch_fee_deduction": dispatch_fee,
            "remit_fee_to": "Green Candle Dispatch LLC",
        },
    }

    if not dry_run:
        factoring_result = _post_to_factoring_api(payload)
        if not factoring_result.get("ok"):
            return {
                "ok": False,
                "message": factoring_result.get("message", "factoring_api_error"),
                "error": factoring_result,
                "payload": payload,
            }

        db.execute(
            text(
                """
                UPDATE negotiations
                SET factoring_status = 'SENT',
                    factored_at = NOW(),
                    updated_at = NOW()
                WHERE id = :negotiation_id
                  AND driver_id = :driver_id
                """
            ),
            {
                "negotiation_id": negotiation_id,
                "driver_id": driver_id,
            },
        )

        return {
            "ok": True,
            "message": "packet_sent_to_factoring",
            "payload": payload,
            "attachments": attachments,
            "factoring_response": factoring_result.get("response"),
            "factoring_http_status": factoring_result.get("http_status"),
            "factoring_external_id": factoring_result.get("external_id"),
        }

    return {
        "ok": True,
        "message": "dry_run_packet_ready",
        "payload": payload,
        "attachments": attachments,
    }
