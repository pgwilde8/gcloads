-- 006_stripe_dispatch_fees.sql
-- Stripe customer/payment method storage + dispatch fee charge tracking

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255);

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS stripe_default_payment_method_id VARCHAR(255);

-- Add first, then set default (avoids surprises)
ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS stripe_payment_status VARCHAR(40);
ALTER TABLE public.drivers
  ALTER COLUMN stripe_payment_status SET DEFAULT 'UNSET';

-- Safer NOT NULL: add nullable, backfill, then set NOT NULL + default
ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS stripe_action_required BOOLEAN;

UPDATE public.drivers
SET stripe_action_required = FALSE
WHERE stripe_action_required IS NULL;

ALTER TABLE public.drivers
  ALTER COLUMN stripe_action_required SET NOT NULL;

ALTER TABLE public.drivers
  ALTER COLUMN stripe_action_required SET DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_drivers_stripe_customer_id
  ON public.drivers(stripe_customer_id);

CREATE TABLE IF NOT EXISTS public.dispatch_fee_payments (
  id SERIAL PRIMARY KEY,
  negotiation_id INTEGER NOT NULL REFERENCES public.negotiations(id) ON DELETE CASCADE,
  driver_id INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
  stripe_payment_intent_id VARCHAR(255),
  amount_cents INTEGER NOT NULL,
  currency VARCHAR(10) NOT NULL DEFAULT 'usd',
  status VARCHAR(40) NOT NULL DEFAULT 'PENDING',
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dispatch_fee_payments_negotiation
  ON public.dispatch_fee_payments(negotiation_id);

CREATE INDEX IF NOT EXISTS idx_dispatch_fee_payments_intent
  ON public.dispatch_fee_payments(stripe_payment_intent_id);