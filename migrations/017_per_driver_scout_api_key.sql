-- Migration 017: per-driver Scout API keys
-- Each driver gets a unique 64-char hex key used to authenticate
-- Scout extension requests.  The ingest endpoint resolves the driver
-- from this key rather than trusting a client-supplied driver_id field.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS scout_api_key VARCHAR(64) UNIQUE;

-- Back-fill any rows that don't have a key yet (idempotent).
UPDATE public.drivers
SET scout_api_key = encode(gen_random_bytes(32), 'hex')
WHERE scout_api_key IS NULL;
