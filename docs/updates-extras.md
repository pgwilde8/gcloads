# Call-required Loads Verification

## SQL
```
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c "\nSELECT id, driver_id, load_ref, phone, outcome, rate, created_at\nFROM call_logs ORDER BY id DESC LIMIT 10;"
```

## Browser
- Open dashboard, find a load with CALL required.
- Click Call Broker, log a call, see UI success feedback.
# Lane B: Factoring Send Verification

1) Show driver factor email set:
	SELECT id, factor_packet_email, onboarding_status FROM drivers WHERE id=...;

2) Compose full packet exists:
	SELECT doc_type, bucket, file_key FROM driver_documents WHERE negotiation_id=... AND doc_type='NEGOTIATION_PACKET' AND is_active=true;

3) Insert QUEUED then SENT row:
	SELECT negotiation_id, status, to_email, packet_key, sent_at, error_message FROM factoring_submissions WHERE negotiation_id=...;

4) Missing factor email produces 409 with redirect_url and no submission row created.

5) Pending-century produces 409 blocked.
This is *really* good product thinking. You’re solving three painful realities in trucking dispatch all at once:

1. **“Carrier packet” chaos** (insurance/W-9/authority/etc)
2. **“Professional identity”** (not gmail/yahoo)
3. **“Paperwork latency”** (BOL → factoring packet fast)

And your **plus-address tagging** idea is a clean, scalable way to keep the broker directory normalized while still meeting each load board’s routing/sorting behavior. That’s exactly how you build a system that doesn’t rot over time.

Here are the important details to lock in so it stays bulletproof.

---

## 1) Carrier packet auto-attach: do it with “packet versions”

During onboarding, drivers upload docs once — great. But documents expire/change.

**Must-have pattern: `packet_version`**

* Each driver has a “current packet” version (v1, v2, v3…)
* Each doc has `uploaded_at`, `expires_at` (for insurance), and `hash`
* When they press “Send Packet to Broker”, you attach **the current version** and log exactly what was sent.

Why it matters:

* Brokers dispute “you didn’t send insurance” → you show exact sent packet + timestamp.
* Driver updates insurance mid-week → you’re always sending the latest.

**Minimum schema idea**

* `driver_documents(id, driver_id, doc_type, file_key, uploaded_at, expires_at, sha256, is_active)`
* `driver_packet_versions(id, driver_id, version, created_at)`
* `driver_packet_version_items(packet_version_id, document_id)`

When insurance updates: create a new packet version.

---

## 2) Factoring packet: make it a templated “packet recipe”

You already have the right flow: BOL photo → assemble packet → send.

Do it as a recipe by factoring company:

* Century recipe = BOL + RateCon + Invoice + NOA + W-9 + Insurance
* OTR recipe might differ
* Some want POD separate, etc.

So store:

* `factor_packet_templates(factor_company, required_docs[], optional_docs[])`
* then your “build packet” step is deterministic.

This will let you scale to multiple factoring partners without rewriting logic.

---

## 3) Professional negotiation domain: make deliverability non-negotiable

This is a power move. Drivers *instantly* look legit when negotiating from a branded domain.

Just be sure you’ve nailed:

* SPF
* DKIM
* DMARC (at least p=none at first, then tighten)
* Dedicated sending subdomain (e.g. `mail.greencandledispatch.com`)
* Separate negotiation mailbox namespace (e.g. `driver123@dispatch.greencandle…` or aliasing)

Deliverability is the difference between “wow this works” and “brokers never reply.”

---

## 4) Your plus-address tagging strategy: ✅ strong, but add fallbacks

Plus-addressing is widely supported, but some legacy systems *strip or break* it.

So implement a **3-layer routing strategy**:

### Layer A (primary): plus-tag

`gcs_parade+dat@geodis.com`

### Layer B (fallback): header tag

Always include a header like:

* `X-GCD-Load-Source: dat`
* `X-GCD-Negotiation-ID: 678`
* `X-GCD-Load-ID: 12345`

Even if plus-tags break, your inbound parser can still route.

### Layer C (final fallback): subject token

Append a short token at end of subject:
`Load Inquiry [GCD:12345:678]`

This makes routing resilient even in weird mail gateways.

---

## 5) Normalize tags carefully (your mapping is good)

