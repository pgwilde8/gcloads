# Green Candle Dispatch — Negotiation Simulator
Check the "Heartbeat": docker-compose logs -f inbound — Is it polling?

Verify Routing: docker-compose exec app python app/scripts/verify_routing.py --negotiation-id [ID] — Does the system see the load?

Audit the "Brain": Run the psql Audit Trail query (see section 1) — Why did the AI choose that zone?

Check SMTP: Look at gcloads_api logs — Did the email actually leave the server?

Emergency Override: If the AI is "stuck," manually update negotiation_status to WAITING_FOR_HUMAN

With the simulator, you can run fast, repeatable regression tests for the whole negotiation brain without waiting on inbox traffic.

Zone logic tests: verify GREEN/YELLOW/RED thresholds ($2,100, $1,850, $1,550) map to expected actions.
Rounding tests: ensure yellow-zone counters are professional increments ($2,150, not odd cents).
Guardrail tests: confirm floor protection when driver rates are set vs missing (fallback behavior).
Draft vs auto-send tests: use dry_run=true to force review path and confirm pending_review_* fields populate.
Audit-trail tests: assert messages gets zone reasoning logs every run.
Parser/price extraction tests: throw tricky broker text (2.1k, 21 hundred, $1,900*) and verify detected result path.
You can treat it like a mini test harness: run 10 messages in a row and compare endpoint response + DB messages for each case.
If you want, I can create a test_brain.py script that executes a full matrix and prints pass/fail per scenario.

## Purpose
The simulator endpoint lets you test negotiation behavior (zone logic, rounding, audit logs, draft behavior) without waiting on real IMAP email flow.

- Endpoint: `POST /api/test/simulate-broker`
- Route file: `app/main.py`
- Core logic executed: `app/logic/negotiator.py::handle_broker_reply`

## Security Model
- In `development`, endpoint can run without admin password.
- In non-development environments (ex: production), provide `admin_password`.
- This endpoint writes to DB (`messages`, and possibly pending-review fields).

## Request Parameters
| Name | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `negotiation_id` | int | Yes | — | Existing negotiation ID (ex: `10245`) |
| `message_text` | string | Yes | — | Simulated broker message |
| `dry_run` | bool | No | `true` | Forces review mode for this run to avoid outbound send |
| `admin_password` | string | Conditionally | — | Required in non-dev env |

## Important Behavior
- `dry_run=true` still executes real decision logic and writes audit trail messages.
- `dry_run=true` temporarily forces `review_before_send=True` during the run and restores original value after.
- If `action_taken=REVIEW_REQUIRED`, check `pending_review_action` + `pending_review_price` in response and DB.

## Curl Examples
### Yellow Zone (counter expected)
```bash
curl -sS -X POST http://127.0.0.1:8369/api/test/simulate-broker \
  -F "negotiation_id=10245" \
  -F 'message_text=We can only do $1,850 for this lane.' \
  -F "dry_run=true" \
  -F "admin_password=YOUR_ADMIN_PASSWORD"
```

### Red Zone (walk-away expected)
```bash
curl -sS -X POST http://127.0.0.1:8369/api/test/simulate-broker \
  -F "negotiation_id=10245" \
  -F 'message_text=Best we can do is $1,200 firm.' \
  -F "dry_run=true" \
  -F "admin_password=YOUR_ADMIN_PASSWORD"
```

### Green Zone (close stance expected)
```bash
curl -sS -X POST http://127.0.0.1:8369/api/test/simulate-broker \
  -F "negotiation_id=10245" \
  -F 'message_text=We can do the full $2,100 all-in.' \
  -F "dry_run=true" \
  -F "admin_password=YOUR_ADMIN_PASSWORD"
```

## Shell Quoting Tip (Critical)
Always single-quote `message_text` when it contains `$`.

✅ Good:
```bash
-F 'message_text=We can do $1,850 all-in.'
```

❌ Risky:
```bash
-F "message_text=We can do $1,850 all-in."
```

With double quotes, shell may expand `$1` and corrupt the value.

## Quick Audit Queries
### Latest messages for one negotiation
```bash
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c \
"SELECT id, sender, left(body,220) AS preview, timestamp \
 FROM public.messages \
 WHERE negotiation_id = 10245 \
 ORDER BY id DESC LIMIT 10;"
```

### Pending draft state
```bash
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c \
"SELECT id, pending_review_action, pending_review_price, left(pending_review_body,220) AS draft \
 FROM public.negotiations \
 WHERE id = 10245;"
```

## Expected Zone Outcomes (Current)
- `ratio >= 0.95` → Green zone: close/strong finalize posture.
- `0.80 <= ratio < 0.95` → Yellow zone: rounded counter (professional increments).
- `ratio < 0.80` → Red zone: walk away + explicit audit message.

## Troubleshooting
- `curl: (56) Recv failure` right after deploy usually means app is still booting; retry after health check.
- Health check:
```bash
curl -sS http://127.0.0.1:8369/health
```
- If endpoint seems stale, rebuild/recreate app container:
```bash
cd /srv/gcloads-app && docker-compose build app inbound
cd /srv/gcloads-app && docker rm -f $(docker ps -aq --filter name=gcloads_api --filter name=gcloads_inbound)
cd /srv/gcloads-app && docker-compose up -d app inbound
```

