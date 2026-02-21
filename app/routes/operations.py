import os
import base64
import io
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.broker import BrokerEmail
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import Message, Negotiation
from app.services.email import send_quick_reply_email
from app.services.document_registry import upsert_driver_document
from app.services.factoring import send_negotiation_to_factoring
from app.services.ledger import process_load_fees
from app.services.packet_compose import compose_negotiation_packet
from app.services.packet_readiness import packet_readiness_for_driver
from app.services.packet_storage import save_bytes_by_key
from app.services.packet_manager import log_packet_snapshot
from app.services.storage_keys import bol_processed_key, bol_raw_key, ratecon_key


router = APIRouter(tags=["operations"])


def _session_driver(request: Request, db: Session) -> Driver | None:
    session_driver_id = request.session.get("user_id")
    if not session_driver_id:
        return None
    return db.query(Driver).filter(Driver.id == session_driver_id).first()


def _parse_strict_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return default


def _sha256_hex(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _image_to_pdf_bytes(image_bytes: bytes) -> bytes:
    image = ImageReader(io.BytesIO(image_bytes))
    width, height = image.getSize()
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(width, height))
    pdf.drawImage(image, 0, 0, width=width, height=height, preserveAspectRatio=True, anchor="sw")
    pdf.showPage()
    pdf.save()
    output.seek(0)
    return output.read()


def _packet_files_for_driver(driver_id: int) -> list[Path]:
    packet_root = Path(os.getenv("PACKET_STORAGE_ROOT", "/srv/gcd-data/packets"))
    driver_dir = packet_root / f"driver_{driver_id}"
    files = [
        driver_dir / "mc_auth.pdf",
        driver_dir / "coi.pdf",
        driver_dir / "w9.pdf",
    ]
    return [file_path for file_path in files if file_path.exists()]


def _missing_docs_response(request: Request, missing_labels: list[str]):
    accepts_html = "text/html" in (request.headers.get("accept") or "").lower()
    is_hx = request.headers.get("HX-Request") == "true"

    if accepts_html or is_hx:
        missing_text = ", ".join(missing_labels) if missing_labels else "W-9, COI, MC Authority"
        html = (
            '<div class="bg-amber-900/20 border border-amber-500/50 rounded-xl p-4 mb-3">'
            '<div class="text-amber-300 text-xs font-black uppercase tracking-wider">Upload required before booking</div>'
            f'<p class="text-amber-200 text-xs mt-2">Upload {missing_text} to book loads.</p>'
            '<a href="/onboarding/step3" class="mt-3 inline-flex items-center gap-2 bg-amber-600 hover:bg-amber-500 text-white text-xs font-bold px-3 py-2 rounded-lg">'
            '<i class="fas fa-briefcase"></i> Upload docs</a>'
            '</div>'
        )
        return HTMLResponse(status_code=409, content=html)

    return JSONResponse(
        status_code=409,
        content={
            "status": "error",
            "message": "packet_readiness_required",
            "banner": "Upload W-9, COI, and MC Authority to book loads.",
            "redirect_url": "/onboarding/step3",
            "missing_docs": missing_labels,
        },
    )


def _packet_readiness_block(request: Request, db: Session, driver_id: int):
    readiness = packet_readiness_for_driver(db, driver_id)
    if readiness.get("ready"):
        return None

    missing_labels = readiness.get("missing_labels") or []
    return _missing_docs_response(request, missing_labels)


