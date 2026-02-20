#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from app.database import SessionLocal
from app.logic.parser import extract_negotiation_id_from_message
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import Negotiation


@dataclass
class RoutingResult:
    ok: bool
    case_name: str
    details: str


def _get_negotiation_context(negotiation_id: int) -> tuple[Negotiation, Driver, Load]:
    with SessionLocal() as db:
        negotiation = db.query(Negotiation).filter(Negotiation.id == negotiation_id).first()
        if not negotiation:
            raise RuntimeError(f"Negotiation not found: {negotiation_id}")

        driver = db.query(Driver).filter(Driver.id == negotiation.driver_id).first()
        if not driver:
            raise RuntimeError(f"Driver not found for negotiation: {negotiation_id}")

        load = db.query(Load).filter(Load.id == negotiation.load_id).first()
        if not load:
            raise RuntimeError(f"Load not found for negotiation: {negotiation_id}")

        return negotiation, driver, load


def _build_message(
    *,
    to_address: str,
    from_address: str,
    subject: str,
    body: str,
    header_negotiation_id: int | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to_address
    msg["From"] = from_address
    msg["Subject"] = subject
    if header_negotiation_id is not None:
        msg["X-GCD-Negotiation-ID"] = str(header_negotiation_id)
    msg.set_content(body)
    return msg


def _run_case(
    *,
    case_name: str,
    msg: EmailMessage,
    email_domain: str,
    expected_negotiation_id: int,
) -> RoutingResult:
    parsed = extract_negotiation_id_from_message(msg, email_domain=email_domain)
    if not parsed:
        return RoutingResult(False, case_name, "Parser returned no negotiation ID")

    parsed_id = int(parsed.get("negotiation_id", 0))
    layer = str(parsed.get("layer", "unknown"))
    if parsed_id != expected_negotiation_id:
        return RoutingResult(
            False,
            case_name,
            f"Wrong ID parsed. expected={expected_negotiation_id} got={parsed_id} layer={layer}",
        )

    return RoutingResult(True, case_name, f"Matched negotiation_id={parsed_id} via layer={layer}")


def run_verification(negotiation_id: int, email_domain: str) -> int:
    negotiation, driver, load = _get_negotiation_context(negotiation_id)

    safe_handle = (driver.display_name or "dispatch").strip().lower().replace(" ", "")
    load_ref = load.ref_id or str(load.id)

    no_tag_to = f"{safe_handle}@{email_domain}"
    broker_from = "broker@example.com"

    case_a_msg = _build_message(
        to_address=no_tag_to,
        from_address=broker_from,
        subject=f"Re: Load {load_ref}",
        body="Routing verification: header-only fallback",
        header_negotiation_id=negotiation.id,
    )

    case_b_msg = _build_message(
        to_address=no_tag_to,
        from_address=broker_from,
        subject=f"Re: Load {load_ref} [GCD:{negotiation.id}]",
        body="Routing verification: subject-only fallback",
        header_negotiation_id=None,
    )

    results = [
        _run_case(
            case_name="Case A - Header Fallback",
            msg=case_a_msg,
            email_domain=email_domain,
            expected_negotiation_id=negotiation.id,
        ),
        _run_case(
            case_name="Case B - Subject Fallback",
            msg=case_b_msg,
            email_domain=email_domain,
            expected_negotiation_id=negotiation.id,
        ),
    ]

    print(f"Negotiation: {negotiation.id} | Driver: {driver.display_name} | Load Ref: {load_ref}")
    print("-" * 72)

    failures = 0
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.case_name}: {result.details}")
        if not result.ok:
            failures += 1

    print("-" * 72)
    if failures:
        print(f"Routing verification FAILED ({failures} failing case(s)).")
        return 1

    print("Routing verification PASSED (all fallback cases).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify inbound fallback routing using X-headers and subject token."
    )
    parser.add_argument("--negotiation-id", type=int, required=True, help="Target negotiation ID")
    parser.add_argument(
        "--email-domain",
        default=(os.getenv("EMAIL_DOMAIN") or "gcdloads.com").strip().lower() or "gcdloads.com",
        help="Domain used for parser matching (defaults to EMAIL_DOMAIN)",
    )
    args = parser.parse_args()

    try:
        return run_verification(args.negotiation_id, args.email_domain)
    except Exception as exc:
        print(f"Verification failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
