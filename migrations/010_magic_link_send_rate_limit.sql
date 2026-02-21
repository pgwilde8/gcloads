-- 010_magic_link_send_rate_limit.sql
-- Adds persistence for per-email/per-IP magic-link send throttling.

CREATE TABLE IF NOT EXISTS magic_link_send_attempts (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    client_ip VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_magic_link_send_attempts_email_created
    ON magic_link_send_attempts(email, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_magic_link_send_attempts_ip_created
    ON magic_link_send_attempts(client_ip, created_at DESC);