@router.post("/api/negotiations/{negotiation_id}/compose-packet")
async def compose_packet(
    request: Request,
    negotiation_id: int,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return JSONResponse(status_code=404, content={"status": "error", "message": "negotiation_not_found"})

    include_full_raw = request.query_params.get("include_full_packet")
    force_raw = request.query_params.get("force")
    if include_full_raw is None or force_raw is None:
        try:
            form_data = await request.form()
        except Exception:
            form_data = {}
        if include_full_raw is None:
            include_full_raw = form_data.get("include_full_packet")
        if force_raw is None:
            force_raw = form_data.get("force")

    include_full_packet = _parse_strict_bool(str(include_full_raw) if include_full_raw is not None else None, default=False)
    force = _parse_strict_bool(str(force_raw) if force_raw is not None else None, default=False)

    composed = compose_negotiation_packet(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation_id,
        include_full_packet=include_full_packet,
        force=force,
    )
    if not composed.get("ok"):
        if composed.get("message") == "packet_readiness_required":
            return _missing_docs_response(request, list(composed.get("missing_docs") or []))
        return JSONResponse(status_code=400, content={"status": "error", **composed})

    db.commit()
    response_payload = {
        "status": "ok",
        "bol_packet_key": composed.get("bol_packet_key"),
        "bol_packet_bucket": composed.get("bol_packet_bucket"),
        "bol_packet_url": composed.get("bol_packet_url"),
        "full_packet_included": bool(composed.get("full_packet_included")),
        "included_docs": composed.get("included_docs") or [],
        "missing_docs": [],
    }
    if composed.get("full_packet_included"):
        response_payload.update(
            {
                "packet_key": composed.get("packet_key"),
                "packet_bucket": composed.get("packet_bucket"),
                "presigned_url": composed.get("presigned_url"),
            }
        )
    return response_payload


def _resolve_rate_con_file(driver_id: int, stored_path: str) -> Path:
    base_root = Path(os.getenv("RATE_CON_STORAGE_ROOT", "/srv/gcd-data/rate_cons")).resolve()
    driver_root = (base_root / f"driver_{driver_id}").resolve()

    stored_candidate = Path(stored_path)
    if stored_candidate.is_absolute():
        resolved = stored_candidate.resolve()
        if str(resolved).startswith(str(base_root)):
            return resolved
        raise HTTPException(status_code=404, detail="rate_con_not_found")

    resolved = (driver_root / stored_candidate.name).resolve()
    if not str(resolved).startswith(str(driver_root)):
        raise HTTPException(status_code=404, detail="rate_con_not_found")
    return resolved


def _decode_signature_data(signature_data: str) -> bytes:
    if not signature_data or "," not in signature_data:
        raise HTTPException(status_code=400, detail="invalid_signature_payload")
    header, payload = signature_data.split(",", 1)
    if "base64" not in header:
        raise HTTPException(status_code=400, detail="invalid_signature_payload")
    try:
        return base64.b64decode(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_signature_payload") from exc


def _stamp_signature_on_pdf(source_pdf: Path, signature_png_bytes: bytes, output_pdf: Path) -> None:
    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()

    if not reader.pages:
        raise HTTPException(status_code=400, detail="invalid_pdf_document")

    for page in reader.pages[:-1]:
        writer.add_page(page)

    last_page = reader.pages[-1]
    page_width = float(last_page.mediabox.width)
    page_height = float(last_page.mediabox.height)

    overlay_stream = io.BytesIO()
    overlay = canvas.Canvas(overlay_stream, pagesize=(page_width, page_height))

    signature_width = min(220, page_width * 0.35)
    signature_height = 70
    signature_x = max(36, page_width - signature_width - 36)
    signature_y = 72

    overlay.drawImage(
        ImageReader(io.BytesIO(signature_png_bytes)),
        signature_x,
        signature_y,
        width=signature_width,
        height=signature_height,
        mask="auto",
        preserveAspectRatio=True,
        anchor="sw",
    )
    stamp_text = f"Signed via Green Candle AI - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}"
    overlay.setFont("Helvetica", 8)
    overlay.drawString(36, 24, stamp_text)
    overlay.save()

    overlay_stream.seek(0)
    overlay_pdf = PdfReader(overlay_stream)
    last_page.merge_page(overlay_pdf.pages[0])
    writer.add_page(last_page)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as file_handle:
        writer.write(file_handle)


@router.get("/drivers/negotiations/{negotiation_id}/view-rate-con")
async def view_rate_con(
    request: Request,
    negotiation_id: int,
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        raise HTTPException(status_code=401, detail="auth_required")

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation or not negotiation.rate_con_path:
        raise HTTPException(status_code=404, detail="rate_con_not_found")

    rate_con_file = _resolve_rate_con_file(selected_driver.id, negotiation.rate_con_path)
    if not rate_con_file.exists():
        raise HTTPException(status_code=404, detail="rate_con_not_found")

    return FileResponse(path=str(rate_con_file), media_type="application/pdf", filename=rate_con_file.name)


@router.post("/api/negotiations/{negotiation_id}/apply-signature")
async def apply_rate_con_signature(
    request: Request,
    negotiation_id: int,
    signature_data: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation or not negotiation.rate_con_path:
        return {"status": "error", "message": "rate_con_not_found"}

    if negotiation.status not in {"RATE_CON_RECEIVED", "RATE_CON_SIGNED"}:
        return {"status": "error", "message": "invalid_negotiation_state"}

    source_pdf = _resolve_rate_con_file(selected_driver.id, negotiation.rate_con_path)
    if not source_pdf.exists():
        return {"status": "error", "message": "rate_con_not_found"}

    signature_png = _decode_signature_data(signature_data)

    signed_dir = Path(os.getenv("RATE_CON_STORAGE_ROOT", "/srv/gcd-data/rate_cons")) / f"driver_{selected_driver.id}"
    signed_filename = f"signed_neg_{negotiation.id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.pdf"
    signed_pdf = signed_dir / signed_filename

    _stamp_signature_on_pdf(source_pdf, signature_png, signed_pdf)

    signed_bytes = signed_pdf.read_bytes()
    ratecon_storage_key = ratecon_key(selected_driver.id, negotiation.id)
    save_result = save_bytes_by_key(
        ratecon_storage_key,
        signed_bytes,
        content_type="application/pdf",
    )
    if not save_result["local_saved"] and not save_result["spaces_saved"]:
        return {"status": "error", "message": "ratecon_storage_write_failed"}

    stored_key = ratecon_storage_key if save_result["spaces_saved"] else str(save_result["local_path"])
    stored_bucket = str(save_result["bucket"]) if save_result["spaces_saved"] and save_result["bucket"] else None
    upsert_driver_document(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation.id,
        doc_type="RATECON",
        bucket=stored_bucket,
        file_key=stored_key,
        sha256_hash=_sha256_hex(signed_bytes),
    )

    negotiation.rate_con_path = signed_filename
    negotiation.status = "RATE_CON_SIGNED"
    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="System",
            body="✅ RATE CON SIGNED: Signature stamped and contract saved.",
            is_read=True,
        )
    )
    db.commit()

    return {
        "status": "ok",
        "message": "rate_con_signed",
        "signed_file": signed_filename,
    }


@router.post("/api/negotiations/secure-load")
async def secure_load_action(
    request: Request,
    negotiation_id: int = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}

    load = db.query(Load).filter(Load.id == negotiation.load_id).first()
    if not load:
        return {"status": "error", "message": "load_not_found"}

    broker_email_record = (
        db.query(BrokerEmail)
        .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
        .order_by(BrokerEmail.confidence.desc())
        .first()
    )
    if not broker_email_record or not broker_email_record.email:
        return {"status": "error", "message": "broker_email_not_found"}

    composed = compose_negotiation_packet(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation.id,
        include_full_packet=True,
    )
    if not composed.get("ok"):
        if composed.get("message") == "packet_readiness_required":
            return _missing_docs_response(request, list(composed.get("missing_docs") or []))
        return {"status": "error", **composed}

    packet_local_path = composed.get("local_path")
    packet_attachments = [Path(str(packet_local_path))] if packet_local_path else []
    if not packet_attachments or not packet_attachments[0].exists():
        return {"status": "error", "message": "packet_compose_local_missing"}

    load_ref = load.ref_id or str(load.id)
    subject = f"Load #{load_ref} Accepted - Packet Attached"
    body = (
        "Hello,\n\n"
        "We accept this load and are ready to move forward.\n"
        "Our carrier packet is attached (MC Authority, COI, and W9).\n\n"
        "Please send the Rate Con and we will execute immediately.\n\n"
        "Thank you."
    )
    watermark_footer_text = (
        f"Sent via Green Candle Dispatch | Driver: {selected_driver.display_name} "
        f"| MC: {selected_driver.mc_number} "
        f"| {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}"
    )

    try:
        sent = send_quick_reply_email(
            broker_email=broker_email_record.email,
            load_ref=load_ref,
            driver_handle=selected_driver.dispatch_handle or selected_driver.display_name,
            subject=subject,
            body=body,
            attachment_paths=packet_attachments,
            load_source=load.source_platform,
            negotiation_id=negotiation.id,
            watermark_footer_text=watermark_footer_text,
        )
    except Exception:
        sent = False

    if not sent:
        negotiation.status = "CLOSED_PENDING_EMAIL"
        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="System",
                body="⚠️ LOAD CLOSED PENDING EMAIL: SMTP send failed. Retry packet dispatch from ops.",
                is_read=True,
            )
        )
        db.commit()
        return {
            "status": "ok",
            "message": "closed_pending_email_retry_required",
            "email_sent": False,
        }

    log_packet_snapshot(
        db,
        negotiation_id=negotiation.id,
        driver_id=selected_driver.id,
        recipient_email=broker_email_record.email,
        attachment_paths=packet_attachments,
        storage_root=os.getenv("PACKET_STORAGE_ROOT", "/srv/gcd-data/packets"),
    )

    process_load_fees(
        db,
        load_value=load.price or "0",
        driver_id=selected_driver.id,
        negotiation_id=negotiation.id,
    )

    negotiation.status = "CLOSED"
    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="System",
            body="🎯 LOAD SECURED: Packet sent to broker. Waiting for Rate Con.",
            is_read=True,
        )
    )
    db.commit()

    return {
        "status": "ok",
        "message": "load_secured_packet_dispatched",
        "email_sent": True,
    }


