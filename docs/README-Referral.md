# Referral & Revenue Engine (Week 3)

This document explains the live referral bounty + fee ledger system implemented for Green Candle Dispatch.

## What It Does
- Applies a dispatch fee of 2.5% per secured load.
- Splits that fee into a 4-slice model.
- Pays referral bounty from platform profit (not as a fifth structural slice).
- Records immutable accounting rows in `fee_ledger` and `referral_earnings`.

## Core Rules
- Dispatch fee: `2.5%` of load value.
- Referral bounty: `10%` of dispatch fee.
- Referral bounty cap: `$5.00` per load.
- Referral payout source: deducted from `slice_platform_profit` only.

## 4-Slice Model
Configured in `app/core/config.py`:
- `SLICE_DRIVER_CREDITS_RATE = 0.2105` (21.05%)
- `SLICE_INFRA_RESERVE_RATE = 0.2105` (21.05%)
- `SLICE_PLATFORM_PROFIT_RATE = 0.3158` (31.58%)
- `SLICE_TREASURY_RATE = 0.2632` (26.32%)

Accounting identity used by the engine:

`total_fee = driver_credits + infra_reserve + treasury + platform_profit_net + referral_bounty_paid`

**Note:** `slice_driver_credits` is the driver's share of the fee in the revenue allocation model—recorded in `fee_ledger` for accounting. It is **not** a spendable wallet or balance. (The legacy `drivers.balance` wallet was removed; referrals never depended on it.)

## Tables
Migration: `migrations/004_revenue_ledger.sql`

### `public.fee_ledger`
- One ledger row per negotiation (`uq_fee_ledger_negotiation`).
- Stores total fee + 4 slices + `referral_bounty_paid`.

### `public.referral_earnings`
- Stores referral payout events.
- Columns include `referrer_id`, `referred_driver_id`, `negotiation_id`, `amount`, `status`, `payout_type`.

### `public.drivers.referred_by_id`
- Self-reference to `drivers.id`.
- Determines whether referral bounty applies.

## Service Entry Point
`app/services/ledger.py` -> `process_load_fees(db, load_value, driver_id, negotiation_id)`

Behavior:
1. Idempotency check: skip if `fee_ledger` already has this negotiation.
2. Parse load value safely (supports strings like `$2,100`).
3. Calculate total fee and slice amounts with Decimal rounding (`ROUND_HALF_UP`).
4. If referred driver:
   - `raw_bounty = total_fee * REFERRAL_BOUNTY_RATE`
   - `referral_bounty_paid = min(raw_bounty, REFERRAL_BOUNTY_CAP)`
   - subtract from platform profit.
5. Insert `fee_ledger` row.
6. Insert `referral_earnings` row when bounty > 0.

## Wiring
Triggered when a load is secured:
- `app/routes/operations.py` in `/api/negotiations/secure-load`
- `app/routes/operations.py` in `/api/negotiations/retry-secure-email`

## Verified Live Examples
- Uncapped case (`load_value=1200`):
  - `total_fee=30.00`, bounty `3.00`
- Cap-hit case (`load_value=4000`):
  - `total_fee=100.00`, raw bounty `10.00`, paid bounty `5.00`

## Ops Queries
### Check latest ledger rows
```bash
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c "
SELECT id, negotiation_id, total_fee_collected, slice_driver_credits, slice_infra_reserve, slice_platform_profit, slice_treasury, referral_bounty_paid, created_at
FROM fee_ledger
ORDER BY id DESC
LIMIT 20;"
```

### Check referral earnings queue
```bash
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c "
SELECT id, referrer_id, referred_driver_id, negotiation_id, amount, status, payout_type, created_at
FROM referral_earnings
ORDER BY id DESC
LIMIT 20;"
```

### Verify one negotiation end-to-end
```bash
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c "
SELECT fl.negotiation_id,
       fl.total_fee_collected,
       fl.slice_driver_credits,
       fl.slice_infra_reserve,
       fl.slice_platform_profit,
       fl.slice_treasury,
       fl.referral_bounty_paid,
       re.amount AS referral_amount,
       re.status AS referral_status
FROM fee_ledger fl
LEFT JOIN referral_earnings re ON re.negotiation_id = fl.negotiation_id
WHERE fl.negotiation_id = 10245;"
```

## Notes
- This is a 4-slice system with a referral expense inside profit, not a 5-slice allocation model.
- All monetary math is Decimal-based to avoid float drift.
- If `referred_by_id` is null, referral payout remains `0.00` and no `referral_earnings` row is created.
