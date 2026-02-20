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
