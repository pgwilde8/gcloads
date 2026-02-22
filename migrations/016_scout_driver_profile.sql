-- Migration 016: Scout driver filter profile
-- Adds three columns to public.drivers for Scout setup/activation.
-- preferred_origin_region and preferred_destination_region were applied manually
-- during initial setup; this file documents and idempotently ensures all three exist.

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS preferred_origin_region      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS preferred_destination_region VARCHAR(100),
    ADD COLUMN IF NOT EXISTS scout_active                 BOOLEAN NOT NULL DEFAULT false;
