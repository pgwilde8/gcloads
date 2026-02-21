import asyncio
import io
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from app.core.config import settings


SOURCE_TAG_ALIASES = {
    "dat_one": "dat",
    "datpower": "dat",
    "dat_power": "dat",
    "datloadboard": "dat",
    "truckstop_pro": "truckstop",
    "truckstoppro": "truckstop",
}


def _normalize_sender_handle(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower()) or "dispatch"


def _normalize_source_tag(load_source: str | None) -> str | None:
    raw = (load_source or "").strip().lower()
    if not raw:
        return None
    mapped = SOURCE_TAG_ALIASES.get(raw, raw)
    normalized = re.sub(r"[^a-z0-9]", "", mapped)
    return normalized or None


def add_load_board_tag(email: str, load_source: str | None = None) -> str:
    if "@" not in (email or ""):
        return email

    local, domain = email.rsplit("@", 1)
    base_local = local.split("+", 1)[0]

    tag = _normalize_source_tag(load_source)
    if not tag:
        return f"{base_local}@{domain}"
    return f"{base_local}+{tag}@{domain}"


def _append_subject_token(subject: str, negotiation_id: int | None) -> str:
    if not negotiation_id:
        return subject
    token = f"[GCD:{negotiation_id}]"
    if token in (subject or ""):
        return subject
    return f"{(subject or '').rstrip()} {token}".strip()


def _build_sender_token(
    *,
    email_domain: str,
    driver_handle: str,
    load_ref: str | None = None,
    negotiation_id: int | None = None,
) -> str:
    if negotiation_id:
        return f"dispatch+{negotiation_id}@{email_domain}"

    clean_handle = _normalize_sender_handle(driver_handle)
    safe_load_ref = (load_ref or "").strip()
    return f"{clean_handle}+{safe_load_ref}@{email_domain}"


def _add_pdf_footer_watermark(file_bytes: bytes, footer_text: str) -> bytes:
    if not footer_text:
        return file_bytes

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        return file_bytes

    writer = PdfWriter()

    for page in reader.pages:
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        overlay_stream = io.BytesIO()
        overlay_canvas = canvas.Canvas(overlay_stream, pagesize=(page_width, page_height))
        overlay_canvas.setFont("Helvetica", 8)
        overlay_canvas.drawString(24, 18, footer_text[:220])
        overlay_canvas.save()

        overlay_stream.seek(0)
        overlay_pdf = PdfReader(overlay_stream)
        if overlay_pdf.pages:
            page.merge_page(overlay_pdf.pages[0])
        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def send_negotiation_email(
    broker_email: str,
    load_id: str,
    origin: str,
    destination: str,
    driver_handle: str,
    load_source: str | None = None,
) -> None:
    smtp_host = os.getenv("MXROUTE_SMTP_HOST") or os.getenv("EMAIL_HOST")
    smtp_port = int(os.getenv("MXROUTE_SMTP_PORT") or os.getenv("EMAIL_PORT") or "465")
    smtp_user = os.getenv("MXROUTE_SMTP_USER") or os.getenv("EMAIL_USER")
    smtp_password = os.getenv("MXROUTE_SMTP_PASSWORD") or os.getenv("EMAIL_PASS")
    email_domain = os.getenv("EMAIL_DOMAIN", "gcdloads.com")

    if not all([smtp_host, smtp_user, smtp_password, broker_email]):
        return

    clean_handle = _normalize_sender_handle(driver_handle)
    from_token = f"{clean_handle}+{load_id}@{email_domain}"
    tagged_broker_email = add_load_board_tag(broker_email, load_source)

    message = EmailMessage()
    message["Subject"] = f"Load Inquiry #{load_id}"
    message["From"] = from_token
    message["To"] = tagged_broker_email
    message["Reply-To"] = from_token
    message.set_content(
        (
            f"Hello,\n\n"
            f"We have capacity for your load #{load_id} from {origin} to {destination}.\n"
            f"Is it still available?\n\n"
            f"Thank you."
        )
    )

    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as client:
        client.login(smtp_user, smtp_password)
        client.send_message(message, from_addr=smtp_user, to_addrs=[tagged_broker_email])


