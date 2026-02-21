-- 011_driver_documents_readiness_index.sql
-- Speeds up packet readiness checks for active Golden Trio docs.

CREATE INDEX IF NOT EXISTS idx_driver_documents_readiness
ON public.driver_documents (driver_id, doc_type)
WHERE is_active = true;
