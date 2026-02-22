Option 1 — Immediate Charge on POD Upload

Trigger: Driver uploads signed POD → system marks delivered → charge 2.5%.

Why it’s strong

Clean trigger.

Tied to real operational event.

Hard to fake.

No waiting.

Revenue comes in immediately.

Why it’s risky

Driver hasn’t been paid yet.

Factor hasn’t paid yet.

If card declines → you must suspend access immediately.

More Stripe transactions (higher processing cost).

Psychology matters.

If a driver delivers a $7,000 load:

2.5% = $175

You charge before they see money

Some will feel it.

You need strong value perception.

Option 3 — Weekly Aggregated Billing (My Favorite Structurally)

Trigger: POD marks load completed
Billing: Every Friday auto-charge total 2.5% for all completed loads.

Why this is powerful

Fewer Stripe fees.

Cleaner statements.

Lower decline probability.

Less emotional friction.

Easier reconciliation.

Feels like SaaS, not per-load tolling.

You still:

Track load-level fees internally.

Show transparent breakdown.

Email weekly statement.

This is operationally clean.

Option 4 — Invoice + Auto-Pay Ledger (Most Professional)

This is basically Option 3 but structured better.

Flow:

POD uploaded → system generates load invoice (2.5%)

Adds to driver ledger

Weekly → invoice generated for all outstanding balances

Auto-charge card

Email receipt + breakdown

This gives you:

Audit trail

Professional accounting structure

Dispute handling per load

Balance tracking

Enterprise credibility

It’s more work.
But it scales beautifully.

Now Let’s Think Like a Risk Manager

You care about 3 things:

Revenue reliability

Reduced chargebacks

Operational simplicity

Immediate per-load billing increases:

Emotional friction

Dispute frequency

Transaction count

Weekly billing smooths all three.

My Strategic Recommendation

Use this hybrid:

Trigger: POD upload
→ Immediately create internal invoice
→ Add to driver ledger
→ Charge weekly (auto-pay)

That gives you:

Strong event trigger (Option 1 logic)

Clean billing (Option 3 efficiency)

Professional structure (Option 4 clarity)

Best of all worlds.

Now Let’s Address the Real Fear

You’re worried about:

What if they don’t factor?
What if they have different factoring companies?
What if they forget steps?

Good news:

None of that matters.

Because your billing trigger is document upload, not factoring.

You’re charging for value delivered:

Negotiation support

Packet generation

Business workflow support

Not for factoring specifically.

Keep it clean.

Now Let Me Push You Harder

You must decide one policy clearly:

What happens if weekly charge fails?

Options:

A) Immediate account suspension
B) Grace period (3–5 days)
C) Allow continued use but block packet sending

If you don’t define this now, you’ll bleed revenue.

Stripe Architecture (Simple Version)

At signup:

Create Stripe Customer

SetupIntent → save card

Store stripe_customer_id

When POD uploaded:

Calculate fee

Insert invoice row in DB

Weekly job:

Sum unpaid invoices

Create Stripe PaymentIntent off-session

Mark invoices paid on success

Suspend on failure

No Connect needed.

Clean SaaS pattern.

Now here’s the real business question:

Are you comfortable enforcing billing strictly?

Because the weekly model only works if enforcement is firm.

If you go soft, you’ll accumulate unpaid balances fast.

Tell me what your tolerance is for enforcement — strict or flexible — and we’ll design the policy layer correctly.

The Model You’re Choosing

Event trigger:
POD / BOL uploaded → load marked Delivered.

Accounting action:
System generates a load-level invoice (2.5%).

Billing action:
Every week → aggregate unpaid invoices → auto-charge card.

Enforcement:
Failed charge → restricted access.

That’s professional.

Step 1 — Define the Ledger Layer

You need a real internal accounting table. Not ad-hoc math.

Something like:

driver_invoices
---------------
id
driver_id
negotiation_id
gross_amount
fee_percentage (2.5)
fee_amount
status (pending, paid, failed, disputed)
stripe_payment_intent_id
created_at
paid_at

When POD uploaded:

Calculate fee = gross * 0.025

Insert invoice row

Status = pending

Nothing charged yet.

This creates clean audit trail.

Step 2 — Weekly Billing Job

Every Friday (cron job):

Select all pending invoices per driver

Sum fee_amount

