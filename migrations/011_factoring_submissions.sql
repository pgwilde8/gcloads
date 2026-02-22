-- Migration: Factoring Submissions Table
CREATE TABLE IF NOT EXISTS public.factoring_submissions (
    id SERIAL PRIMARY KEY,
    negotiation_id INTEGER NOT NULL REFERENCES public.negotiations(id) ON DELETE CASCADE,
    driver_id INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    to_email VARCHAR(255) NOT NULL,
    packet_doc_type VARCHAR(40) NOT NULL DEFAULT 'NEGOTIATION_PACKET',
    packet_bucket VARCHAR(255) NOT NULL,
    packet_key TEXT NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'QUEUED',
    error_message TEXT,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS factoring_submissions_negotiation_id_idx ON public.factoring_submissions(negotiation_id);
CREATE INDEX IF NOT EXISTS factoring_submissions_driver_id_idx ON public.factoring_submissions(driver_id);
CREATE INDEX IF NOT EXISTS factoring_submissions_status_idx ON public.factoring_submissions(status);
