-- Migration 015: Backfill broker_emails from historical inbound Broker messages
--
-- Strategy:
--   Parse the embedded "From: Name <email@domain>" header that the inbound listener
--   stores verbatim in messages.body. Join through negotiations to get the mc_number.
--   Insert with source='inbound_history', confidence=0.65 (lower than Scout's 0.80
--   because we're extracting from raw text, not a structured contact block).
--   ON CONFLICT DO NOTHING — never overwrite existing entries.
--
-- Safe to run multiple times (idempotent).

INSERT INTO webwise.broker_emails (mc_number, email, source, confidence)
SELECT DISTINCT
    n.broker_mc_number                                                         AS mc_number,
    lower(trim(substring(m.body FROM 'From: .+ <([^>]+@[^>]+)>')))            AS email,
    'inbound_history'                                                          AS source,
    0.65                                                                       AS confidence
FROM public.messages m
JOIN public.negotiations n ON n.id = m.negotiation_id
WHERE m.sender = 'Broker'
  AND m.body ~ 'From: .+ <[^>]+@[^>]+>'
  -- Only promote into brokers we actually have in the vault
  AND EXISTS (
      SELECT 1 FROM webwise.brokers b WHERE b.mc_number = n.broker_mc_number
  )
  -- Skip obviously non-broker addresses (personal gmail / yahoo / hotmail / outlook)
  AND lower(substring(m.body FROM 'From: .+ <([^>]+@[^>]+)>'))
      NOT SIMILAR TO '%(gmail|yahoo|hotmail|outlook|icloud|me\.com|aol)%'
ON CONFLICT (mc_number, email) DO NOTHING;

-- Also backfill primary_email on brokers where it is currently NULL/empty,
-- picking the highest-confidence email we just inserted for that MC.
UPDATE webwise.brokers b
SET    primary_email = be.email,
       updated_at    = now()
FROM (
    SELECT DISTINCT ON (mc_number)
        mc_number,
        email
    FROM webwise.broker_emails
    WHERE source = 'inbound_history'
    ORDER BY mc_number, confidence DESC
) be
WHERE b.mc_number = be.mc_number
  AND (b.primary_email IS NULL OR b.primary_email = '');
