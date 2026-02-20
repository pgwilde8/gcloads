import json
import os
import re

from app.models.broker import BrokerEmail
from app.models.load import Load
from app.models.operations import Message
from app.services.email import send_outbound_email

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None


def _format_currency(value):
    if value is None or value == "":
        return "negotiable"
    try:
        clean_value = str(value).replace("$", "").replace(",", "").strip()
        return f"${float(clean_value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _extract_json_object(payload: str) -> dict | None:
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", payload)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _counter_target_from_floor(absolute_floor: float) -> float:
    target = absolute_floor * 1.08
    return round(target / 50) * 50


def _absolute_floor(negotiation, driver_settings, broker_price_detected: float) -> float:
    min_cpm = float(getattr(driver_settings, "min_cpm", 0) or 0)
    min_flat_rate = float(getattr(driver_settings, "min_flat_rate", 0) or 0)
    distance_miles = float(getattr(negotiation, "distance_miles", 0) or 0)

    cpm_floor = min_cpm * distance_miles
    absolute_floor = max(min_flat_rate, cpm_floor)
    if absolute_floor <= 0:
        absolute_floor = float(broker_price_detected) * 1.10
    return absolute_floor


def _enforce_decision_guardrails(negotiation, driver_settings, broker_price_detected: float, decision: dict | None) -> dict:
    safe = dict(decision or {})
    action = str(safe.get("action") or "SEND_COUNTER").upper()
    if action not in {"SEND_COUNTER", "WALK_AWAY", "FINALIZE"}:
        action = "SEND_COUNTER"

    absolute_floor = _absolute_floor(negotiation, driver_settings, broker_price_detected)

    if action == "WALK_AWAY":
        return {
            "action": "WALK_AWAY",
            "template": "polite_decline",
        }

    raw_price = safe.get("price")
    try:
        parsed_price = float(str(raw_price).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        parsed_price = _counter_target_from_floor(absolute_floor)

    floor_bounded = max(parsed_price, absolute_floor)
    rounded_price = round(floor_bounded / 50) * 50
    if rounded_price <= 0:
        rounded_price = round(absolute_floor / 50) * 50

    template = str(safe.get("template") or "standard_negotiation")
    if template not in {"close_the_deal", "standard_negotiation", "polite_decline"}:
        template = "standard_negotiation"

    if action == "FINALIZE":
        template = "close_the_deal"

    return {
        "action": "SEND_COUNTER",
        "price": rounded_price,
        "template": template,
    }


async def resolve_ai_decision(negotiation, broker_message_text: str, broker_price_detected: float, driver_settings) -> dict | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or AsyncOpenAI is None:
        return None

    load_ref = str(getattr(negotiation, "load_id", "") or "")
    min_cpm = float(getattr(driver_settings, "min_cpm", 0) or 0)
    min_flat_rate = float(getattr(driver_settings, "min_flat_rate", 0) or 0)

    system_prompt = (
        "You are the Lead Dispatcher for Green Candle Dispatch. "
        "Style: professional, brief, firm. "
        "Hard rules: never go below the provided floor constraints, always include load reference in email body, "
        "format currency as $X,XXX. "
        "Return only valid JSON with keys: action, price, template, email_body."
    )
    user_prompt = (
        f"Broker message: {broker_message_text}\n"
        f"Detected broker price: {broker_price_detected}\n"
        f"Load reference: {load_ref}\n"
        f"Driver min_cpm: {min_cpm}\n"
        f"Driver min_flat_rate: {min_flat_rate}\n"
        "Choose action from SEND_COUNTER, WALK_AWAY, FINALIZE. "
        "template from close_the_deal, standard_negotiation, polite_decline."
    )

    client = AsyncOpenAI(api_key=api_key)
    try:
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception:
        return None

    content = ""
    try:
        content = response.choices[0].message.content or ""
    except Exception:
        return None
    return _extract_json_object(content)


def generate_negotiation_email(negotiation, decision):
    """
    Build a dispatcher-style negotiation email body from strategy output.

    decision is expected to come from process_negotiation_logic and include:
    - template: close_the_deal | standard_negotiation | polite_decline
    - price: numeric (for counter flows)
    """
    template = (decision or {}).get("template", "standard_negotiation")
    price = (decision or {}).get("price")
    formatted_price = _format_currency(price)

    lane = ""
    if getattr(negotiation, "origin", None) and getattr(negotiation, "destination", None):
        lane = f" {negotiation.origin} to {negotiation.destination}"

    load_ref = getattr(negotiation, "load_id", None) or getattr(negotiation, "id", "")
    intro = f"Hi, this is dispatch checking in on load {load_ref}{lane}."

    if template == "close_the_deal":
        return (
            f"{intro}\n\n"
            f"If you can do {formatted_price} all-in, we can lock this in now and get moving.\n"
            f"Send over your confirmation and we'll finalize right away."
        )

    if template == "polite_decline":
        return (
            f"{intro}\n\n"
            "We appreciate the update, but we're too far apart on rate to make this one work.\n"
            "If your number comes up, send it over and we'll take another look."
        )

    return (
        f"{intro}\n\n"
        f"We're interested and can do this for {formatted_price} all-in.\n"
        "If that works for you, we'll lock it down now."
    )


def extract_price_from_text(text: str, ignored_values: set[float] | None = None) -> float | None:
    if not text:
        return None

    normalized = text.lower()
    candidates: list[float] = []

    for match in re.finditer(r"\$\s*(\d{1,3}(?:,\d{3})+|\d{3,5})(?:\.\d{1,2})?", normalized):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if 300 <= value <= 20000:
            candidates.append(value)

    for match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*k\b", normalized):
        try:
            value = float(match.group(1)) * 1000
        except ValueError:
            continue
        if 300 <= value <= 20000:
            candidates.append(value)

    for match in re.finditer(r"\b(\d{1,3})\s*hundred\b", normalized):
        try:
            value = float(match.group(1)) * 100
        except ValueError:
            continue
        if 300 <= value <= 20000:
            candidates.append(value)

    for match in re.finditer(r"\b(?:at|for|do|offer|offering|rate)\s*\$?\s*(\d{3,5})\b", normalized):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 300 <= value <= 20000:
            candidates.append(value)

    if ignored_values:
        candidates = [value for value in candidates if value not in ignored_values]

    if not candidates:
        return None

    return max(candidates)


async def handle_broker_reply(negotiation, broker_message_text, driver_settings, db):
    """
    Orchestrator: parse broker rate -> decide move -> generate response -> send -> log.
    """
    load = db.query(Load).filter(Load.id == negotiation.load_id).first()
    load_ref = str(getattr(load, "ref_id", None) or negotiation.load_id)

    ignored_values: set[float] = set()
    numeric_load_ref = re.sub(r"[^0-9]", "", load_ref)
    if numeric_load_ref:
        try:
            candidate = float(numeric_load_ref)
            if 300 <= candidate <= 20000:
                ignored_values.add(candidate)
        except ValueError:
            pass

    detected_price = extract_price_from_text(broker_message_text, ignored_values=ignored_values)
    if detected_price is None:
        return "WAITING_FOR_HUMAN"

    use_ai_negotiation = bool(os.getenv("OPENAI_API_KEY", "").strip())
    ai_payload = await resolve_ai_decision(negotiation, broker_message_text, detected_price, driver_settings) if use_ai_negotiation else None

    decision = process_negotiation_logic(negotiation, detected_price, driver_settings)

    if ai_payload:
        email_content = str(ai_payload.get("email_body") or "").strip()
        if not email_content:
            email_content = generate_negotiation_email(negotiation, decision)
    else:
        email_content = generate_negotiation_email(negotiation, decision)

    if not decision.get("log"):
        absolute_floor = _absolute_floor(negotiation, driver_settings, detected_price)
        ratio = detected_price / absolute_floor if absolute_floor > 0 else 0.0
        if decision.get("action") == "WALK_AWAY":
            decision["log"] = (
                f"RED ZONE (ratio {ratio:.2f}): "
                f"Offer {_format_currency(detected_price)} too far below "
                f"floor {_format_currency(absolute_floor)}. Walking away."
            )
        elif decision.get("template") == "close_the_deal":
            decision["log"] = (
                f"GREEN ZONE (ratio {ratio:.2f}): "
                f"Closing at {_format_currency(decision.get('price'))}."
            )
        else:
            decision["log"] = (
                f"YELLOW ZONE (ratio {ratio:.2f}): "
                f"Countering at {_format_currency(decision.get('price'))}."
            )

    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="SYSTEM",
            body=decision.get("log", "AI processed decision."),
            is_read=True,
        )
    )

    if load_ref.lower() not in email_content.lower():
        email_content = f"{email_content}\n\nRef: {load_ref}"

    should_send_decline = bool(getattr(driver_settings, "notify_on_decline", False))
    should_send = decision.get("action") != "WALK_AWAY" or should_send_decline
    if not should_send:
        db.commit()
        return decision.get("action", "WAITING_FOR_HUMAN")

    lane = ""
    if load and load.origin and load.destination:
        lane = f"{load.origin} to {load.destination}"
    elif getattr(negotiation, "lane", None):
        lane = str(getattr(negotiation, "lane"))

    broker_email = getattr(negotiation, "broker_email", None)
    if not broker_email:
        broker_email_record = (
            db.query(BrokerEmail)
            .filter(BrokerEmail.mc_number == negotiation.broker_mc_number)
            .order_by(BrokerEmail.confidence.desc())
            .first()
        )
        broker_email = broker_email_record.email if broker_email_record and broker_email_record.email else None

    if not broker_email:
        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="SYSTEM",
                body="AI could not send response: broker email missing.",
                is_read=False,
            )
        )
        db.commit()
        return "WAITING_FOR_HUMAN"

    subject = f"Re: Load {load_ref}"
    if lane:
        subject = f"{subject} - {lane}"

    if bool(getattr(driver_settings, "review_before_send", False)):
        negotiation.pending_review_subject = subject
        negotiation.pending_review_body = email_content
        negotiation.pending_review_action = decision.get("action")
        negotiation.pending_review_price = decision.get("price")
        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="SYSTEM",
                body=(
                    "AI DRAFT READY: Review required before send. "
                    f"Action={decision.get('action', 'UNKNOWN')} "
                    f"Price={_format_currency(decision.get('price')) or 'N/A'}"
                ),
                is_read=False,
            )
        )
        db.commit()
        return "REVIEW_REQUIRED"

    success = await send_outbound_email(
        recipient=broker_email,
        subject=subject,
        body=email_content,
        load_ref=load_ref,
        driver_handle=getattr(driver_settings, "display_name", "dispatch"),
        load_source=getattr(load, "source_platform", None) if load else None,
        negotiation_id=getattr(negotiation, "id", None),
    )

    if not success:
        db.add(
            Message(
                negotiation_id=negotiation.id,
                sender="SYSTEM",
                body=f"AI attempted {decision.get('action', 'UNKNOWN')} but SMTP failed.",
                is_read=False,
            )
        )
        db.commit()
        return "WAITING_FOR_HUMAN"

    if decision.get("price") is not None:
        negotiation.current_offer = decision["price"]

    db.add(
        Message(
            negotiation_id=negotiation.id,
            sender="SYSTEM",
            body=f"AI SENT: {decision.get('action', 'UNKNOWN')} at {_format_currency(decision.get('price')) or 'N/A'}",
            is_read=False,
        )
    )
    db.commit()

    return decision.get("action", "WAITING_FOR_HUMAN")


