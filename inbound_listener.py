import asyncio
import email
import imaplib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import getaddresses
from pathlib import Path

from sqlalchemy import func

from app.database import SessionLocal
from app.logic.parser import (
    extract_load_ref_from_subject,
    extract_negotiation_id_from_message,
    extract_routing_from_message,
    normalize_load_ref,
)
from app.logic.negotiator import handle_broker_reply
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import Message, Negotiation


logger = logging.getLogger(__name__)


def _normalize_handle(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_text_body(msg: EmailMessage) -> str:
    if msg.is_multipart():
        parts: list[str] = []
        html_fallback: list[str] = []

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if (part.get("Content-Disposition") or "").lower().startswith("attachment"):
                continue

            payload = part.get_payload(decode=True)
            if payload is None:
                continue

            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace").strip()
            if not decoded:
                continue

            content_type = (part.get_content_type() or "").lower()
            if content_type == "text/plain":
                parts.append(decoded)
            elif content_type == "text/html":
                html_fallback.append(re.sub(r"<[^>]+>", " ", decoded))

        if parts:
            return "\n\n".join(parts).strip()
        if html_fallback:
            return "\n\n".join(html_fallback).strip()
        return ""

    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace").strip()


def _redact_email_value(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        local = match.group(1)
        domain = match.group(2)
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = f"{local[:2]}***"
        return f"{masked_local}@{domain}"

    return re.sub(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", _replace, value or "")


def _redacted_header_snapshot(msg: EmailMessage) -> dict[str, list[str]]:
    snapshot_headers = [
        "Delivered-To",
        "X-Original-To",
        "Envelope-To",
        "To",
        "Cc",
        "From",
        "Subject",
        "In-Reply-To",
        "References",
    ]
    snapshot: dict[str, list[str]] = {}
    for header in snapshot_headers:
        values = msg.get_all(header, [])
        if values:
            snapshot[header] = [_redact_email_value(_decode_mime_header(value)) for value in values]
    return snapshot


def _find_driver_by_handle(db, handle: str) -> Driver | None:
    candidate_handle = (handle or "").strip().lower()
    if not candidate_handle:
        return None

    driver = db.query(Driver).filter(Driver.display_name == candidate_handle).first()
    if driver:
        return driver

    drivers = db.query(Driver).all()
    for candidate in drivers:
        if _normalize_handle(candidate.display_name) == _normalize_handle(candidate_handle):
            return candidate
    return None


def _find_load_by_ref(db, load_ref: str | None) -> Load | None:
    raw_ref = (load_ref or "").strip()
    if not raw_ref:
        return None

    exact = db.query(Load).filter(Load.ref_id == raw_ref).first()
    if exact:
        return exact

    normalized_ref = normalize_load_ref(raw_ref)
    if not normalized_ref:
        return None

    return (
        db.query(Load)
        .filter(
            func.regexp_replace(func.lower(Load.ref_id), "[^a-z0-9]", "", "g") == normalized_ref
        )
        .order_by(Load.id.desc())
        .first()
    )


def _set_manual_review(db, negotiations: list[Negotiation], reason: str) -> None:
    for negotiation in negotiations:
        negotiation.status = "MANUAL_REVIEW"
        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="System",
                body=f"⚠️ MANUAL REVIEW REQUIRED: {reason}",
                is_read=False,
            )
        )
    db.commit()


def _mark_seen(mail: imaplib.IMAP4_SSL, uid: bytes) -> None:
    mail.store(uid, "+FLAGS", "\\Seen")


def _safe_attachment_name(raw_filename: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", raw_filename)
    return cleaned[:180] or "contract.pdf"


def _save_rate_con_attachment(part: EmailMessage, negotiation_id: int, driver_id: int) -> str | None:
    payload = part.get_payload(decode=True)
    if not payload:
        return None

    base_dir = Path(os.getenv("RATE_CON_STORAGE_ROOT", "/srv/gcd-data/rate_cons")) / f"driver_{driver_id}"
    base_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"neg_{negotiation_id}_{uuid.uuid4().hex[:8]}.pdf"
    destination = base_dir / unique_name
    destination.write_bytes(payload)
    return unique_name


def _route_and_store(msg: EmailMessage) -> bool:
    inbound_from = _decode_mime_header(msg.get("From"))
    inbound_subject = _decode_mime_header(msg.get("Subject"))
    inbound_body = _extract_text_body(msg)
    email_domain = (os.getenv("EMAIL_DOMAIN") or "gcdloads.com").strip().lower() or "gcdloads.com"

    routing = extract_routing_from_message(msg, email_domain=email_domain)
    routing_negotiation = extract_negotiation_id_from_message(msg, email_domain=email_domain)
    fallback_subject_load_ref = extract_load_ref_from_subject(inbound_subject)

    with SessionLocal() as db:
        driver: Driver | None = None
        load: Load | None = None
        negotiation: Negotiation | None = None
        handle = ""
        load_ref = ""

        if routing_negotiation:
            negotiation = (
                db.query(Negotiation)
                .filter(Negotiation.id == int(routing_negotiation["negotiation_id"]))
                .first()
            )
            if negotiation:
                driver = db.query(Driver).filter(Driver.id == negotiation.driver_id).first()
                load = db.query(Load).filter(Load.id == negotiation.load_id).first()
                if driver:
                    handle = driver.display_name
                if load:
                    load_ref = load.ref_id or str(load.id)
                logging.info(
                    "Inbound negotiation route matched: negotiation=%s layer=%s",
                    negotiation.id,
                    routing_negotiation.get("layer"),
                )

        if not negotiation and routing:
            handle = (routing.get("driver_handle") or "").strip().lower()
            load_ref = (routing.get("load_ref") or "").strip()
            driver = _find_driver_by_handle(db, handle)
            load = _find_load_by_ref(db, load_ref)

            if driver and load:
                negotiation = (
                    db.query(Negotiation)
                    .filter(
                        Negotiation.driver_id == driver.id,
                        Negotiation.load_id == load.id,
                    )
                    .order_by(Negotiation.id.desc())
                    .first()
                )

        if not negotiation and fallback_subject_load_ref:
            load = _find_load_by_ref(db, fallback_subject_load_ref)
            if load:
                active_candidates = (
                    db.query(Negotiation)
                    .filter(
                        Negotiation.load_id == load.id,
                        Negotiation.status.notin_(["RATE_CON_SIGNED"]),
                    )
                    .order_by(Negotiation.id.desc())
                    .all()
                )

                if len(active_candidates) == 1:
                    negotiation = active_candidates[0]
                    driver = db.query(Driver).filter(Driver.id == negotiation.driver_id).first()
                    if driver:
                        handle = driver.display_name
                    load_ref = load.ref_id or fallback_subject_load_ref
                elif len(active_candidates) > 1:
                    _set_manual_review(
                        db,
                        active_candidates,
                        f"Ambiguous inbound route for subject '{inbound_subject[:120]}' and load ref '{fallback_subject_load_ref}'.",
                    )
                    logging.warning(
                        "Inbound ambiguous routing -> manual review. load_ref=%s candidates=%s snapshot=%s",
                        fallback_subject_load_ref,
                        len(active_candidates),
                        _redacted_header_snapshot(msg),
                    )
                    return True

        if not negotiation or not driver or not load:
            logging.warning(
                "Inbound email skipped: routing unresolved. subject_ref=%s snapshot=%s",
                fallback_subject_load_ref,
                _redacted_header_snapshot(msg),
            )
            return True

        if negotiation.status == "CLOSED":
            for part in msg.walk():
                disposition = (part.get("Content-Disposition") or "").lower()
                filename = part.get_filename()
                if "attachment" not in disposition or not filename:
                    continue
                if not filename.lower().endswith(".pdf"):
                    continue

                saved_filename = _save_rate_con_attachment(part, negotiation.id, driver.id)
                if not saved_filename:
                    continue

                negotiation.status = "RATE_CON_RECEIVED"
                negotiation.rate_con_path = saved_filename
                db.add(
                    Message(
                        negotiation_id=negotiation.id,
                        sender="System",
                        body=f"🚨 RATE CON / CONTRACT RECEIVED: {filename}. Please review and sign.",
                        is_read=False,
                    )
                )
                db.commit()
                logging.info(
                    "Rate con detected: negotiation=%s load_ref=%s file=%s",
                    negotiation.id,
                    load_ref,
                    saved_filename,
                )
                break

        normalized_body = inbound_body.strip() or "[empty message body]"
        stored_body = (
            f"From: {inbound_from}\n"
            f"Subject: {inbound_subject}\n"
            f"To-Token: {handle}+{load_ref}\n\n"
            f"{normalized_body}"
        )

        duplicate = (
            db.query(Message.id)
            .filter(
                Message.negotiation_id == negotiation.id,
                Message.sender == "Broker",
                Message.body == stored_body,
            )
            .first()
        )
        if duplicate:
            logging.info(
                "Inbound duplicate ignored for negotiation=%s handle=%s load=%s",
                negotiation.id,
                handle,
                load_ref,
            )
            return True

        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="Broker",
                body=stored_body,
                is_read=False,
            )
        )
        db.commit()

        auto_enabled = bool(getattr(driver, "auto_negotiate", False))
        blocked_statuses = {"Manual", "CLOSED", "CLOSED_PENDING_EMAIL", "RATE_CON_RECEIVED", "RATE_CON_SIGNED"}
        if auto_enabled and negotiation.status not in blocked_statuses:
            try:
                action = asyncio.run(
                    handle_broker_reply(
                        negotiation=negotiation,
                        broker_message_text=normalized_body,
                        driver_settings=driver,
                        db=db,
                    )
                )
                logging.info(
                    "Auto-negotiation processed: negotiation=%s action=%s",
                    negotiation.id,
                    action,
                )
            except Exception:
                logging.exception("Auto-negotiation failed for negotiation=%s", negotiation.id)

        logging.info(
            "Inbound routed: driver=%s negotiation=%s load_ref=%s",
            handle,
            negotiation.id,
            load_ref,
        )
        return True


