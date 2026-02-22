Absolutely. Here’s the clean way to onboard 10 beta drivers **without billing them**, while still keeping **the exact same accounting/billing machinery** running so you can measure value, test enforcement, and flip them to paid later with near-zero code churn.

I’m going to record this as a “spec you can implement against” and a “policy you can sell to drivers.”

---

# Beta Driver Onboarding Plan

## Goals

1. Get 10 real drivers using Scout + negotiation flow.
2. Track *exact* fees they would have paid (so value is visible).
3. Charge **$0** during beta.
4. Avoid special-case code paths everywhere (one clean gate).
5. Keep enforcement logic intact for non-beta users.
6. Make conversion to paid a single switch.

---

# Part 1 — The Offer That Actually Works

## “Founding Driver Program” (10 slots)

* **0% fee for 60–90 days** (your call)
* In exchange:

  * Must use Scout (button) for loads they want help on
  * Must send 3–5 screenshots per week for first 2 weeks (load detail pages, broker contact blocks)
  * Must allow a short testimonial if satisfied
  * Must allow you to contact them for feedback (text/call)

### Why this works

* Drivers don’t trust promises. They trust “no fee + transparent ledger.”
* Your product’s hook is “less stress + more money + no BS.”

### Avoid the trap

Don’t promise “we’ll make you rich.” Promise:

* faster contact
* less time chasing brokers
* clear settlement trail
* no hidden percentage games

---

# Part 2 — System Design: Don’t Bill Them, Still Track Everything

You already built the right weekly billing system. Now we add a **single policy layer**:

## Add “billing_plan” to drivers

You already have `public.drivers.billing_state` (active | delinquent | suspended).

Add:

* `public.drivers.billing_plan` with values:

  * `standard` (normal billing)
  * `beta_free` (no charges, still invoice/ledger)
  * (optional later) `discount_150` (1.5%), etc.

Default: `standard`

### Why “plan” not “state”

* **state** is enforcement (active/delinquent/suspended)
* **plan** is pricing/waiver policy

This keeps your mental model clean.

### Alternative (works too, but less clean)

Put `beta_free` inside billing_state. I don’t recommend mixing pricing with enforcement.

---

# Part 3 — Billing Behavior for Beta Drivers

## Rule: Always create invoices. Never hide economics.

When load is DELIVERED (POD/BOL upload triggers it), you still create:

* `webwise.driver_invoices` row
* amount_cents = 2.5% of load
* status = `pending`

This is critical. It gives you:

* exact ROI measurement
* per-load accounting
* conversion leverage (“you saved $X in fees during beta”)

## Weekly job still runs and creates billing_runs

But the payment action changes.

### For billing_plan = beta_free

* Create `billing_runs` row (like normal)
* Create `billing_run_items` rows mapping invoices to run (like normal)
* Mark run as **waived** (instead of paid/failed)
* Mark invoices as **waived** (instead of paid)
* Record:

  * waived_reason = `beta_free`
  * waived_by = admin id or `system`
  * waived_at timestamp

✅ This preserves deterministic idempotency and full records.

### Why waive (vs skipping)

If you skip the driver entirely:

* you lose weekly grouping
* you lose visibility
* you can’t produce “you would’ve paid $X weekly” statements
* it’s harder to flip them later

Waive keeps the job deterministic and your admin reporting clean.

---

# Part 4 — Database Contract Changes

You already have:

* `public.driver_invoices`
* `public.billing_runs`
* `public.billing_run_items`
* `public.drivers.billing_state`

(I’m using your latest notes.)

## Add column: `public.drivers.billing_plan`

DDL:

```sql
ALTER TABLE public.drivers
ADD COLUMN IF NOT EXISTS billing_plan TEXT NOT NULL DEFAULT 'standard';

CREATE INDEX IF NOT EXISTS ix_drivers_billing_plan
ON public.drivers (billing_plan);
```

## Add invoice status: include waived

If your invoices table has status enum/text:

* statuses should include: `pending`, `paid`, `failed`, `waived`

If you used TEXT, just start using `waived`.

Also add:

* `waived_at TIMESTAMP NULL`
* `waived_reason TEXT NULL`

## Add billing_runs status: include waived

billing_runs.status:

* `success`, `failed`, `needs_reconcile`, `waived`

Add:

* `waived_at`
* `waived_reason`

(You can also store waived data only on the run and infer invoice waiver from run status, but it’s nicer to have invoices explicit.)

---

# Part 5 — Weekly Job Logic Update (Minimal, Deterministic)

## Where the gate lives

In your domain billing orchestrator (app/services/billing.py), after you group pending invoices by driver, before calling Stripe:

Pseudo:

```python
def process_driver(driver_id, week_ending, invoices):
    driver = repo.get_driver_billing_profile(driver_id)

    run = repo.create_or_get_billing_run(driver_id, week_ending)

    if run.status in ("success", "waived"):
        return already_done

    repo.attach_invoices_to_run(run.id, invoice_ids)

    if driver.billing_plan == "beta_free":
        repo.mark_run_waived(run.id, reason="beta_free")
        repo.mark_invoices_waived(invoice_ids, reason="beta_free")
        return waived_result

    # standard plan: go to Stripe
    pi = stripe.create_payment_intent_off_session(...)
    ...
```

That’s it.

No other layer needs to know about beta.

---

# Part 6 — Admin UX You Need (So You Can Operate)

In Admin:

* Driver list shows:

  * billing_plan
  * billing_state
  * card on file (yes/no)
  * lifetime waived fees (sum)
  * last billing run status
* Driver detail:

  * toggle billing_plan: `standard` / `beta_free`
  * “end beta” button sets plan to standard
  * show “savings during beta” totals

This becomes part of your sales motion:

> “During beta you saved $742 in fees. Want to keep the system running? Switch to standard.”

---

# Part 7 — Beta Onboarding Flow

### Step 0: Pre-qualify

Only take drivers who:

* have loads weekly
* will actually use email / Scout
* can text you screenshots
* will respond

### Step 1: Create driver account

* create driver row
* set:

  * billing_plan = beta_free
  * billing_state = active
* collect card on file anyway (optional but recommended)

**Important call:**
Even for beta-free, collecting a card early reduces future friction.
But if you think it’ll spook them, you can defer card collection until week 3.

### Step 2: Scout setup

* give them extension
* confirm API base is codriverfreight.com
* confirm scout api key set

### Step 3: First success loop (must happen in 24 hours)

* they send one load via Scout
* you show:

  * broker email/phone found
  * next step decided
  * (if auto-bid disabled) show they can send with one click

### Step 4: Weekly cadence

* every Friday:

  * weekly run executes
  * their “charges” show as waived
  * you send a simple summary:

    * loads processed
    * bids sent
    * fees waived this week
    * total beta savings

---

# Part 8 — Metrics You Track During Beta

For each driver:

* loads ingested via Scout
* negotiations started
* auto-bids sent
* wins / callbacks
* time-to-contact broker
* fees accrued (would-have-paid)
* waived total

This gives you:

* product validation
* pricing validation
* testimonial material

---

# Part 9 — Conversion Strategy (End of Beta)

At day 60/90:

* switch billing_plan to `standard`
* send one message:

  * “You saved $X during beta. We’re turning on standard billing starting next Friday.”

No surprise. No drama.

---

## Decision you should make right now

Do you want beta drivers to:

1. **Not need a card on file** during beta (lowest friction), OR
2. **Add card on file** during beta but still waived (best conversion later)

My recommendation: **Option 2**, but position it plainly:

> “We keep a card on file so there’s no disruption when beta ends. During beta we won’t charge you.”

---

If you want, I’ll turn this into an actual `README.md` file content exactly how you’d commit it (and a checklist for onboarding the 10 drivers), but the above is the full recorded spec you can build and operate from.