## Simulator & Fallback Runbook

### 1) Audit Trail: How to Read the Brain
When a broker reply is processed, decision reasoning is written to `messages`.

```bash
docker exec -it gcloads_db psql -U gcd_admin -d gcloads_db -c \
"SELECT sender, body FROM messages WHERE negotiation_id = 10245 ORDER BY timestamp DESC LIMIT 2;"
```

Interpretation shortcuts:
- RED ZONE: AI walked away. Check whether the driver floor is set too high.
- YELLOW ZONE: AI countered. Check that counter pricing is rounded to nearest `$50`.
- GREEN ZONE: AI is closing. Check that closing language requests Rate Con.

### 2) Fallback Path Troubleshooting
If an inbound email fails to route to dashboard negotiation context:

- Header fallback: if `To` is missing `+<id>` tag, inspect whether `X-GCD-Negotiation-ID` survived the broker reply chain.
- Subject fallback: verify `[GCD:<id>]` token still exists in subject thread.
- If both are stripped, the system can lose thread context and route will fall back to manual handling.

Action for zombie inbound:
- Manually re-link by updating the `messages.negotiation_id` row in Postgres after confirming source/authenticity.

### 3) Regression Testing Guardrail
Run matrix after any negotiation logic or prompt change:

```bash
docker-compose exec -T app python app/scripts/verify_matrix.py
```

Deploy rule:
- If matrix fails, do not deploy.
- Any failure means behavior drift that can cause lost margin or missed fair closures.

### 4) Dispute Resolution Queries (Packet Proof-of-Send)
When broker disputes packet content, query snapshot receipt + exact document IDs used at send time.

```bash
docker exec -it gcloads_db psql -U gcd_admin -d gcloads_db -c \
"SELECT
   ps.sent_at,
   ps.recipient_email,
   ps.version_label,
   dd.doc_type,
   dd.file_key
 FROM packet_snapshots ps
 JOIN driver_documents dd
  ON dd.id = ANY(
    SELECT jsonb_array_elements_text(ps.metadata->'doc_ids')::int
  )
 WHERE ps.negotiation_id = 10245
 ORDER BY ps.sent_at DESC, dd.doc_type ASC;"
```

Additional support checks:

```bash
docker exec -it gcloads_db psql -U gcd_admin -d gcloads_db -c \
"SELECT id, driver_id, doc_type, file_key, uploaded_at, is_active
 FROM driver_documents
 WHERE driver_id = 1
 ORDER BY doc_type, uploaded_at DESC;"
```

```bash
docker exec -it gcloads_db psql -U gcd_admin -d gcloads_db -c \
"SELECT id, negotiation_id, driver_id, version_label, sent_at, recipient_email, metadata
 FROM packet_snapshots
 WHERE driver_id = 1
 ORDER BY sent_at DESC
 LIMIT 20;"
```

### Week 1 Recap
- Routing hardened: Layer 1 (plus tag), Layer 2 (header), Layer 3 (subject token) active.
- Simulator matrix: 9-point verification live and validated (9/9 pass).
- Runbook: fallback and audit troubleshooting now documented for operations.

## Config Migration Plan (5 Steps, Safe Order)

Use this sequence to move from scattered environment reads to centralized typed settings without breaking live flows.

### Step 1 — Stabilize Core Toggle Settings First
Move only global feature toggles that affect multiple modules:
- `WATERMARK_ENABLED`
- future ledger toggles (example: fee rules or payout guards)

Why first:
- Low blast radius, fast validation, immediate operational visibility.

### Step 2 — Centralize Non-Secret Runtime Defaults
Move defaults that are used broadly and are safe to validate as types:
- app mode / environment
- public base URLs
- packet size limits and storage root defaults

Why second:
- Reduces duplicated fallback logic while avoiding secret-handling refactors early.

### Step 3 — Migrate Critical Infrastructure Settings with Validation
Move DB + SMTP/IMAP settings into typed config and mark required where appropriate.

Add startup checks so app fails fast when required values are missing.

Why third:
- Prevents mid-transaction failures in negotiation and inbound routing paths.

### Step 4 — Replace Direct Env Reads Incrementally by Domain
Refactor module-by-module (not all at once):
- email service
- inbound listener
- negotiation/simulator tools
- payments/ledger modules

After each domain migration:
- run local compile checks
- run simulator matrix
- run routing verifier

Why fourth:
- Keeps rollback simple and isolates regressions.

### Step 5 — Enforce Policy and Freeze Pattern
When coverage is high:
- ban new direct `os.getenv` calls in app code (except config module)
- add a lightweight review checklist item: "new env var must be declared in central settings"
- keep `.env` keys documented and grouped by subsystem

Why fifth:
- Locks consistency for future sprints (ledger, factoring recipes, support tooling).

### What to Move First vs Later
- Move first: global toggles and cross-cutting defaults.
- Move next: DB/email infra settings with required validation.
- Move later: niche or one-off script-only env values, unless they become production-critical.