Create Stripe PaymentIntent (off-session)

On success:

Mark invoices paid

Store payment_intent_id

On failure:

Mark invoices failed

Trigger enforcement logic

Important: One Stripe charge per driver per week.

That keeps:

Stripe fees low

Statements clean

Drivers calmer

Step 3 — Enforcement Policy (Be Clear)

You said strict. Good.

Here’s a clean structure:

Day 0 — Charge fails
→ Email notification
→ In-app banner: “Payment failed. Please update card.”

Day 3 — Still unpaid
→ Disable new load negotiations
→ Allow viewing only

Day 7 — Still unpaid
→ Full account lock until payment made

That’s firm but fair.

Don’t immediately hard-lock unless you want angry calls.

Step 4 — Dispute Handling

Because percentage billing causes disputes.

You need:

Invoice breakdown view in dashboard

Per-load fee transparency

Click-to-dispute button

Admin override ability

Without that, you’ll drown in email arguments.

Step 5 — Stripe Setup (No Connect)

At signup:

Create Stripe Customer

SetupIntent to save card

Store stripe_customer_id

Require valid card before first load negotiation

That’s non-negotiable if you want strict enforcement.

No card = no access.

Important Risk You Must Accept

Percentage billing means revenue scales with driver success.

But so do disputes.

If driver:

Gets short paid

Broker deducts lumper

Rate changed

Partial load

You need rule clarity:

Are you charging on:

Original negotiated rate?

Final settled rate?

Factored net?

I strongly recommend charging on negotiated gross rate stored in your DB at booking time.

Do not try to track broker payment deltas. That’s chaos.

Psychological Framing

In onboarding, explain clearly:

We earn 2.5% only when you complete a load and upload delivery docs.
We bill weekly. No surprises. Full breakdown provided.

Clarity reduces disputes dramatically.

Strategic Note

This model is scalable.

You can later add:

Volume discounts

Fleet tier pricing

Subscription hybrid

But the ledger architecture stays the same.

That’s why this is the right foundation.

Now I’m going to ask something critical:

Are you comfortable requiring card-on-file before they negotiate their first load?

Because if you hesitate there, this entire enforcement model weakens.

A — DDL / Migrations — migrations/014_billing_tables.sql
public.driver_invoices with UNIQUE (negotiation_id) for idempotency
public.billing_runs with UNIQUE (driver_id, week_ending) as the idempotency anchor
public.billing_run_items with UNIQUE (driver_invoice_id) preventing double-attachment
public.drivers.billing_state column (active | delinquent | suspended)
Note: tables are correctly in public schema (not webwise — that's broker intel only, as your DB README documents)
B — FastAPI endpoint + cron runner
POST /internal/billing/run?week_ending=YYYY-MM-DD&dry_run=true|false in main.py:1469
current_week_ending() computes most recent Friday in America/New_York via pytz
scripts/run_weekly_billing.sh with cron entry comment: 0 20 * * 5 (Fridays 8 PM ET)
C — Repository/service boundaries
app/repositories/billing_repo.py — all DB reads/writes, no business logic
app/services/stripe_billing.py — Stripe client wrapper, create_payment_intent_off_session + retrieve_payment_intent
app/services/billing.py — orchestration only, calls repo and Stripe, never touches DB directly
D — Idempotency
DB unique constraint on (driver_id, week_ending) — duplicate run rows are rejected at the DB level
ON CONFLICT DO NOTHING on billing_run_items inserts
Stripe idempotency key: f"billing-{driver_id}-{week_ending.isoformat()}"
Pre-flight check: if existing run has status='success', skip immediately
E — Failure handling
Stripe fails → mark_run_failed + mark_invoices_failed + set_driver_delinquent — invoices stay failed (not pending) so the next weekly run doesn't re-attempt until manually reset; delinquent blocks new negotiations
Stripe succeeds but DB commit fails → mark_run_needs_reconcile with PI id stored; next job run calls _reconcile_pending_runs first, retrieves PI from Stripe, and completes the DB writes
F — Response contract
Summary: drivers_processed, drivers_succeeded, drivers_failed, drivers_skipped, total_amount_cents, total_amount_usd
dry_run=true adds dry_run_preview[] with per-driver driver_id, invoice_ids, total_cents, total_usd
G — Implementation — fully implemented code, not pseudocode