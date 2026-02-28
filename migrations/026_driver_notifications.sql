-- Migration 026: driver_notifications table + notification preference columns
-- Supports in-app toast polling, email alerts, and future SMS opt-in.

CREATE TABLE IF NOT EXISTS driver_notifications (
    id            SERIAL PRIMARY KEY,
    driver_id     INTEGER      NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    notif_type    VARCHAR(40)  NOT NULL,   -- LOAD_MATCH | AUTO_SENT | BROKER_REPLY | LOAD_WON
    message       TEXT         NOT NULL,
    payload       JSONB        DEFAULT '{}',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    delivered_at  TIMESTAMPTZ  DEFAULT NULL   -- NULL = unread/undelivered
);

CREATE INDEX IF NOT EXISTS ix_driver_notif_driver_unread
    ON driver_notifications (driver_id, delivered_at)
    WHERE delivered_at IS NULL;

-- Notification preference columns on drivers
ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS notif_email_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS notif_sms_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS notif_quiet_start     SMALLINT DEFAULT 22,  -- 10 PM local
    ADD COLUMN IF NOT EXISTS notif_quiet_end       SMALLINT DEFAULT 6,   -- 6 AM local
    ADD COLUMN IF NOT EXISTS notif_email_digest    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS phone                 VARCHAR(30) DEFAULT NULL;
