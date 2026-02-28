-- Migration 024: Add broker_match_status to loads table
-- Tracks the outcome of broker resolution at ingest time.
-- Values: resolved | unknown_mc | missing_mc | malformed_mc

ALTER TABLE loads
    ADD COLUMN IF NOT EXISTS broker_match_status VARCHAR(30) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS ix_loads_broker_match_status
    ON loads (broker_match_status);
