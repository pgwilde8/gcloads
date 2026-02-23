-- Migration 020: Driver billing mode
-- Supports beta vs paid mode. Beta drivers are never charged (Stripe skipped).
-- billing_exempt_until/reason support temporary paid exemptions (future promos).

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS billing_mode VARCHAR(20) NOT NULL DEFAULT 'paid',
    ADD COLUMN IF NOT EXISTS billing_exempt_until DATE,
    ADD COLUMN IF NOT EXISTS billing_exempt_reason TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'drivers_billing_mode_check'
    ) THEN
        ALTER TABLE public.drivers
            ADD CONSTRAINT drivers_billing_mode_check
            CHECK (billing_mode IN ('paid', 'beta'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_drivers_billing_mode
    ON public.drivers (billing_mode);

CREATE INDEX IF NOT EXISTS ix_drivers_billing_exempt_until
    ON public.drivers (billing_exempt_until)
    WHERE billing_exempt_until IS NOT NULL;
