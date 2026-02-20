#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from app.database import SessionLocal
from app.models.driver import Driver
from app.models.operations import Negotiation


@dataclass(frozen=True)
class MatrixCase:
    zone: str
    label: str
    offer: int
    expected_action: str


MATRIX_CASES: list[MatrixCase] = [
    MatrixCase("RED", "Low", 1000, "WALK_AWAY"),
    MatrixCase("RED", "Med", 1400, "WALK_AWAY"),
    MatrixCase("RED", "High", 1590, "WALK_AWAY"),
    MatrixCase("YELLOW", "Low", 1610, "SEND_COUNTER"),
    MatrixCase("YELLOW", "Med", 1800, "SEND_COUNTER"),
    MatrixCase("YELLOW", "High", 1890, "SEND_COUNTER"),
    MatrixCase("GREEN", "Low", 1910, "SEND_COUNTER"),
    MatrixCase("GREEN", "Med", 2100, "SEND_COUNTER"),
    MatrixCase("GREEN", "High", 2400, "SEND_COUNTER"),
]


def _pick_negotiation_id(explicit_id: int | None) -> int:
    if explicit_id:
        return explicit_id

    with SessionLocal() as db:
        latest = db.query(Negotiation).order_by(Negotiation.id.desc()).first()
        if not latest:
            raise RuntimeError("No negotiations found. Create one before running matrix.")
        return int(latest.id)


def _load_driver_for_negotiation(negotiation_id: int) -> tuple[int, Decimal | None, Decimal | None]:
    with SessionLocal() as db:
        negotiation = db.query(Negotiation).filter(Negotiation.id == negotiation_id).first()
        if not negotiation:
            raise RuntimeError(f"Negotiation not found: {negotiation_id}")

        driver = db.query(Driver).filter(Driver.id == negotiation.driver_id).first()
        if not driver:
            raise RuntimeError(f"Driver not found for negotiation: {negotiation_id}")

        return int(driver.id), driver.min_flat_rate, driver.min_cpm


def _set_driver_floor(driver_id: int, min_flat_rate: float, min_cpm: float) -> None:
    with SessionLocal() as db:
        driver = db.query(Driver).filter(Driver.id == driver_id).first()
        if not driver:
            raise RuntimeError(f"Driver not found: {driver_id}")
        driver.min_flat_rate = min_flat_rate
        driver.min_cpm = min_cpm
        db.commit()


def _effective_action(payload: dict) -> str:
    action_taken = str(payload.get("action_taken") or "").strip().upper()
    pending_action = str(payload.get("pending_review_action") or "").strip().upper()

    if action_taken == "REVIEW_REQUIRED" and pending_action:
        return pending_action
    return action_taken


def _run_case(base_url: str, case: MatrixCase, negotiation_id: int, admin_password: str | None) -> tuple[bool, str, str]:
    form_data = {
        "negotiation_id": str(negotiation_id),
        "message_text": f"We can do ${case.offer:,} all-in.",
        "dry_run": "true",
    }
    if admin_password:
        form_data["admin_password"] = admin_password

    response = requests.post(
        f"{base_url}/api/test/simulate-broker",
        data=form_data,
        timeout=30,
    )

    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}

    if response.status_code >= 400 or payload.get("status") != "ok":
        return (
            False,
            "ERROR",
            f"HTTP {response.status_code} payload={payload}",
        )

    effective = _effective_action(payload)
    passed = effective == case.expected_action
    details = (
        f"effective={effective} action_taken={payload.get('action_taken')} "
        f"pending={payload.get('pending_review_action')}"
    )
    return (passed, effective or "UNKNOWN", details)


def _print_header(negotiation_id: int, floor_value: int, base_url: str) -> None:
    print(f"Regression Matrix | negotiation_id={negotiation_id} | forced_floor=${floor_value:,}")
    print(f"Endpoint: {base_url}/api/test/simulate-broker (dry_run=true)")
    print("-" * 108)
    print(f"{'Zone':<8} {'Case':<6} {'Offer':>10} {'Expected':<14} {'Actual':<14} {'Result':<8} Details")
    print("-" * 108)


def _print_row(case: MatrixCase, actual: str, ok: bool, details: str) -> None:
    result = "PASS" if ok else "FAIL"
    print(
        f"{case.zone:<8} {case.label:<6} ${case.offer:>9,} {case.expected_action:<14} "
        f"{actual:<14} {result:<8} {details}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 9-point negotiation regression matrix.")
    parser.add_argument("--negotiation-id", type=int, default=None, help="Target negotiation ID (default: latest)")
    parser.add_argument("--base-url", default=os.getenv("VERIFY_BASE_URL", "http://127.0.0.1:8369"))
    parser.add_argument("--floor", type=int, default=2000, help="Temporary min_flat_rate floor for matrix")
    parser.add_argument(
        "--admin-password",
        default=os.getenv("ADMIN_ENRICH_PASSWORD", ""),
        help="Required when APP_ENV is not development",
    )
    args = parser.parse_args()

    negotiation_id = _pick_negotiation_id(args.negotiation_id)
    driver_id, original_flat, original_cpm = _load_driver_for_negotiation(negotiation_id)

    failures = 0

    try:
        _set_driver_floor(driver_id, float(args.floor), 0.0)
        _print_header(negotiation_id, args.floor, args.base_url.rstrip("/"))

        for case in MATRIX_CASES:
            ok, actual, details = _run_case(
                base_url=args.base_url.rstrip("/"),
                case=case,
                negotiation_id=negotiation_id,
                admin_password=(args.admin_password or None),
            )
            _print_row(case, actual, ok, details)
            if not ok:
                failures += 1

        print("-" * 108)
        if failures:
            print(f"Matrix FAILED: {failures}/{len(MATRIX_CASES)} failing case(s).")
            return 1

        print(f"Matrix PASSED: {len(MATRIX_CASES)}/{len(MATRIX_CASES)} cases green.")
        return 0
    finally:
        restore_flat = float(original_flat) if original_flat is not None else 0.0
        restore_cpm = float(original_cpm) if original_cpm is not None else 0.0
        _set_driver_floor(driver_id, restore_flat, restore_cpm)


if __name__ == "__main__":
    raise SystemExit(main())
