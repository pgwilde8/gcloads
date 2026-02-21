import os
import re
from email.message import Message as EmailMessage
from email.utils import getaddresses

try:
    from app.core.config import settings as core_settings
except Exception:
    core_settings = None

ROUTING_ADDRESS_RE = re.compile(r"([a-z0-9._-]+)\+([a-z0-9._-]+)@([a-z0-9.-]+)", re.IGNORECASE)
LOCAL_PART_RE = re.compile(r"^[a-z0-9]{2,32}$", re.IGNORECASE)
SUBJECT_LOAD_RE_PATTERNS = [
    re.compile(r"\bload\s*#?\s*([a-z0-9._-]+)\b", re.IGNORECASE),
    re.compile(r"\bref\s*[:#-]?\s*([a-z0-9._-]+)\b", re.IGNORECASE),
]
SUBJECT_NEGOTIATION_TOKEN_RE = re.compile(r"\[\s*GCD\s*:\s*(\d+)\s*\]", re.IGNORECASE)


def normalize_load_ref(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _plus_local_mode() -> str:
    configured = None
    if core_settings is not None:
        configured = getattr(core_settings, "EMAIL_PLUS_LOCAL_MODE", None)
    raw = (configured or os.getenv("EMAIL_PLUS_LOCAL_MODE") or "dispatch_and_handles").strip().lower()
    if raw in {"dispatch_only", "dispatch_and_handles"}:
        return raw
    return "dispatch_and_handles"


def _is_allowed_local_part(local_part: str) -> bool:
    candidate = (local_part or "").strip().lower()
    if candidate == "dispatch":
        return True
    if _plus_local_mode() == "dispatch_only":
        return False
    return bool(LOCAL_PART_RE.fullmatch(candidate))


def _extract_plus_token_from_address(address: str, email_domain: str) -> dict[str, str] | None:
    match = ROUTING_ADDRESS_RE.search(address or "")
    if not match:
        return None

    local_part, token, domain = match.groups()
    if domain.lower() != (email_domain or "gcdloads.com").lower():
        return None

    local_part = (local_part or "").strip().lower()
    token = (token or "").strip()
    if not local_part or not token:
        return None
    if not _is_allowed_local_part(local_part):
        return None

    return {
        "local_part": local_part,
        "token": token,
    }


def extract_plus_token(address_value: str, email_domain: str = "gcdloads.com") -> dict[str, str] | None:
    for _, address in getaddresses([address_value or ""]):
        parsed = _extract_plus_token_from_address(address, email_domain)
        if parsed:
            return parsed
    return _extract_plus_token_from_address(address_value, email_domain)


def _extract_from_text(value: str, email_domain: str) -> dict[str, str] | None:
    plus_parsed = extract_plus_token(value, email_domain=email_domain)
    if not plus_parsed:
        return None

    return {
        "driver_handle": plus_parsed["local_part"],
        "local_part": plus_parsed["local_part"],
        "load_ref": plus_parsed["token"],
    }


def extract_routing_data(to_address: str, email_domain: str = "gcdloads.com") -> dict[str, str] | None:
    for _, address in getaddresses([to_address or ""]):
        parsed = _extract_from_text(address, email_domain)
        if parsed:
            return parsed
    return _extract_from_text(to_address, email_domain)


def extract_routing_from_message(msg: EmailMessage, email_domain: str = "gcdloads.com") -> dict[str, str] | None:
    header_order = ["Delivered-To", "X-Original-To", "Envelope-To", "To", "Cc", "Reply-To"]

    for header in header_order:
        values = msg.get_all(header, [])
        for value in values:
            parsed = extract_routing_data(value, email_domain=email_domain)
            if parsed:
                return {
                    **parsed,
                    "matched_header": header,
                    "raw_address": value,
                }

    return None


def extract_load_ref_from_subject(subject: str) -> str | None:
    clean_subject = (subject or "").strip()
    if not clean_subject:
        return None

    for pattern in SUBJECT_LOAD_RE_PATTERNS:
        match = pattern.search(clean_subject)
        if match:
            token = (match.group(1) or "").strip()
            if token:
                return token

    return None


def extract_negotiation_id_from_message(
    msg: EmailMessage,
    email_domain: str = "gcdloads.com",
) -> dict[str, str | int] | None:
    plus_route = extract_routing_from_message(msg, email_domain=email_domain)
    plus_token = (plus_route or {}).get("load_ref")
    if plus_token and str(plus_token).isdigit():
        return {
            "negotiation_id": int(str(plus_token)),
            "layer": "plus_tag",
        }

    subject_value = (msg.get("Subject") or "").strip()
    subject_match = SUBJECT_NEGOTIATION_TOKEN_RE.search(subject_value)
    if subject_match:
        return {
            "negotiation_id": int(subject_match.group(1)),
            "layer": "subject_token",
        }

    header_value = (msg.get("X-GCD-Negotiation-ID") or "").strip()
    if header_value.isdigit():
        return {
            "negotiation_id": int(header_value),
            "layer": "x_header",
        }

    return None