One tweak: keep tags **short** and consistent; some systems have limits.

Good:

* `+dat`
* `+ts` (truckstop)
* `+tsm` (trucksmarter)

You can keep the human-readable mapping internally, but send short tags for compatibility.

Also: if load_source is unknown, use `+na` instead of no tag (optional) so everything is consistently tagged.

---

## 6) You’re building the “one-button dispatch identity”

Your flow is exactly the “aha” moment for drivers:

* Upload docs once
* Look professional to brokers
* One button sends packet
* One photo completes packet to factor

That’s a product that sells itself in a truck stop conversation.

If you want a simple one-liner for the postcard:

**“Upload your carrier packet once. Book loads from a professional dispatch email. One tap sends packet to broker. One photo builds your factoring packet.”**

---

## 7) One security note (important)

Since you’re acting as the system that sends docs:

* Always watermark packets with: driver name + MC + “Sent via Green Candle Dispatch” + timestamp (small footer)
* Limit access to documents by strict driver_id checks
* Use signed URLs with short expiry if you ever allow viewing links
* Log every outbound packet event (who/when/what docs)

This protects you if someone ever claims you sent the wrong thing or leaked docs.

---

If you want, paste your `add_load_board_tag()` function and I’ll tighten it (edge cases: existing tags, quoted local-parts, subaddressing already present, unusual emails). Also tell me what your negotiation “From” format looks like (e.g., `Dispatch Desk <driver123@dispatch.greencandledispatch.com>`), and I’ll suggest the most deliverable setup.


---

## Sprint 1 Implementation Brief (Routing Hardening First)

### Objective
Ship the highest-impact reliability upgrades first, then move into packet versioning and factoring recipes.

### Priority Order

1. **Routing Hardening (Now)**
2. **Packet Versioning + Send Snapshot Logging**
3. **Factoring Recipe Templates**

---

## Workstream A — Routing Hardening (Immediate)

### Scope

- Keep plus-tag routing as primary (`driver+token@...`).
- Add subject token fallback (ex: `[GCD:<load_id>:<negotiation_id>]`).
- Add custom SMTP headers fallback:
	- `X-GCD-Load-Source`
	- `X-GCD-Load-ID`
	- `X-GCD-Negotiation-ID`
- Normalize short outbound source tags:
	- `dat`, `ts`, `tsm`

### Acceptance Criteria

- Inbound routing succeeds when plus-tags are present.
- Inbound routing still succeeds when plus-tags are missing but headers exist.
- Inbound routing still succeeds when headers are missing but subject token exists.
- Simulator tests cover all three fallback layers.

### Effort / Risk

- **Effort:** Medium
- **Risk:** Low-to-medium (parser branching complexity)

---

## Workstream B — Packet Versioning + Audit Shield

### Scope

- Introduce immutable document history with active/inactive pointers.
- Create packet snapshots for each outbound send event.
- Store exact version membership and timestamp for broker-facing packet sends.

### Minimum Schema (proposed)

- `document_archive(id, driver_id, doc_type, file_key, uploaded_at, expires_at, sha256, is_active)`
- `packet_snapshots(id, driver_id, packet_version, sent_at, recipient_email, negotiation_id, metadata_json)`
- `packet_snapshot_items(snapshot_id, document_id)`

### Acceptance Criteria

- Every packet send records exactly which doc IDs were sent.
- Insurance refresh creates a new packet version without mutating prior history.
- Support can answer “what did we send, when, to whom?” from DB alone.

### Effort / Risk

- **Effort:** Medium-to-high
- **Risk:** Medium (schema + migration + service integration)

---

## Workstream C — Factoring Recipe Templates

### Scope

- Create deterministic factoring packet recipes by partner.
- Required/optional doc lists stored in configuration table.
- Packet builder reads template and assembles deterministic output.

### Minimum Schema (proposed)

- `factoring_configs(id, company_name, required_docs_json, optional_docs_json, is_active)`

### Acceptance Criteria

- Selecting a factoring company produces predictable required-doc checklist.
- Missing required docs fail fast with clear user-facing reason.
- Recipe updates do not require code changes.

### Effort / Risk

- **Effort:** Medium
- **Risk:** Low-to-medium

---

## Non-Negotiable Guardrails