@router.post("/api/negotiations/{negotiation_id}/upload-bol")
async def upload_bol(
    request: Request,
    negotiation_id: int,
    bol_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}

    raw_bytes = await bol_file.read()
    if not raw_bytes:
        return {"status": "error", "message": "empty_file"}

    original_name = (bol_file.filename or "bol.bin").strip()
    ext = Path(original_name).suffix.lower() or ".bin"
    content_type = (bol_file.content_type or "application/octet-stream").lower()
    if content_type == "application/pdf" or ext == ".pdf":
        processed_pdf = raw_bytes
    elif content_type.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp"}:
        try:
            processed_pdf = _image_to_pdf_bytes(raw_bytes)
        except Exception:
            return {"status": "error", "message": "image_to_pdf_failed"}
    else:
        return {"status": "error", "message": "unsupported_file_type"}

    raw_key = bol_raw_key(selected_driver.id, negotiation.id)
    processed_key = bol_processed_key(selected_driver.id, negotiation.id)

    raw_save = save_bytes_by_key(
        raw_key,
        raw_bytes,
        content_type=content_type,
    )
    processed_save = save_bytes_by_key(
        processed_key,
        processed_pdf,
        content_type="application/pdf",
    )

    if not raw_save["local_saved"] and not raw_save["spaces_saved"]:
        return {"status": "error", "message": "raw_storage_write_failed"}
    if not processed_save["local_saved"] and not processed_save["spaces_saved"]:
        return {"status": "error", "message": "processed_storage_write_failed"}

    raw_file_key = raw_key if raw_save["spaces_saved"] else str(raw_save["local_path"])
    processed_file_key = processed_key if processed_save["spaces_saved"] else str(processed_save["local_path"])

    raw_bucket = str(raw_save["bucket"]) if raw_save["spaces_saved"] and raw_save["bucket"] else None
    processed_bucket = str(processed_save["bucket"]) if processed_save["spaces_saved"] and processed_save["bucket"] else None

    upsert_driver_document(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation.id,
        doc_type="BOL_RAW",
        bucket=raw_bucket,
        file_key=raw_file_key,
        sha256_hash=_sha256_hex(raw_bytes),
    )
    upsert_driver_document(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation.id,
        doc_type="BOL_PDF",
        bucket=processed_bucket,
        file_key=processed_file_key,
        sha256_hash=_sha256_hex(processed_pdf),
    )
    db.commit()

    return {
        "status": "ok",
        "message": "bol_uploaded",
        "raw_key": raw_file_key,
        "processed_key": processed_file_key,
        "spaces_saved": bool(raw_save["spaces_saved"] and processed_save["spaces_saved"]),
    }


