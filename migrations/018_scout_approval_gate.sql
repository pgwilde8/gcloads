-- Migration 018: Scout Match Scoring + Approval Gate
-- Adds driver profile columns for match scoring and new negotiation statuses.

-- ── public.drivers new columns ────────────────────────────────────────────────
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS preferred_equipment_type    TEXT,
    ADD COLUMN IF NOT EXISTS auto_send_on_perfect_match  BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS approval_threshold          SMALLINT NOT NULL DEFAULT 3;

-- ── public.negotiations: unique constraint (driver, load) ─────────────────────
-- Prevents duplicate negotiations for the same driver+load pair.
-- NOTE: This constraint requires no duplicate (driver_id, load_id) rows to exist.
-- If existing data has duplicates (e.g. test data), resolve them first, then run:
--   ALTER TABLE public.negotiations ADD CONSTRAINT uq_negotiations_driver_load UNIQUE (driver_id, load_id);
-- The application enforces idempotency via SELECT-before-INSERT regardless.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_negotiations_driver_load'
    ) THEN
        -- Only add if no duplicates exist
        IF NOT EXISTS (
            SELECT 1 FROM public.negotiations
            GROUP BY driver_id, load_id HAVING COUNT(*) > 1
        ) THEN
            ALTER TABLE public.negotiations
                ADD CONSTRAINT uq_negotiations_driver_load
                UNIQUE (driver_id, load_id);
        END IF;
    END IF;
END $$;

-- ── public.negotiations: match scoring columns ────────────────────────────────
ALTER TABLE public.negotiations
    ADD COLUMN IF NOT EXISTS match_score      SMALLINT,
    ADD COLUMN IF NOT EXISTS match_details    JSONB;

-- ── public.loads: driver_id column (which driver ingested this load) ──────────
ALTER TABLE public.loads
    ADD COLUMN IF NOT EXISTS ingested_by_driver_id INTEGER REFERENCES public.drivers(id) ON DELETE SET NULL;
