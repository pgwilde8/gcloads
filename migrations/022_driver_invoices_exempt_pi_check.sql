-- Migration 022: Enforce is_exempt / stripe_payment_intent_id consistency
-- Exempt-settled invoices must have pi_id NULL. Cash-paid invoices must have is_exempt FALSE.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_driver_invoices_exempt_pi'
    ) THEN
        ALTER TABLE public.driver_invoices
            ADD CONSTRAINT chk_driver_invoices_exempt_pi
            CHECK (
                (is_exempt = FALSE) OR (stripe_payment_intent_id IS NULL)
            );
    END IF;
END $$;

COMMENT ON CONSTRAINT chk_driver_invoices_exempt_pi ON public.driver_invoices IS
    'Exempt-settled invoices (is_exempt=TRUE) cannot have stripe_payment_intent_id. Cash-paid must have is_exempt=FALSE.';
