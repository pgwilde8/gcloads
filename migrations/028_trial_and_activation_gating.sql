-- Migration 028: Trial + activation gating
--
-- Adds trial state to drivers so we can:
--   - allow Scout browsing/scoring/notifications during trial
--   - block real work (broker emails, packet compose, factoring, mark WON)
--     until billing_status = 'active'
--
-- Does NOT touch: ledger, weekly billing job, stripe charge code,
--                 billing_mode, billing_exempt_until, or billing_state.

BEGIN;

-- 1) New columns
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS trial_started_at  TIMESTAMPTZ  DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS trial_ends_at     TIMESTAMPTZ  DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS billing_status    VARCHAR(20)  NOT NULL DEFAULT 'trial',
    ADD COLUMN IF NOT EXISTS activated_at      TIMESTAMPTZ  DEFAULT NULL;

-- 2) Valid billing_status values
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'drivers_billing_status_check'
    ) THEN
        ALTER TABLE public.drivers
            ADD CONSTRAINT drivers_billing_status_check
            CHECK (billing_status IN ('trial', 'active', 'card_required', 'suspended'));
    END IF;
END $$;

-- 3) Trial date sanity (both set or both null; end > start)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'drivers_trial_dates_check'
    ) THEN
        ALTER TABLE public.drivers
            ADD CONSTRAINT drivers_trial_dates_check
            CHECK (
                (trial_started_at IS NULL AND trial_ends_at IS NULL)
                OR
                (trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL
                 AND trial_ends_at > trial_started_at)
            );
    END IF;
END $$;

-- 4) Indexes for banner checks, cron flips, gating lookups
CREATE INDEX IF NOT EXISTS idx_drivers_billing_status ON public.drivers (billing_status);
CREATE INDEX IF NOT EXISTS idx_drivers_trial_ends_at  ON public.drivers (trial_ends_at)
    WHERE trial_ends_at IS NOT NULL;

-- 5) Backfill existing drivers
--    Drivers with a Stripe customer id → active (they've already set up billing).
--    Everyone else → trial with a 7-day window from now.
UPDATE public.drivers
SET
    billing_status   = CASE
                           WHEN stripe_customer_id IS NOT NULL THEN 'active'
                           ELSE 'trial'
                       END,
    activated_at     = CASE
                           WHEN stripe_customer_id IS NOT NULL AND activated_at IS NULL
                           THEN NOW()
                           ELSE activated_at
                       END,
    trial_started_at = CASE
                           WHEN stripe_customer_id IS NULL AND trial_started_at IS NULL
                           THEN NOW()
                           ELSE trial_started_at
                       END,
    trial_ends_at    = CASE
                           WHEN stripe_customer_id IS NULL AND trial_ends_at IS NULL
                           THEN NOW() + INTERVAL '7 days'
                           ELSE trial_ends_at
                       END;

COMMIT;
