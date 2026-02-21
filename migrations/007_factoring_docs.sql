-- 007_factoring_docs.sql
-- Add negotiation-scoped document metadata + factoring tracking columns

ALTER TABLE IF EXISTS negotiations
    ADD COLUMN IF NOT EXISTS factoring_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS factored_at TIMESTAMPTZ;

ALTER TABLE IF EXISTS driver_documents
    ADD COLUMN IF NOT EXISTS negotiation_id INTEGER REFERENCES negotiations(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS bucket VARCHAR(255);

ALTER TABLE IF EXISTS driver_documents
    ALTER COLUMN file_key TYPE VARCHAR(1024);

CREATE INDEX IF NOT EXISTS idx_driver_documents_driver_neg_type_active
    ON driver_documents(driver_id, negotiation_id, doc_type, is_active);
