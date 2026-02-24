-- Migration 023: Drop drivers.balance (credits removal Phase 2)
-- All reads/writes removed in Phase 1. Column was unused legacy from $CANDLE/wallet concept.

ALTER TABLE public.drivers
    DROP COLUMN IF EXISTS balance;
