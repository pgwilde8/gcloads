CREATE INDEX IF NOT EXISTS idx_call_logs_driver_neg_created
ON call_logs (driver_id, negotiation_id, created_at DESC);
-- Migration: Call Logs Table
CREATE TABLE IF NOT EXISTS public.call_logs (
    id SERIAL PRIMARY KEY,
    driver_id INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    broker_id INTEGER REFERENCES public.brokers(id) ON DELETE SET NULL,
    negotiation_id INTEGER REFERENCES public.negotiations(id) ON DELETE SET NULL,
    load_ref VARCHAR(40),
    phone VARCHAR(40),
    outcome VARCHAR(20),
    rate NUMERIC(10,2),
    notes TEXT,
    next_follow_up_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS call_logs_driver_id_idx ON public.call_logs(driver_id);
CREATE INDEX IF NOT EXISTS call_logs_broker_id_idx ON public.call_logs(broker_id);
CREATE INDEX IF NOT EXISTS call_logs_negotiation_id_idx ON public.call_logs(negotiation_id);
