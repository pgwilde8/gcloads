-- 005_referral_expiration.sql
-- Add deterministic referral expiration window fields (18 months)

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS referral_started_at TIMESTAMPTZ NULL;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS referral_expires_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_referral_expires_at
    ON public.drivers(referral_expires_at);

-- Optional backfill: if a start exists but expiry is missing, set deterministic 18-month end.
UPDATE public.drivers
SET referral_expires_at = referral_started_at + INTERVAL '18 months'
WHERE referral_started_at IS NOT NULL
  AND referral_expires_at IS NULL;
