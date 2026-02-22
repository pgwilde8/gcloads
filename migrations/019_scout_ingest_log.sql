-- Migration 019: Scout ingest activity log
-- Persists each Scout ingest outcome so drivers can see "Recent Scout Activity" on the dashboard.

CREATE TABLE IF NOT EXISTS public.scout_ingest_log (
    id              SERIAL PRIMARY KEY,
    driver_id       INTEGER NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    load_id         INTEGER NOT NULL REFERENCES public.loads(id) ON DELETE CASCADE,
    next_step       VARCHAR(32) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_scout_ingest_log_driver_created
    ON public.scout_ingest_log (driver_id, created_at DESC);
