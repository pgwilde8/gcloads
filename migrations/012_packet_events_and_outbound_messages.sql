-- 012_packet_events_and_outbound_messages.sql
-- Add source-version metadata, compose observability, and outbound email receipts.

ALTER TABLE IF EXISTS driver_documents
    ADD COLUMN IF NOT EXISTS source_version VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_driver_documents_source_version
    ON driver_documents(driver_id, negotiation_id, doc_type, source_version, is_active);

CREATE TABLE IF NOT EXISTS public.packet_events (
    id BIGSERIAL PRIMARY KEY,
    negotiation_id INTEGER REFERENCES negotiations(id) ON DELETE SET NULL,
    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    doc_type VARCHAR(64) NOT NULL,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    meta_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_packet_events_neg_created
    ON public.packet_events(negotiation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_packet_events_driver_created
    ON public.packet_events(driver_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.outbound_messages (
    id BIGSERIAL PRIMARY KEY,
    negotiation_id INTEGER REFERENCES negotiations(id) ON DELETE SET NULL,
    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL DEFAULT 'email',
    recipient VARCHAR(255) NOT NULL,
    subject VARCHAR(512) NOT NULL,
    attachment_doc_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbound_messages_neg_created
    ON public.outbound_messages(negotiation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_outbound_messages_driver_created
    ON public.outbound_messages(driver_id, created_at DESC);