def send_quick_reply_email(
    broker_email: str,
    load_ref: str,
    driver_handle: str,
    subject: str,
    body: str,
    attachment_paths: list[Path] | None = None,
    load_source: str | None = None,
    negotiation_id: int | None = None,
    watermark_footer_text: str | None = None,
) -> bool:
    smtp_host = os.getenv("MXROUTE_SMTP_HOST") or os.getenv("EMAIL_HOST")
    smtp_port = int(os.getenv("MXROUTE_SMTP_PORT") or os.getenv("EMAIL_PORT") or "465")
    smtp_user = os.getenv("MXROUTE_SMTP_USER") or os.getenv("EMAIL_USER")
    smtp_password = os.getenv("MXROUTE_SMTP_PASSWORD") or os.getenv("EMAIL_PASS")
    email_domain = os.getenv("EMAIL_DOMAIN", "gcdloads.com")

    if not all([smtp_host, smtp_user, smtp_password, broker_email, driver_handle, load_ref]):
        return False

    from_token = _build_sender_token(
        email_domain=email_domain,
        driver_handle=driver_handle,
        load_ref=load_ref,
        negotiation_id=negotiation_id,
    )
    tagged_broker_email = add_load_board_tag(broker_email, load_source)

    message = EmailMessage()
    message["Subject"] = _append_subject_token(subject, negotiation_id)
    message["From"] = from_token
    message["To"] = tagged_broker_email
    message["Reply-To"] = from_token
    if negotiation_id:
        message["X-GCD-Negotiation-ID"] = str(negotiation_id)
    message["X-GCD-Load-Ref"] = str(load_ref)
    if load_source:
        message["X-GCD-Load-Source"] = _normalize_source_tag(load_source) or str(load_source)
    message.set_content(body)

    for attachment_path in attachment_paths or []:
        try:
            file_bytes = attachment_path.read_bytes()
        except OSError:
            continue

        if settings.WATERMARK_ENABLED and watermark_footer_text and attachment_path.suffix.lower() == ".pdf":
            file_bytes = _add_pdf_footer_watermark(file_bytes, watermark_footer_text)

        message.add_attachment(
            file_bytes,
            maintype="application",
            subtype="pdf",
            filename=attachment_path.name,
        )

    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as client:
        client.login(smtp_user, smtp_password)
        client.send_message(message, from_addr=smtp_user, to_addrs=[tagged_broker_email])

    return True


async def send_outbound_email(
    recipient: str,
    subject: str,
    body: str,
    load_ref: str,
    driver_handle: str,
    attachment_paths: list[Path] | None = None,
    load_source: str | None = None,
    negotiation_id: int | None = None,
) -> bool:
    return await asyncio.to_thread(
        send_quick_reply_email,
        broker_email=recipient,
        load_ref=load_ref,
        driver_handle=driver_handle,
        subject=subject,
        body=body,
        attachment_paths=attachment_paths,
        load_source=load_source,
        negotiation_id=negotiation_id,
    )


def send_magic_link_email(to_email: str, verify_url: str) -> bool:
    smtp_host = os.getenv("MXROUTE_SMTP_HOST") or os.getenv("EMAIL_HOST")
    smtp_port = int(os.getenv("MXROUTE_SMTP_PORT") or os.getenv("EMAIL_PORT") or "465")
    smtp_user = os.getenv("MXROUTE_SMTP_USER") or os.getenv("EMAIL_USER")
    smtp_password = os.getenv("MXROUTE_SMTP_PASSWORD") or os.getenv("EMAIL_PASS")
    email_domain = os.getenv("EMAIL_DOMAIN", "gcdloads.com")

    if not all([smtp_host, smtp_user, smtp_password, to_email, verify_url]):
        return False

    message = EmailMessage()
    message["Subject"] = "Your Green Candle sign-in link"
    message["From"] = f"dispatch@{email_domain}"
    message["To"] = to_email
    message.set_content(
        (
            "Welcome to Green Candle Dispatch.\n\n"
            "Use this secure sign-in link (expires soon):\n"
            f"{verify_url}\n\n"
            "If you did not request this, you can ignore this email."
        )
    )

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as client:
            client.login(smtp_user, smtp_password)
            client.send_message(message, from_addr=smtp_user, to_addrs=[to_email])
    except Exception:
        return False

    return True


