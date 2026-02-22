Good. This is exactly what disciplined builders do — freeze the current architecture in writing so future-you doesn’t reverse-engineer your own system.

Below is a clean, technical README you can drop into:

```
docs/architecture/scout_and_broker_enrichment.md
```

or similar.

No fluff. Just truth.

---

# CoDriver Freight

## Scout + Broker Enrichment Architecture

This document explains:

* How the Scout Chrome Extension works
* How load ingestion flows through the API
* How broker contact intelligence is harvested
* How broker directory enrichment compounds over time
* Where data is stored and how it evolves

---

# 1. High-Level Overview

The system creates a **self-enriching broker intelligence loop**:

```
Driver browses load board
→ Scout injects button
→ Driver clicks “SHIP TO DISPATCH”
→ Scout harvests load + broker data
→ POST /api/scout/ingest
→ Load stored/updated
→ Broker promotion fills directory gaps
→ triage_broker_contact() decides next action
→ Email or Call workflow
→ Future loads become lower-friction
```

Over time, the broker directory improves automatically.

No external enrichment API required.

---

# 2. Scout Extension

Location:

```
/srv/gcloads-app/scout-extension
```

Files:

* manifest.json
* content.js
* options.html
* options.js

## Supported Load Boards

* trucksmarter.com
* dat.com
* truckstop.com

## What Scout Harvests

From page DOM + body text:

* load_id
* source (hostname)
* mc_number
* dot_number
* email (mailto: first, regex fallback)
* phone (tel: first, regex fallback)
* price
* origin
* destination
* equipment_type
* raw_notes (email only / call only detection)

Scout then POSTs to:

```
POST /api/scout/ingest
```

With:

```
x-api-key header
```

---

# 3. Scout Ingest Flow (Backend)

Route:

```
app/routes/ingest.py
@scout_router.post("/ingest")
```

### Ingest Steps

1. Normalize metadata
2. Merge contact_info (email + phone)
3. Resolve contact mode via:

   ```
   resolve_contact_mode()
   ```
4. Insert or update Load row
5. Promote broker data (NEW)
6. Run triage_broker_contact()
7. Optionally auto-send negotiation email
8. Return next action to extension

---

# 4. Contact Mode Resolution

File:

```
app/services/parser_rules.py
```

Uses:

```
config/parsing_rules.json
```

Keywords detect:

* "call only"
* "call for rate"
* "no email"
* "email bids only"
* etc.

Output:

```
"call"
or
"email"
```

Default = email.

---

# 5. Broker Directory Structure

Schema:

```
webwise.brokers
webwise.broker_emails
```

## webwise.brokers (Primary Vault)

Primary key:

```
mc_number
```

Key fields:

* company_name
* dot_number
* primary_email
* primary_phone
* preferred_contact_method
* source
* internal_note

## webwise.broker_emails

Fields:

* mc_number
* email
* confidence NUMERIC(4,3)
* source TEXT

Unique constraint:

```
(mc_number, email)
```

---

# 6. Broker Promotion Service (NEW)

File:

```
app/services/broker_promotion.py
```

Purpose:
Promote Scout-harvested contact data into broker vault safely.

### Rules

1. Broker must already exist in webwise.brokers

   * No creation from Scout alone
2. Never overwrite manual/admin data
3. Only fill empty fields

### Promotion Logic

Email:

* Insert into broker_emails
* confidence = 0.800
* source = 'scout'
* ON CONFLICT DO NOTHING
* Fill primary_email only if empty

Phone:

* Strip non-digits
* Require >= 10 digits
* Fill primary_phone only if empty

DOT:

* Fill dot_number only if empty

Contact mode:

* Fill preferred_contact_method only if empty

Logging paths:

* broker_not_in_vault → INFO
* new_email_inserted → INFO
* enriched_fields → INFO
* email_already_exists → DEBUG

---

# 7. Broker Triage Engine

File:

```
app/services/broker_intelligence.py
```

Function:

```
triage_broker_contact()
```

Decision Tree:

1. Check driver-specific BrokerOverride
2. Check global BrokerOverride
3. Check broker internal_note
4. If BLACKLISTED → block
5. If CALL required → return CALL_REQUIRED
6. If BrokerEmail exists → EMAIL_BROKER
7. If broker exists but no email → CALL_REQUIRED
8. If broker missing → ENRICHMENT_QUEUED

Output example:

```
{
  action: "EMAIL_BROKER",
  email: "...",
  phone: "...",
  standing: {...}
}
```

---

# 8. Auto-Bid Flow

Inside ingest route:

```
enqueue_auto_bid()
```

Conditions:

* auto_bid = True
* broker_email exists
* driver exists
* mc_number exists
* negotiation not already created
* not blacklisted

If valid:

* Create Negotiation row
* Background send email

---

# 9. Message Routing (Broker Replies)

Inbound routing priority:

Layer 1:
Plus-tag numeric ID
→ deterministic, cannot mis-route

Layer 2:
handle+load_ref fallback

Layer 3:
subject parsing fallback (safe, ambiguity → manual review)

Conflict detection:
Logs warning if plus-tag ID != claimed ID.

No routing change — logging only.

---

# 10. Historical Backfill (Optional)

Migration:

```
migrations/015_backfill_broker_emails_from_messages.sql
```

Extracts:

```
From: Name <email@domain>
```

Filters:

* Excludes personal domains (gmail, yahoo, etc.)
* Idempotent
* confidence = 0.65
* source = 'inbound_history'
* Fills primary_email only if empty

Currently no real broker data present in dev DB.

---

# 11. Current Data Reality (Dev Environment)

webwise.brokers:
25,107 rows (mostly fmcsa_api)

Contact coverage:
~75 phones
~64 emails
1 scout-enriched email

This is expected due to:

* No real broker traffic
* No production drivers

---

# 12. What This Architecture Achieves

1. Zero paid enrichment services required
2. Intelligence compounds over time
3. Scout + inbound replies both grow directory
4. Auto-bid coverage improves automatically
5. Triage decisions become smarter per broker

This becomes a long-term competitive moat.

---

# 13. Known Truths

* FMCSA preferred_contact_method='email' is default import value, not truth.
* No automatic broker creation from Scout.
* Enrichment is gap-fill only.
* Confidence scores differentiate sources.

---

# 14. Next Logical Enhancements (Optional)

1. Inbound listener → auto-promote broker email on reply
2. Track enrichment source frequency
3. Increase email confidence after successful negotiation
4. Add broker success metrics (win rate per MC)

---

# System Status

Scout → Stable
Broker Promotion → Live
Triage → Live
Auto-Bid → Live
Inbound Routing → Deterministic
Backfill → Ready


