-- 014_billing_tables.sql
-- Weekly billing infrastructure: driver_invoices, billing_runs, billing_run_items
-- Also fixes 013_call_logs.sql which incorrectly referenced public.brokers (brokers live in webwise schema)
-- All tables go in public schema — webwise is broker market intel only.

-- ============================================================
-- FIX: call_logs migration 013 had a bad FK to public.brokers
-- Drop and recreate correctly (broker_id is nullable/informational only)
-- ============================================================
DROP TABLE IF EXISTS public.call_logs CASCADE;

CREATE TABLE IF NOT EXISTS public.call_logs (
    id               SERIAL PRIMARY KEY,
    driver_id        INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    broker_id        INTEGER,  -- informational only, no FK (brokers live in webwise schema)
    negotiation_id   INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
    load_ref         VARCHAR(40),
    phone            VARCHAR(40),
    outcome          VARCHAR(20),
    rate             NUMERIC(10,2),
    notes            TEXT,
    next_follow_up_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_call_logs_driver_neg_created
    ON public.call_logs (driver_id, negotiation_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_call_logs_driver_id
    ON public.call_logs (driver_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_negotiation_id
    ON public.call_logs (negotiation_id);

-- ============================================================
-- billing_state on drivers
-- Separate from stripe_payment_status (which tracks Stripe setup state).
-- billing_state tracks weekly fee enforcement state.
-- Values: active | delinquent | suspended
-- ============================================================
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS billing_state VARCHAR(40) NOT NULL DEFAULT 'active';

-- ============================================================
-- driver_invoices
-- One row per delivered load. Source of truth for fee accrual.
-- Replaces/supersedes dispatch_fee_payments for the weekly billing flow.
-- dispatch_fee_payments is kept for historical per-negotiation charge records.
-- ============================================================
CREATE TABLE IF NOT EXISTS public.driver_invoices (
    id                       SERIAL PRIMARY KEY,
    driver_id                INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    negotiation_id           INTEGER NOT NULL REFERENCES public.negotiations(id) ON DELETE CASCADE,
    gross_amount_cents       INTEGER NOT NULL,           -- gross load value in cents
    fee_rate                 NUMERIC(6,4) NOT NULL DEFAULT 0.0250,
    fee_amount_cents         INTEGER NOT NULL,           -- gross_amount_cents * fee_rate, rounded
    status                   VARCHAR(40) NOT NULL DEFAULT 'pending',
    -- status values: pending | paid | failed | disputed | void
    billed_week_ending       DATE,                       -- set when included in a billing run
    stripe_payment_intent_id VARCHAR(255),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at                  TIMESTAMPTZ,
    CONSTRAINT uq_driver_invoices_negotiation UNIQUE (negotiation_id)
);

CREATE INDEX IF NOT EXISTS idx_driver_invoices_driver_status
    ON public.driver_invoices (driver_id, status);
CREATE INDEX IF NOT EXISTS idx_driver_invoices_week_ending
    ON public.driver_invoices (billed_week_ending);
CREATE INDEX IF NOT EXISTS idx_driver_invoices_stripe_pi
    ON public.driver_invoices (stripe_payment_intent_id);

-- ============================================================
-- billing_runs
-- One row per driver per week. Idempotency anchor.
-- ============================================================
CREATE TABLE IF NOT EXISTS public.billing_runs (
    id                       SERIAL PRIMARY KEY,
    driver_id                INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    week_ending              DATE NOT NULL,
    status                   VARCHAR(40) NOT NULL DEFAULT 'pending',
    -- status values: pending | success | failed | needs_reconcile | dry_run
    total_amount_cents       INTEGER NOT NULL DEFAULT 0,
    stripe_payment_intent_id VARCHAR(255),
    error_message            TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_billing_runs_driver_week UNIQUE (driver_id, week_ending)
);

CREATE INDEX IF NOT EXISTS idx_billing_runs_status
    ON public.billing_runs (status);
CREATE INDEX IF NOT EXISTS idx_billing_runs_week_ending
    ON public.billing_runs (week_ending);
CREATE INDEX IF NOT EXISTS idx_billing_runs_stripe_pi
    ON public.billing_runs (stripe_payment_intent_id);

-- ============================================================
-- billing_run_items
-- Maps invoices to their billing run. One invoice belongs to one run.
-- ============================================================
CREATE TABLE IF NOT EXISTS public.billing_run_items (
    id                  SERIAL PRIMARY KEY,
    billing_run_id      INTEGER NOT NULL REFERENCES public.billing_runs(id) ON DELETE CASCADE,
    driver_invoice_id   INTEGER NOT NULL REFERENCES public.driver_invoices(id) ON DELETE CASCADE,
    CONSTRAINT uq_billing_run_items_invoice UNIQUE (driver_invoice_id)
);

CREATE INDEX IF NOT EXISTS idx_billing_run_items_run_id
    ON public.billing_run_items (billing_run_id);