def send_century_referral_email(
    *,
    to_email: str,
    full_name: str,
    email: str,
    cell_phone: str,
    mc_number: str,
    dot_number: str,
    number_of_trucks: str,
    secondary_phone: str | None = None,
    company_name: str | None = None,
    interested_fuel_card: bool = False,
    estimated_monthly_volume: str | None = None,
    current_factoring_company: str | None = None,
    preferred_funding_speed: str | None = None,
) -> bool:
    smtp_host = os.getenv("MXROUTE_SMTP_HOST") or os.getenv("EMAIL_HOST")
    smtp_port = int(os.getenv("MXROUTE_SMTP_PORT") or os.getenv("EMAIL_PORT") or "465")
    smtp_user = os.getenv("MXROUTE_SMTP_USER") or os.getenv("EMAIL_USER")
    smtp_password = os.getenv("MXROUTE_SMTP_PASSWORD") or os.getenv("EMAIL_PASS")
    email_domain = os.getenv("EMAIL_DOMAIN", "gcdloads.com")

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return False

    message = EmailMessage()
    message["Subject"] = f"New Century Referral - MC# {mc_number} DOT# {dot_number}"
    message["From"] = f"dispatch@{email_domain}"
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "New Century Finance referral submitted:",
                "",
                f"Full Name: {full_name}",
                f"Email: {email}",
                f"Cell Phone: {cell_phone}",
                f"Secondary Phone: {secondary_phone or ''}",
                f"Company Name: {company_name or ''}",
                f"MC Number: {mc_number}",
                f"DOT Number: {dot_number}",
                f"Number of Trucks: {number_of_trucks}",
                f"Interested in Fuel Card: {'Yes' if interested_fuel_card else 'No'}",
                f"Estimated Monthly Volume: {estimated_monthly_volume or ''}",
                f"Current Factoring Company: {current_factoring_company or ''}",
                f"Preferred Funding Speed: {preferred_funding_speed or ''}",
            ]
        )
    )

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as client:
            client.login(smtp_user, smtp_password)
            client.send_message(message, from_addr=smtp_user, to_addrs=[to_email])
    except Exception:
        return False

    return True


def send_century_approval_email(*, to_email: str, driver_name: str | None = None) -> bool:
    smtp_host = os.getenv("MXROUTE_SMTP_HOST") or os.getenv("EMAIL_HOST")
    smtp_port = int(os.getenv("MXROUTE_SMTP_PORT") or os.getenv("EMAIL_PORT") or "465")
    smtp_user = os.getenv("MXROUTE_SMTP_USER") or os.getenv("EMAIL_USER")
    smtp_password = os.getenv("MXROUTE_SMTP_PASSWORD") or os.getenv("EMAIL_PASS")
    email_domain = os.getenv("EMAIL_DOMAIN", "gcdloads.com")

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return False

    message = EmailMessage()
    message["Subject"] = "Century approval complete - your Green Candle account is active"
    message["From"] = f"dispatch@{email_domain}"
    message["To"] = to_email
    message.set_content(
        (
            f"Hi {(driver_name or 'Driver').strip()},\n\n"
            "Your Century setup has been approved and your account is now active.\n"
            "Log in with your magic link to continue dispatching.\n\n"
            "Green Candle Dispatch"
        )
    )

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as client:
            client.login(smtp_user, smtp_password)
            client.send_message(message, from_addr=smtp_user, to_addrs=[to_email])
    except Exception:
        return False

    return True