@router.post("/api/negotiations/{negotiation_id}/send-to-factoring")
async def send_to_factoring(
    request: Request,
    negotiation_id: int,
    dry_run: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    composed = compose_negotiation_packet(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation_id,
        include_full_packet=True,
    )
    if not composed.get("ok"):
        if composed.get("message") == "packet_readiness_required":
            return _missing_docs_response(request, list(composed.get("missing_docs") or []))
        return {"status": "error", **composed}

    result = send_negotiation_to_factoring(
        db,
        negotiation_id=negotiation_id,
        driver_id=selected_driver.id,
        dry_run=dry_run,
    )
    if not result.get("ok"):
        return {"status": "error", **result}

    db.add(
        Message(
            negotiation_id=negotiation_id,
            sender="System",
            body="💸 FACTORING PACKET SENT" if not dry_run else "🧪 FACTORING PACKET READY (DRY RUN)",
            is_read=True,
        )
    )
    db.commit()

    return {
        "status": "ok",
        "compose_packet_key": composed.get("packet_key"),
        "compose_packet_bucket": composed.get("packet_bucket"),
        **result,
    }


@router.post("/api/negotiations/retry-secure-email")
async def retry_secure_email(
    request: Request,
    negotiation_id: int = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = _session_driver(request, db)
    if not selected_driver:
        return JSONResponse(status_code=401, content={"status": "error", "message": "auth_required"})

    negotiation = (
        db.query(Negotiation)
        .filter(
            Negotiation.id == negotiation_id,
            Negotiation.driver_id == selected_driver.id,
        )
        .first()
    )
    if not negotiation:
        return {"status": "error", "message": "negotiation_not_found"}
    if negotiation.status != "CLOSED_PENDING_EMAIL":
        return {"status": "error", "message": "retry_not_required_for_this_state"}

    load = db.query(Load).filter(Load.id == negotiation.load_id).first()
    if not load:
        return {"status": "error", "message": "load_not_found"}

    broker_email_record = (
        db.query(BrokerEmail)
        .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
        .order_by(BrokerEmail.confidence.desc())
        .first()
    )
    if not broker_email_record or not broker_email_record.email:
        return {"status": "error", "message": "broker_email_not_found"}

    composed = compose_negotiation_packet(
        db,
        driver_id=selected_driver.id,
        negotiation_id=negotiation.id,
        include_full_packet=True,
    )
    if not composed.get("ok"):
        if composed.get("message") == "packet_readiness_required":
            return _missing_docs_response(request, list(composed.get("missing_docs") or []))
        return {"status": "error", **composed}

    packet_local_path = composed.get("local_path")
    packet_attachments = [Path(str(packet_local_path))] if packet_local_path else []
    if not packet_attachments or not packet_attachments[0].exists():
        return {"status": "error", "message": "packet_compose_local_missing"}

    load_ref = load.ref_id or str(load.id)
    subject = f"Load #{load_ref} Accepted - Packet Attached"
    body = (
        "Hello,\n\n"
        "We accept this load and are ready to move forward.\n"
        "Our carrier packet is attached (MC Authority, COI, and W9).\n\n"
        "Please send the Rate Con and we will execute immediately.\n\n"
        "Thank you."
    )
    watermark_footer_text = (
        f"Sent via Green Candle Dispatch | Driver: {selected_driver.display_name} "
        f"| MC: {selected_driver.mc_number} "
        f"| {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}"
    )

    try:
        sent = send_quick_reply_email(
            broker_email=broker_email_record.email,
            load_ref=load_ref,
            driver_handle=selected_driver.dispatch_handle or selected_driver.display_name,
            subject=subject,
            body=body,
            attachment_paths=packet_attachments,
            load_source=load.source_platform,
            negotiation_id=negotiation.id,
            watermark_footer_text=watermark_footer_text,
        )
    except Exception:
        sent = False

    if sent:
        log_packet_snapshot(
            db,
            negotiation_id=negotiation.id,
            driver_id=selected_driver.id,
            recipient_email=broker_email_record.email,
            attachment_paths=packet_attachments,
            storage_root=os.getenv("PACKET_STORAGE_ROOT", "/srv/gcd-data/packets"),
        )

        process_load_fees(
            db,
            load_value=load.price or "0",
            driver_id=selected_driver.id,
            negotiation_id=negotiation.id,
        )

        negotiation.status = "CLOSED"
        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="System",
                body="✅ RETRY SUCCESSFUL: Packet delivered to broker.",
                is_read=True,
            )
        )
        db.commit()
        return {"status": "ok", "message": "retry_successful", "email_sent": True}

    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="System",
            body="⚠️ RETRY FAILED: SMTP still unavailable. Check mail server and retry.",
            is_read=True,
        )
    )
    db.commit()
    return {"status": "error", "message": "retry_failed_check_smtp_logs", "email_sent": False}
