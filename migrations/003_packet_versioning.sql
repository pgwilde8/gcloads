-- 003_packet_versioning.sql
-- Packet document history + outbound snapshot receipts

CREATE TABLE IF NOT EXISTS driver_documents (
    id SERIAL PRIMARY KEY,
    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    doc_type VARCHAR(50) NOT NULL,
    file_key VARCHAR(255) NOT NULL,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    sha256_hash VARCHAR(64),
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_driver_documents_driver_active
    ON driver_documents(driver_id, is_active);

CREATE INDEX IF NOT EXISTS idx_driver_documents_driver_type_active
    ON driver_documents(driver_id, doc_type, is_active);

CREATE TABLE IF NOT EXISTS packet_snapshots (
    id SERIAL PRIMARY KEY,
    negotiation_id INTEGER REFERENCES negotiations(id) ON DELETE SET NULL,
    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    version_label VARCHAR(20),
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    recipient_email VARCHAR(255),
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_packet_negotiation
    ON packet_snapshots(negotiation_id);

CREATE INDEX IF NOT EXISTS idx_packet_snapshots_driver_sent_at
    ON packet_snapshots(driver_id, sent_at DESC);