def _connect_imap() -> imaplib.IMAP4_SSL:
    imap_host = os.getenv("MXROUTE_IMAP_HOST") or os.getenv("MXROUTE_SMTP_HOST")
    imap_port = int(os.getenv("MXROUTE_IMAP_PORT", "993"))
    imap_user = os.getenv("MXROUTE_IMAP_USER") or os.getenv("MXROUTE_SMTP_USER")
    imap_password = os.getenv("MXROUTE_IMAP_PASSWORD") or os.getenv("MXROUTE_SMTP_PASSWORD")

    if not all([imap_host, imap_user, imap_password]):
        raise RuntimeError(
            "Missing IMAP config. Set MXROUTE_IMAP_HOST/USER/PASSWORD "
            "(or MXROUTE_SMTP_HOST/USER/PASSWORD fallbacks)."
        )

    mail = imaplib.IMAP4_SSL(imap_host, imap_port)
    mail.login(imap_user, imap_password)
    return mail


def listen_for_replies() -> None:
    poll_seconds = int(os.getenv("INBOUND_POLL_SECONDS", "10"))
    reconnect_seconds = int(os.getenv("INBOUND_RECONNECT_SECONDS", "5"))
    mailbox_name = os.getenv("INBOUND_IMAP_MAILBOX", "INBOX")

    mail: imaplib.IMAP4_SSL | None = None
    logger.info("🟢 CLOSER WATCHDOG: Started and polling for broker replies...")

    try:
        while True:
            try:
                if mail is None:
                    mail = _connect_imap()
                    logging.info("Inbound listener connected to IMAP server.")

                mail.select(mailbox_name)
                status, data = mail.search(None, "UNSEEN")
                if status != "OK":
                    raise RuntimeError(f"IMAP search failed: {status}")

                message_uids = data[0].split() if data and data[0] else []
                for uid in message_uids:
                    fetch_status, msg_data = mail.fetch(uid, "(RFC822)")
                    if fetch_status != "OK" or not msg_data or not msg_data[0]:
                        logging.warning("Failed to fetch IMAP message uid=%s", uid)
                        continue

                    raw_email = msg_data[0][1]
                    if not raw_email:
                        continue

                    inbound_message = email.message_from_bytes(raw_email)
                    should_mark_seen = _route_and_store(inbound_message)
                    if should_mark_seen:
                        _mark_seen(mail, uid)

                time.sleep(poll_seconds)
            except (imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
                logging.warning("IMAP connection lost: %s", exc)
                if mail is not None:
                    try:
                        mail.logout()
                    except Exception:
                        pass
                mail = None
                time.sleep(reconnect_seconds)
            except Exception:
                logging.exception("Inbound listener cycle failed.")
                time.sleep(reconnect_seconds)
    except KeyboardInterrupt:
        logger.warning("🔴 CLOSER WATCHDOG: Shutting down. (Check VM memory if unexpected)")
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


if __name__ == "__main__":
    log_level = os.getenv("INBOUND_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    listen_for_replies()