- Keep zone strategy + decision audit logs in negotiation flow.
- Keep simulator endpoint as primary regression harness before inbox tests.
- Keep unknown source policy consistent across system (`no tag` vs `+na`) — choose one and document it once.

---

## Suggested Two-Week Execution Plan

### Week 1

- Finish routing hardening with subject/header fallback.
- Add simulator regression matrix for green/yellow/red + fallback layers.
- Add runbook examples for each fallback path.

### Week 2

- Add packet versioning tables + migration.
- Implement outbound packet snapshot logging.
- Add support query snippets for dispute resolution.

---

## Success Metrics

- Fewer unroutable inbound replies.
- Faster triage when brokers dispute packet contents.
- Lower support time to resolve packet/email incidents.
- Cleaner onboarding to factoring workflows.


This is good. You’re not just coding — you’re actually understanding your system. That’s how you avoid subtle production landmines later.

Let me zoom out and frame what you’ve uncovered in a clean way, because this matters architecturally.

---

## 1️⃣ Your Two-Schema Architecture Is Actually Strong

You’ve unintentionally built something very sane:

### `webwise` = Market Intelligence Layer

* 25k+ brokers
* Broker validation vault
* Canonical MC numbers
* Email gold list

This is reference data.
It should be relatively static.
It validates operations.

### `public` = Live Operational Layer

* drivers
* loads
* negotiations
* messages
* scout_status
* billing tables (now)

This is transactional data.
High write volume.
Time-sensitive.
Driver-scoped.

That separation is enterprise-grade whether you intended it or not.

Don’t collapse it later unless you have a strong reason.

---

## 2️⃣ The Foreign Key Lesson Was Important

Your `negotiations` FK to `webwise.brokers` is doing something powerful:

> You cannot negotiate with a broker that isn’t validated.

That prevents:

* Typos
* Random MC spam
* Dirty data
* Email listener chaos

That’s good friction.

But it also means:
If the broker vault is wrong or missing MC → your whole inbound flow silently fails.

That’s something to remember when debugging production.

---

## 3️⃣ The Email + display_name Coupling

This part is fragile long-term:

> plus-tag must match display_name

It works.
But it’s a coupling between identity and email routing.

At scale, you may eventually want:

* email token referencing driver_id directly
* or a routing table separate from display_name

Not urgent — just planting the seed.

---

## 4️⃣ The Real-Time Monitoring Command

If you want a clean production-friendly monitor without clutter:

```bash
watch -n 2 "docker exec -t gcloads_db psql -U gcd_admin -d gcloads_db -c \
\"SELECT id, negotiation_id, sender, timestamp, is_read 
FROM public.messages 
ORDER BY timestamp DESC 
LIMIT 10;\""
```

If you want to monitor a specific negotiation (10245):

```bash
watch -n 2 "docker exec -t gcloads_db psql -U gcd_admin -d gcloads_db -c \
\"SELECT id, sender, timestamp, is_read 
FROM public.messages 
WHERE negotiation_id = 10245
ORDER BY timestamp DESC;\""
```

That’s your live heartbeat.

---

## 5️⃣ Important: Don’t Let Intelligence Drift

You now have:

* Broker vault in `webwise`
* Negotiations FK-bound to it
* Messages tied by integer negotiation_id
* Billing tied to negotiations

That’s a chain:

Broker → Negotiation → Messages → Invoice → Billing Run

That chain must stay clean.

Any time you:

* change schemas
* rename fields
* loosen FKs
* allow “temporary brokers”

You risk breaking the chain.

---

## 6️⃣ Bigger Strategic Insight

What you’ve built is not “dispatch software.”

It’s a constrained operations system:

* Identity is tight
* Brokers are validated
* Loads are linked
* Messages are structured
* Billing is deterministic

That’s why the accounting feature will work.
Because your data graph is real.

---

Now let me pivot this back to something important.

You’ve got:

* Negotiation engine stable
* Broker vault validated
* Billing engine built
* Weekly autopay strict

The next serious risk area in your system is:

**Email reliability and threading integrity.**

Because that’s your revenue engine.

So let me ask you directly:

Have you verified that inbound listener:

* always resolves negotiation_id deterministically,
* and cannot mis-attach a message to the wrong negotiation if subjects are malformed?

That’s the next place subtle chaos hides.

