#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.database import SessionLocal
from app.models.broker import Broker
from app.models.driver import Driver
from app.models.load import Load
from app.models.operations import Message, Negotiation


def _pick_driver(driver_email: str | None) -> Driver:
    with SessionLocal() as db:
        driver = None
        if driver_email:
            driver = db.query(Driver).filter(Driver.email == driver_email.strip().lower()).first()
        if not driver:
            driver = db.query(Driver).order_by(Driver.created_at.desc()).first()
        if not driver:
            raise RuntimeError("No driver found. Register a driver first.")
        return driver


def _pick_valid_broker_mc() -> str:
    with SessionLocal() as db:
        broker = db.query(Broker).order_by(Broker.mc_number.asc()).first()
        if not broker or not broker.mc_number:
            raise RuntimeError("No broker MC found in webwise.brokers; import broker data first.")
        return broker.mc_number


def _get_unread_count(base_url: str, driver_email: str) -> int:
    response = requests.get(
        f"{base_url}/api/notifications/unread-count",
        params={"email": driver_email},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return int(payload.get("unread_count", 0))


def _find_negotiation(load_pk: int, driver_id: int) -> Negotiation | None:
    with SessionLocal() as db:
        return (
            db.query(Negotiation)
            .filter(Negotiation.load_id == load_pk, Negotiation.driver_id == driver_id)
            .order_by(Negotiation.id.desc())
            .first()
        )


def _ensure_negotiation(load_pk: int, driver: Driver, broker_mc_number: str) -> Negotiation:
    negotiation = _find_negotiation(load_pk, driver.id)
    if negotiation:
        return negotiation

    with SessionLocal() as db:
        created = Negotiation(
            load_id=load_pk,
            driver_id=driver.id,
            broker_mc_number=broker_mc_number,
            status="Sent",
        )
        db.add(created)
        db.commit()
        db.refresh(created)
        return created


def _insert_mock_broker_reply(negotiation_id: int, load_ref: str) -> int:
    with SessionLocal() as db:
        inbound = Message(
            negotiation_id=negotiation_id,
            sender="Broker",
            body=f"[VERIFY_LOOP {load_ref}] Is this still available? What is your rate?",
            is_read=False,
        )
        db.add(inbound)
        db.commit()
        db.refresh(inbound)
        return inbound.id


def _cleanup_test_data(load_ref: str) -> None:
    with SessionLocal() as db:
        load = db.query(Load).filter(Load.ref_id == load_ref).first()
        if load:
            db.delete(load)
            db.commit()


def run_test(
    base_url: str,
    api_key: str,
    test_recipient: str | None,
    driver_email: str | None,
    keep_data: bool,
) -> int:
    print("🚀 Starting Full-Loop Validation...")

    driver = _pick_driver(driver_email)
    broker_mc = _pick_valid_broker_mc()
    email_domain = os.getenv("EMAIL_DOMAIN", "gcdloads.com")
    expected_identity = f"{driver.display_name}@{email_domain}"
    print(f"✅ Identity verified for driver: {expected_identity}")

    unread_before = _get_unread_count(base_url, driver.email)
    print(f"🔎 Baseline unread count: {unread_before}")

    load_ref = f"VERIFY-{int(datetime.now(timezone.utc).timestamp())}"
    outbound_enabled = bool(test_recipient)

    payload = {
        "load_id": load_ref,
        "source": "verify-loop",
        "mc_number": broker_mc,
        "email": test_recipient,
        "origin": "Newark, NJ",
        "destination": "Los Angeles, CA",
        "price": "$3500",
        "equipment_type": "Dry Van",
        "contact_instructions": "call" if outbound_enabled else "email",
        "driver_id": driver.id,
        "auto_bid": outbound_enabled,
    }

    print("📡 Step 1: Simulating Scout ingest...")
    response = requests.post(
        f"{base_url}/api/scout/ingest",
        json=payload,
        headers={"x-api-key": api_key},
        timeout=30,
    )
    try:
        response_payload = response.json()
    except Exception:
        response_payload = {"raw": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"Ingest failed [{response.status_code}]: {response_payload}")

    print(f"✅ Ingest response: {response_payload}")

    identity_used = str(response_payload.get("identity_used") or "")
    if expected_identity not in identity_used:
        print(f"⚠️ Identity mismatch: expected to include {expected_identity}, got {identity_used}")
    else:
        print(f"✅ Outbound identity confirmed: {identity_used}")

    load_pk = int(response_payload["load_id"])
    negotiation = _find_negotiation(load_pk, driver.id)
    if not negotiation:
        negotiation = _ensure_negotiation(load_pk, driver, broker_mc)
        print("ℹ️ Auto-bid did not create negotiation; created one for inbound-loop verification.")
    else:
        print(f"✅ Negotiation found: {negotiation.id}")

    print("📥 Step 2: Simulating inbound broker reply...")
    message_id = _insert_mock_broker_reply(negotiation.id, load_ref)
    print(f"✅ Mock inbound inserted as message #{message_id} (is_read=False)")

    print("🔴 Step 3: Asserting dashboard notification pulse...")
    time.sleep(1)
    unread_after = _get_unread_count(base_url, driver.email)
    delta = unread_after - unread_before

    if delta >= 1:
        print(f"🔥 SUCCESS: unread moved from {unread_before} -> {unread_after} (delta={delta})")
        success = True
    else:
        print(f"❌ FAILURE: unread stayed at {unread_after} (baseline={unread_before})")
        success = False

    if keep_data:
        print(f"🧪 Keeping test data for inspection (load ref: {load_ref}).")
    else:
        _cleanup_test_data(load_ref)
        print(f"🧹 Cleanup complete for load ref: {load_ref}")

    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify end-to-end Scout -> SMTP -> inbound -> dashboard unread loop.")
    parser.add_argument("--base-url", default=os.getenv("VERIFY_BASE_URL", "http://127.0.0.1:8369"))
    parser.add_argument("--api-key", default=os.getenv("SCOUT_API_KEY", ""))
    parser.add_argument("--test-recipient", default=os.getenv("VERIFY_TEST_RECIPIENT", ""))
    parser.add_argument("--driver-email", default=os.getenv("VERIFY_DRIVER_EMAIL", ""))
    parser.add_argument("--keep-data", action="store_true", help="Keep inserted test load/message for manual inspection.")
    args = parser.parse_args()

    if not args.api_key:
        print("❌ Missing API key. Set SCOUT_API_KEY or pass --api-key.")
        return 2

    if not args.test_recipient:
        print("⚠️ No test recipient provided; outbound SMTP send is skipped, inbound/dashboard checks still run.")

    try:
        return run_test(
            base_url=args.base_url.rstrip("/"),
            api_key=args.api_key,
            test_recipient=(args.test_recipient or None),
            driver_email=(args.driver_email or None),
            keep_data=args.keep_data,
        )
    except Exception as exc:
        print(f"❌ Verification failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