def process_negotiation_logic(negotiation, broker_price_detected, driver_settings):
    """
    Decides the next move for the 'Closer' agent.
    """
    # 1. Calculate the 'True Floor' for this specific load
    # distance is pulled from the 'negotiation' object populated by the Scout
    absolute_floor = _absolute_floor(negotiation, driver_settings, broker_price_detected)

    ratio = broker_price_detected / absolute_floor if absolute_floor > 0 else 0.0

    # GREEN ZONE: close quickly when offer is near floor
    if ratio >= 0.95:
        negotiation.status = "CLOSING_STANCE"
        close_price = max(broker_price_detected, absolute_floor)
        return {
            "action": "SEND_COUNTER",
            "price": close_price,
            "template": "close_the_deal",
            "log": (
                f"GREEN ZONE (ratio {ratio:.2f}): "
                f"Closing at {_format_currency(close_price)}."
            ),
        }

    # YELLOW ZONE: negotiate up with a clean round counter
    if 0.80 <= ratio < 0.95:
        target = _counter_target_from_floor(absolute_floor)
        return {
            "action": "SEND_COUNTER",
            "price": target,
            "template": "standard_negotiation",
            "log": (
                f"YELLOW ZONE (ratio {ratio:.2f}): "
                f"Countering at {_format_currency(target)}."
            ),
        }

    # RED ZONE: below 80% of floor, walk away
    if ratio < 0.80:
        return {
            "action": "WALK_AWAY",
            "template": "polite_decline",
            "log": (
                f"RED ZONE (ratio {ratio:.2f}): "
                f"Offer {_format_currency(broker_price_detected)} too far below "
                f"floor {_format_currency(absolute_floor)}. Walking away."
            ),
        }

    target = _counter_target_from_floor(absolute_floor)
    return {
        "action": "SEND_COUNTER",
        "price": target,
        "template": "standard_negotiation",
        "log": (
            f"YELLOW ZONE (ratio {ratio:.2f}): "
            f"Countering at {_format_currency(target)}."
        ),
    }