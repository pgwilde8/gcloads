-- 004_revenue_ledger.sql
-- Revenue ledger + referral engine schema

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS referred_by_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_drivers_referred_by'
    ) THEN
        ALTER TABLE public.drivers
            ADD CONSTRAINT fk_drivers_referred_by
            FOREIGN KEY (referred_by_id) REFERENCES public.drivers(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_drivers_referred_by_id
    ON public.drivers(referred_by_id);

CREATE TABLE IF NOT EXISTS public.fee_ledger (
    id SERIAL PRIMARY KEY,
    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
    driver_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
    total_load_value DECIMAL(12,2) NOT NULL,
    total_fee_collected DECIMAL(10,2) NOT NULL,
    slice_driver_credits DECIMAL(10,2) NOT NULL,
    slice_infra_reserve DECIMAL(10,2) NOT NULL,
    slice_platform_profit DECIMAL(10,2) NOT NULL,
    slice_treasury DECIMAL(10,2) NOT NULL,
    referral_bounty_paid DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_ledger_negotiation
    ON public.fee_ledger(negotiation_id)
    WHERE negotiation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.referral_earnings (
    id SERIAL PRIMARY KEY,
    referrer_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
    referred_driver_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL,
    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
    amount DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    payout_type VARCHAR(20) NOT NULL DEFAULT 'CANDLE',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_referral_earnings_referrer
    ON public.referral_earnings(referrer_id);

CREATE INDEX IF NOT EXISTS idx_referral_earnings_negotiation
    ON public.referral_earnings(negotiation_id);
