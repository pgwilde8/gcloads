-- Migration 027: Notification hardening
--
-- 1. drivers.last_seen_at   – updated on every authenticated page hit; used for
--                             session-suppression (skip email if active < 5 min ago).
-- 2. drivers.timezone       – IANA tz string (e.g. "America/Chicago"); used for
--                             quiet-hours evaluation in driver local time.
-- 3. driver_notifications.dedupe_key – prevents duplicate alerts when /ingest is
--                             re-posted for the same load+action.

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS last_seen_at  TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS timezone      VARCHAR(60)  DEFAULT 'America/Chicago';

ALTER TABLE driver_notifications
    ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(120) DEFAULT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ix_driver_notif_dedupe
    ON driver_notifications (dedupe_key)
    WHERE dedupe_key IS NOT NULL;
