import os
import base64
import io
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse
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
from app.services.ledger import process_load_fees
from app.services.packet_manager import log_packet_snapshot


router = APIRouter(tags=["operations"])


def _packet_files_for_driver(driver_id: int) -> list[Path]:
    packet_root = Path(os.getenv("PACKET_STORAGE_ROOT", "/srv/gcd-data/packets"))
    driver_dir = packet_root / f"driver_{driver_id}"
    files = [
        driver_dir / "mc_auth.pdf",
        driver_dir / "coi.pdf",
        driver_dir / "w9.pdf",
    ]
    return [file_path for file_path in files if file_path.exists()]


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
    negotiation_id: int,
    email: str,
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        raise HTTPException(status_code=404, detail="driver_not_found")

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
    negotiation_id: int,
    email: str = Form(...),
    signature_data: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

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
    negotiation_id: int = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

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

    packet_attachments = _packet_files_for_driver(selected_driver.id)
    if len(packet_attachments) < 3:
        return {"status": "error", "message": "packet_files_missing"}

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
            driver_handle=selected_driver.display_name,
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


@router.post("/api/negotiations/retry-secure-email")
async def retry_secure_email(
    negotiation_id: int = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    selected_driver = db.query(Driver).filter(Driver.email == email.strip().lower()).first()
    if not selected_driver:
        return {"status": "error", "message": "driver_not_found"}

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

    packet_attachments = _packet_files_for_driver(selected_driver.id)
    if len(packet_attachments) < 3:
        return {"status": "error", "message": "packet_files_missing"}

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
            driver_handle=selected_driver.display_name,
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
