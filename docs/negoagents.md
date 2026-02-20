# The 3 Musketeer Agents (Your Stack Today)

You built a **real multi-agent system** without needing OpenAI-only infrastructure.

## TL;DR

Your three agents are:

1. **Scout** (Finds + ingests loads)
2. **Closer** (Decides negotiation move + drafts/sends response)
3. **Commander Joe** (Human oversight + approval + exception handling)

This is the **broke dev way** in the best sense: deterministic logic, async services, Postgres state, IMAP/SMTP plumbing, and human control when needed.

---

## 1) Scout Agent

### Mission
- Ingest new opportunities from the extension/load source.
- Normalize broker/contact metadata.
- Create or update `loads` records.
- Optionally open a negotiation and send first touch.

### Core Behavior
- Entry point: `POST /api/scout/ingest`
- Validates `SCOUT_API_KEY`.
- Builds canonical load object (`origin`, `destination`, `price`, `equipment`, `mc_number`, metadata).
- Runs broker triage to determine contact strategy.
- If conditions are good and `auto_bid=true`, creates `negotiations` row and triggers outbound opener.

### Why it matters
- Scout is your **input filter** and **pipeline starter**.
- It keeps bad/missing contact data from polluting downstream negotiation loops.

---

## 2) Closer Agent

### Mission
- Read broker replies.
- Extract price signal.
- Decide whether to counter, close, or walk.
- Generate dispatcher-style message and send (or draft for approval).

### Core Logic
- `process_negotiation_logic(...)`
	- Uses driver floor policy (`min_cpm`, `min_flat_rate`) + detected broker number.
	- Returns structured decision: `action`, `price`, `template`.
- `generate_negotiation_email(...)`
	- Turns decision into short, professional dispatcher copy.
- `handle_broker_reply(...)`
	- Orchestrator: parse price -> decision -> message -> send/draft -> log.

### Price Understanding (current strategy)
- Regex-based extraction supports patterns like:
	- `$2100`
	- `2.1k`
	- `21 hundred`
	- contextual numeric offers (`for 2100`, `rate 2100`, etc.)
- If no confident price is found, returns `WAITING_FOR_HUMAN` (prevents bad auto-replies).

### Review Gate (new)
- If driver has `review_before_send=true`:
	- Closer stores draft on negotiation (`pending_review_subject/body/action/price`).
	- Closer logs `AI DRAFT READY` in messages.
	- No SMTP send until Joe approves.

---

## 3) Commander Joe (Human Agent)

### Mission
- Stay in control of risk and edge-cases.
- Approve high-impact sends when needed.
- Force manual mode at any time.
- Handle complex broker asks outside rate haggling.

### Dashboard Controls
- `Stop AI (Manual Mode)` -> status moved to `Manual`.
- `Approve & Send` when an AI draft is pending.
- Secure/retry controls for close flow and packet dispatch.
- Contract review/sign flow for received rate confirmations.

### Why this is critical
- Joe is not a fallback; Joe is the **safety governor**.
- Human visibility + system logs gives trust and auditability.

---

## Live Control Loop (How the 3 Work Together)

1. Scout ingests load and may start negotiation.
2. Outbound opener sent via SMTP identity.
3. Inbound listener polls IMAP and routes replies by tokenized alias.
4. Broker message stored in `messages`.
5. If `auto_negotiate=true` and status is allowed, listener triggers Closer.
6. Closer either:
	 - sends response immediately, or
	 - stores draft for Joe approval if review gate is on.
7. Dashboard reflects events in near-real-time (HTMX polling).

---

## State + Guardrails You Already Have

- **Status guards** block auto-negotiation in manual/closed/contract stages.
- **Duplicate suppression** in inbound message handling.
- **Pending-email retry** path (`CLOSED_PENDING_EMAIL` + retry action).
- **Contract priority path** (`RATE_CON_RECEIVED` -> view/sign -> `RATE_CON_SIGNED`).
- **Paper trail logging** for AI actions and failures in `messages`.

---

## Why “Broke Dev Way” Works (and why OpenAI is optional)

You proved an important point:

- You do **not** need a heavyweight LLM stack to get production value.
- Most dispatch automation is deterministic workflow + good state design.
- Regex/rules + strict thresholds + human gate gives reliability at low cost.

### What is local/deterministic in your system
- Negotiation decisions are policy-driven.
- Price extraction is rule-based.
- Inbound/outbound is standard IMAP/SMTP.
- Persistence + audit trail is plain Postgres.

### Where an LLM can help later (optional)
- Better parsing for weird broker language.
- Suggested replies for non-price questions.
- Summary generation across long threads.

You can add those later as **assists**, not dependencies.

---

## Key Files (Current Implementation Anchors)

- Scout ingest + auto-bid entry: `app/routes/ingest.py`
- Closer brain + message generation + review gate: `app/logic/negotiator.py`
- Inbound trigger/orchestration loop: `inbound_listener.py`
- Outbound SMTP service helpers: `app/services/email.py`
- Driver + negotiation state fields: `app/models/driver.py`, `app/models/operations.py`
- Approval API + dashboard card payload: `app/main.py`
- Approval UI and review toggle: `app/templates/drivers/partials/active_loads_list.html`, `app/templates/drivers/partials/rate_settings.html`

---

## Practical Operating Modes

### Mode A: Full Autonomous
- `auto_negotiate = ON`
- `review_before_send = OFF`
- Fastest loop; best when lane/profile is stable.

### Mode B: Supervised Autonomy
- `auto_negotiate = ON`
- `review_before_send = ON`
- AI drafts, Joe approves. Best for early production confidence.

### Mode C: Manual Dispatch
- `auto_negotiate = OFF` or status set to `Manual`
- Joe controls every move.

---

## Bottom Line

You now have a legitimate 3-agent dispatch system:

- **Scout** finds and primes opportunities.
- **Closer** negotiates with policy and speed.
- **Commander Joe** controls risk and final authority.

That architecture is scalable, cheap, and robust — and it is absolutely not locked to OpenAI.
