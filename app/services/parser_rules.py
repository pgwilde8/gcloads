import json
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "parsing_rules.json"


def load_parsing_rules() -> dict[str, Any]:
    if not RULES_PATH.exists():
        return {
            "contact_keywords": {
                "email": ["email only"],
                "call": ["call to book"],
            }
        }

    with RULES_PATH.open("r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def resolve_contact_mode(raw_instruction: str | None, metadata: dict[str, Any] | None) -> str:
    instruction_text = (raw_instruction or "").strip().lower()
    metadata = metadata or {}

    searchable_parts = [instruction_text]
    for key in ("notes", "note", "comments", "comment", "special_instructions"):
        value = metadata.get(key)
        if isinstance(value, str):
            searchable_parts.append(value.lower())

    searchable_text = " ".join(searchable_parts)
    rules = load_parsing_rules()
    keywords = rules.get("contact_keywords", {})

    call_keywords = [item.lower() for item in keywords.get("call", []) if isinstance(item, str)]
    email_keywords = [item.lower() for item in keywords.get("email", []) if isinstance(item, str)]

    if any(keyword in searchable_text for keyword in call_keywords):
        return "call"
    if any(keyword in searchable_text for keyword in email_keywords):
        return "email"

    if instruction_text in {"call", "phone", "call_only", "phone_only"}:
        return "call"
    if instruction_text in {"email", "email_only"}:
        return "email"

    return "email"
