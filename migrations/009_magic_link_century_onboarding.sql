-- 009_magic_link_century_onboarding.sql
-- Magic-link auth + onboarding/factoring state + Century referrals

ALTER TABLE IF EXISTS drivers
    ADD COLUMN IF NOT EXISTS dot_number VARCHAR(20),
    ADD COLUMN IF NOT EXISTS onboarding_status VARCHAR(30),
    ADD COLUMN IF NOT EXISTS factor_type VARCHAR(30),
    ADD COLUMN IF NOT EXISTS factor_packet_email VARCHAR(255),
    ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

UPDATE drivers
SET onboarding_status = COALESCE(onboarding_status, 'active'),
    email_verified_at = COALESCE(email_verified_at, created_at)
WHERE email IS NOT NULL;

UPDATE drivers
SET factor_type = COALESCE(factor_type, 'existing')
WHERE onboarding_status = 'active';

CREATE TABLE IF NOT EXISTS magic_link_tokens (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_email
    ON magic_link_tokens(email, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_hash
    ON magic_link_tokens(token_hash);

CREATE TABLE IF NOT EXISTS century_referrals (
    id SERIAL PRIMARY KEY,
    driver_id INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'SUBMITTED',
    payload JSONB NOT NULL,
    submitted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_century_referrals_driver
    ON century_referrals(driver_id, submitted_at DESC);
