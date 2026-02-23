-- Migration 021: driver_invoices.is_exempt
-- Distinguishes exempt-settled invoices (beta/free) from cash-paid invoices.
-- Revenue reports should exclude is_exempt=TRUE from "cash collected."

ALTER TABLE public.driver_invoices
    ADD COLUMN IF NOT EXISTS is_exempt BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.driver_invoices.is_exempt IS
    'True when invoice was settled without payment (beta/exempt). Exclude from cash revenue.';
