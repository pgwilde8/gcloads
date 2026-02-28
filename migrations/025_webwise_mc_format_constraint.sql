-- Migration 025: Enforce normalized MC number format on webwise.brokers
-- Valid formats:
--   \d{4,8}   standard motor carrier MC (digits only, 4-8 chars)
--   FF\d+     freight forwarder MC (FMCSA FF prefix)
-- This blocks raw strings like "MC123456", "mc 123456", or other garbage
-- from being written directly to the vault.

ALTER TABLE webwise.brokers
    ADD CONSTRAINT ck_brokers_mc_number_format
    CHECK (mc_number ~ '^\d{4,8}$' OR mc_number ~ '^FF\d+$');
