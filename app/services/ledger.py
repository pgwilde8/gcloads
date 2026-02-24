from __future__ import annotations

import calendar
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings


CENT = Decimal("0.01")


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _to_decimal(value: str | int | float | Decimal) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _parse_load_value(load_value: str | int | float | Decimal) -> Decimal:
    if isinstance(load_value, Decimal):
        return _money(load_value)
    if isinstance(load_value, (int, float)):
        return _money(_to_decimal(load_value))

    raw = str(load_value or "").strip()
    clean = "".join(char for char in raw if char.isdigit() or char == ".")
    if not clean:
        return Decimal("0.00")
    return _money(_to_decimal(clean))


def _compute_slices(total_fee: Decimal) -> dict[str, Decimal]:
    # driver_credits = driver's revenue share slice (accounting), not a wallet.
    slice_driver_credits = _money(total_fee * _to_decimal(settings.SLICE_DRIVER_CREDITS_RATE))
    slice_infra_reserve = _money(total_fee * _to_decimal(settings.SLICE_INFRA_RESERVE_RATE))
    slice_treasury = _money(total_fee * _to_decimal(settings.SLICE_TREASURY_RATE))

    slice_platform_profit = _money(total_fee - slice_driver_credits - slice_infra_reserve - slice_treasury)

    return {
        "slice_driver_credits": slice_driver_credits,
        "slice_infra_reserve": slice_infra_reserve,
        "slice_treasury": slice_treasury,
        "slice_platform_profit": slice_platform_profit,
    }


def process_load_fees(
    db: Session,
    *,
    load_value: str | int | float | Decimal,
    driver_id: int,
    negotiation_id: int,
) -> dict[str, float | int | bool]:
    existing = db.execute(
        text(
            """
            SELECT id
            FROM fee_ledger
            WHERE negotiation_id = :negotiation_id
            LIMIT 1
            """
        ),
        {"negotiation_id": negotiation_id},
    ).first()
    if existing:
        return {
            "created": False,
            "fee_ledger_id": int(existing.id),
        }

    total_load_value = _parse_load_value(load_value)
    total_fee_collected = _money(total_load_value * _to_decimal(settings.DISPATCH_FEE_RATE))

    slices = _compute_slices(total_fee_collected)
    platform_profit_gross = slices["slice_platform_profit"]

    referred_by_row = db.execute(
        text(
            """
            SELECT referred_by_id, referral_started_at, referral_expires_at
            FROM drivers
            WHERE id = :driver_id
            """
        ),
        {"driver_id": driver_id},
    ).first()
    referred_by_id = int(referred_by_row.referred_by_id) if referred_by_row and referred_by_row.referred_by_id else None

    referral_started_at = getattr(referred_by_row, "referral_started_at", None) if referred_by_row else None
    referral_expires_at = getattr(referred_by_row, "referral_expires_at", None) if referred_by_row else None

    now_utc = datetime.now(timezone.utc)

    if referred_by_id and (referral_started_at is None or referral_expires_at is None):
        referral_started_at = now_utc
        referral_expires_at = _add_months(referral_started_at, 18)
        db.execute(
            text(
                """
                UPDATE drivers
                SET referral_started_at = :referral_started_at,
                    referral_expires_at = :referral_expires_at
                WHERE id = :driver_id
                """
            ),
            {
                "driver_id": driver_id,
                "referral_started_at": referral_started_at,
                "referral_expires_at": referral_expires_at,
            },
        )

    if not referred_by_id and (referral_started_at is not None or referral_expires_at is not None):
        db.execute(
            text(
                """
                UPDATE drivers
                SET referral_started_at = NULL,
                    referral_expires_at = NULL
                WHERE id = :driver_id
                """
            ),
            {"driver_id": driver_id},
        )

    referral_bounty_paid = Decimal("0.00")
    referral_is_active = False
    if referred_by_id and referral_expires_at is not None:
        expiry_value = referral_expires_at
        if isinstance(expiry_value, str):
            try:
                expiry_value = datetime.fromisoformat(expiry_value)
            except ValueError:
                expiry_value = None
        if isinstance(expiry_value, datetime) and expiry_value.tzinfo is None:
            expiry_value = expiry_value.replace(tzinfo=timezone.utc)
        if isinstance(expiry_value, datetime):
            referral_is_active = now_utc <= expiry_value

    if referral_is_active:
        raw_bounty = _money(total_fee_collected * _to_decimal(settings.REFERRAL_BOUNTY_RATE))
        bounty_cap = _money(_to_decimal(settings.REFERRAL_BOUNTY_CAP))
        referral_bounty_paid = min(raw_bounty, bounty_cap)

    slice_platform_profit_net = _money(platform_profit_gross - referral_bounty_paid)
    if slice_platform_profit_net < Decimal("0.00"):
        slice_platform_profit_net = Decimal("0.00")

    ledger_insert = db.execute(
        text(
            """
            INSERT INTO fee_ledger (
                negotiation_id,
                driver_id,
                total_load_value,
                total_fee_collected,
                slice_driver_credits,
                slice_infra_reserve,
                slice_platform_profit,
                slice_treasury,
                referral_bounty_paid
            )
            VALUES (
                :negotiation_id,
                :driver_id,
                :total_load_value,
                :total_fee_collected,
                :slice_driver_credits,
                :slice_infra_reserve,
                :slice_platform_profit,
                :slice_treasury,
                :referral_bounty_paid
            )
            RETURNING id
            """
        ),
        {
            "negotiation_id": negotiation_id,
            "driver_id": driver_id,
            "total_load_value": total_load_value,
            "total_fee_collected": total_fee_collected,
            "slice_driver_credits": slices["slice_driver_credits"],
            "slice_infra_reserve": slices["slice_infra_reserve"],
            "slice_platform_profit": slice_platform_profit_net,
            "slice_treasury": slices["slice_treasury"],
            "referral_bounty_paid": referral_bounty_paid,
        },
    ).first()

    referral_earnings_id: int | None = None
    if referral_is_active and referred_by_id and referral_bounty_paid > Decimal("0.00"):
        referral_insert = db.execute(
            text(
                """
                INSERT INTO referral_earnings (
                    referrer_id,
                    referred_driver_id,
                    negotiation_id,
                    amount,
                    status,
                    payout_type
                )
                VALUES (
                    :referrer_id,
                    :referred_driver_id,
                    :negotiation_id,
                    :amount,
                    'PENDING',
                    'CANDLE'
                )
                RETURNING id
                """
            ),
            {
                "referrer_id": referred_by_id,
                "referred_driver_id": driver_id,
                "negotiation_id": negotiation_id,
                "amount": referral_bounty_paid,
            },
        ).first()
        referral_earnings_id = int(referral_insert.id) if referral_insert else None

    return {
        "created": True,
        "fee_ledger_id": int(ledger_insert.id) if ledger_insert else 0,
        "referral_earnings_id": referral_earnings_id or 0,
        "total_fee_collected": float(total_fee_collected),
        "slice_driver_credits": float(slices["slice_driver_credits"]),
        "slice_infra_reserve": float(slices["slice_infra_reserve"]),
        "slice_platform_profit": float(slice_platform_profit_net),
        "slice_treasury": float(slices["slice_treasury"]),
        "referral_bounty_paid": float(referral_bounty_paid),
    }