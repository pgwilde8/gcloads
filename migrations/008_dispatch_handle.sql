-- 008_dispatch_handle.sql
-- Separate human display name from dispatch handle used for routing aliases.

ALTER TABLE IF EXISTS drivers
    ADD COLUMN IF NOT EXISTS dispatch_handle VARCHAR(20);

UPDATE drivers
SET dispatch_handle = LEFT(
    COALESCE(
        NULLIF(REGEXP_REPLACE(LOWER(display_name), '[^a-z0-9]+', '', 'g'), ''),
        NULLIF(REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)), '[^a-z0-9]+', '', 'g'), ''),
        'driver'
    ),
    20
)
WHERE dispatch_handle IS NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_dispatch_handle
    ON drivers(dispatch_handle);
