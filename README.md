# Green Candle Dispatch

Green Candle Dispatch is a driver-first dispatch platform built to help owner-operators and small fleets keep more of what they earn.

## What We’re Building

Most dispatch services charge **5% to 10%** of your gross load revenue.
That can mean hundreds (or thousands) of dollars per month gone before fuel, insurance, and maintenance.

Green Candle Dispatch is built to replace that model with an automation-first back office that helps you:

- Find and process load opportunities faster
- Communicate with brokers from a professional dispatch identity
- Negotiate rates with clear floor protections
- Keep an audit trail of every broker interaction
- Run more of your operation with less overhead

## Why This Helps Drivers

Instead of giving up a percentage of every load, this platform is designed to reduce your need for percentage-based dispatch fees by automating the repetitive dispatch workflow.

That means more control, more transparency, and more money staying with the driver.

## How It Works (Simple View)

- **You set your floor rates** (your minimum acceptable numbers)
- **The system listens for broker replies** and routes them correctly
- **Negotiation logic applies your rules** (counter, close, or walk away)
- **Every action is logged** so you can see exactly what happened

You stay in control while the system handles the heavy lifting.

## Service Credits: `$Candle`

Green Candle Dispatch uses internal service credits called **`$Candle`** to power automation features.

- `$Candle` is used for platform activity and service operations
- The credit system is being prepared for broader launch
- **`$Candle` is planned to launch on Clanker soon**

More details on usage, accounting, and rollout phases will be published as launch approaches.

## Current Focus

Right now, the project focus is practical driver outcomes:

- Better rate protection
- Less back-and-forth admin time
- More consistent broker communication
- Lower dependency on traditional % dispatch models

## Mission

Help drivers keep more of their revenue, operate with confidence, and scale without giving away 5–10% of every load.
more..
Green Candle Dispatch — Engineer Overview
What it is
Green Candle Dispatch is an AI dispatch platform for trucking. It helps owner-operators and small fleets:
Discover loads (load boards, Scout extension)
Negotiate rates with brokers (AI-generated emails)
Run autonomous “autopilot” negotiation
Handle BOL uploads, factoring packets, and invoices
The platform earns a 2.5% dispatch fee when a load is completed and the driver is paid by their factoring company. Fee is collected via Stripe (driver card on file). A portion of the fee is returned to drivers as $CANDLE credits used for automation features.
Two main flows
Universal flow — Direct signup, setup fee, payment, then full access.
Century flow — For drivers using Century Finance. Payment → Century form → manual approval by Alma/Century before access. Runs on subdomain century.greencandledispatch.com.

Tech stack
Layer
Tech
Backend
FastAPI, Python 3.x
DB
PostgreSQL (schema webwise)
Templates
Jinja2
Frontend
HTMX, Tailwind (CDN)
Payments
Stripe
Storage
DigitalOcean Spaces (S3-compatible)
Email
MXRoute SMTP


Core concepts
$CANDLE — Internal credits (1 CANDLE = $1). Drivers earn from the fee and spend on negotiation, autopilot, packet send, doc parse, etc. See app/services/ledger.py for allocation and usage.
Negotiation — Load broker ↔ driver flow. AI drafts emails; driver sends/counters. Statuses: sent, replied, pending_approval, won, lost.
Scout — Browser extension that finds loads and pushes them into the platform via API (/scout/heartbeat, X-API-Key).
Factoring — Drivers send BOLs/packets to factoring companies. Century is the primary referral partner.


more...
### Fee collection timing


- Platform earns **2.5% dispatch fee** when a load is completed and the driver is paid by the factor.
- Drivers keep **card on file** (Stripe). When factor pays the driver, we charge the 2.5% fee via Stripe.**update stripe connect will be used to collect.
- No upfront dispatch fees; no monthly subscription for core dispatch.


---


### Example per load ($2,000)


| Step | Amount |
|------|--------|
| Broker pays | $2,000 |
| Factoring company (e.g. 3%) | $60 |
| **Green Candle Dispatch (2.5%)** | **$50** |
| Driver receives | $1,940 |


---


### $CANDLE credits — how they are used (current rates)


Credits are internal utility (1 CANDLE = $1 service value). Drivers earn them from the driver slice of the fee and spend them on automation. Rates set so a driver earning ~10.5 CANDLE per load can typically acquire the next load without replenishing.


| Action | Cost (CANDLE) | Notes |
|--------|---------------|-------|
| **Negotiation attempt** | 0.25 | Per bid/send (AI drafts, driver sends) |
| **Manual email (counter)** | 0.25 | Driver sends counter-offer |
| **Autopilot / successful booking** | 6 | Charged when Rate Confirmation detected |
| **Factoring packet send** | 0.2 | Packet to factor |
| **Doc parse (BOL/Invoice)** | 1.0 | AI parses document |
| **Voice escalation** | 0.5 | Escalate to AI phone call |


*Board scanning / load discovery:* Scout extension and in-app load board do not currently charge per scan. Optional future costs: e.g. 1 CANDLE/day for advanced filters, 1 CANDLE/day for “Top 20 loads” ranking.


---


### Fee allocation — driver credits vs dev/ops vs platform


Every dollar of the 2.5% fee is split four ways. Current implementation (see `ledger.py`):


| Slice | % of fee | Purpose | Example on $50 fee |
|-------|----------|---------|---------------------|
| **Driver credits (CANDLE)** | **21.05%** | Automation fuel (negotiation, autopilot, packet, etc.) | ~$10.50 → 10.5 CANDLE |
| **AI / infra reserve** | **21.05%** | OpenAI, hosting, email, Twilio, data | ~$10.50 |
| **Platform profit** | **31.58%** | Product, support, ops, growth | ~$15.80 |
| **Treasury** | **26.32%** | $CANDLE backing / burn reserve | ~$13.16 |


**Check:** 21.05 + 21.05 + 31.58 + 26.32 = **100%**.


- **Driver ops:** The driver credits slice (21.05%) flows back to drivers as CANDLE for automation.
- **Dev/ops:** The AI/infra slice (21.05%) covers AI, hosting, email, Twilio, and related bills.


---


### Discussion: adjusting driver vs dev allocation


- If AI costs grow (e.g. more autonomous bookings): consider raising AI/infra (e.g. 25–30%) and lowering driver credits or platform.
- If driver adoption matters more: consider raising driver credits (e.g. 25–30%) and trimming platform or AI.
- Treasury slice is for long-term stability; keep 20–30% unless you change the burn model.


---


### Driver economics (example)


- $2,000 load → $50 fee → ~10.5 CANDLE to driver.
- Typical full cycle per load: 3× negotiation (0.75) + 1× counter (0.25) + success (6) + packet (0.2) + parse (1) ≈ **8.2 CANDLE**.
- Net: driver earns 10.5, spends ~8.2 → **~2.3 CANDLE surplus** per load. Good chance to acquire next load without replenishing.


---


### Stripe products (unchanged)


- Call Packs (Twilio): $49 / 120 min, $99 / 300 min, $199 / 750 min  
- Fuel Packs (extra CANDLE): Starter, Fleet  
- Broker Subscription: $149/mo






1. Single Stripe account + internal ledger (simplest)
All fees land in one Stripe account. You only track how to allocate each charge.
How:
On each successful charge, store an allocation row in your DB, e.g. revenue_allocations:
Column  Example
stripe_payment_id pi_xxx
load_id LOAD-123
gross_fee 50.00
driver_slice  10.50
ai_slice  10.50
platform_slice  15.80
treasury_slice  13.16
Run reports by slice for budgets and P&amp;L.
Actual cash stays in one account; “splits” are for accounting/reporting.
2. Stripe Connect with multiple connected accounts
Physically split each payment at charge time.
How:
Create connected accounts for each destination (e.g. Platform, Treasury).
Use Stripe Connect destination charges or transfer_data to send portions to each connected account.
Pros: Real separation of funds
Cons: More setup, Stripe fees per transfer, KYC per connected account
3. Scheduled sweeps to separate bank accounts
One Stripe → one bank. Then you move money on a schedule.
How:
Weekly or monthly: sum allocations from your ledger.
Do ACH/wire to separate accounts (e.g. Operating, Treasury).
Example: “This month AI slice = $8,400 → move $8,400 to AI/infra account.”
Pros: Clear physical separation, few Stripe changes
Cons: Manual or scripted, timing lag
4. Stripe + metadata for reporting
Use Stripe only for money; use your DB for splits.
How:
Webhook on payment_intent.succeeded / charge.succeeded captures the fee.
Compute the four slices using your allocation logic (e.g. from ledger.py).
Write them to revenue_allocations (or similar).
Optionally add metadata to the Stripe object:
metadata: { load_id, gross_fee, driver_pct, ai_pct } for easy Stripe reporting.
5. Envelope / virtual sub-accounts in one bank
One Stripe, one bank account, internal “envelopes” in your system.
How:
Maintain running balances per slice in your DB (driver_credits_pool, ai_reserve_pool, etc.).
Each charge increases those balances.
When you pay AI bills, record “draw from AI reserve.”
Balances are for budgeting, not physical cash separation.
Recommendation
For most setups, #1 (single Stripe + internal ledger) is enough:
Add a revenue_allocations (or fee_allocations) table.
On each fee charge, insert one row with the four slices (using your existing allocation math).
Build simple reports: totals by slice, by month, by driver.
Use that for budgets (e.g. “AI spend this month vs AI slice”) and P&amp;L.
You can add #3 (scheduled sweeps) later if you want actual separation into different bank accounts for ops, treasury, etc.

more...
other products and revenue and % of profits will go to buying $Candle to BURN.
Revenue Sources (from pricing-products.html + rev.md)
Source
Type
Price/Amount
Notes
Dispatch fee
Per load
2.5% of load (e.g. $50 on $2k)
Stripe when factor pays
Setup fee
One-time
$25/truck
Stripe
Call Packs
One-time
$49, $99, $199
Stripe; Twilio cost
Fuel Packs
One-time
10 credits, 60 credits
Stripe; no marginal cost
Broker Subscription
Recurring
$149/mo
Stripe
Factoring referral
Per load
0.25–0.75% of invoice
From factor, not Stripe


Refined Burn Design
Core idea
Use a single Burn Pool funded by clear rules per stream. Monthly, use 100% of the pool to buy and burn $CANDLE.
Option A: Treasury slice + fixed % of other revenue
Revenue stream
Allocation to burn
Rationale
Dispatch fee
100% of treasury slice (26.32% of fee)
rev.md already earmarks this for treasury/burn
Setup fee
10%
Simple, small
Call Packs
10% of revenue (or net after Twilio)
Shares upside with token
Fuel Packs
10% of revenue
Same
Broker Subscription
10% of revenue
Same
Factoring referral
10% of referral $
Same

Example (per $2k load): $50 fee × 26.32% ≈ $13.16 → Burn Pool.Other products: 10% of revenue → Burn Pool.Pros: Simple, tied to existing rev.md split.Con: “10%” is arbitrary; you can make it configurable.

Option B: Treasury-only (minimal)
Revenue stream
Allocation to burn
Dispatch fee
100% of treasury slice (26.32%)
Everything else
0%

Only dispatch fees fund burns; other products do not.Pros: Very simple, aligns with rev.md.Con: Call packs, fuel packs, broker sub don’t contribute.
Option C: Fixed % across all revenue
Revenue stream
Allocation to burn
All revenue (dispatch fee, setup, call packs, fuel packs, broker sub, factoring)
X% of gross (e.g. 5–10%)

Pros: Single rule, easy to explain.Con: Ignores rev.md treasury slice; needs careful definition of “gross.”
Recommendation: Option A
Reuse the existing treasury slice for the dispatch fee.
Add a fixed 10% of other revenue streams to the burn pool.
Keep implementation and messaging straightforward.

Implementation Rules (Option A)
Burn Pool sources
Dispatch fee: treasury slice = gross_fee × 0.2632.
Setup fee: 0.10 × setup_fee_revenue.
Call packs: 0.10 × call_pack_revenue.
Fuel packs: 0.10 × fuel_pack_revenue.
Broker sub: 0.10 × broker_sub_revenue.
Factoring referral: 0.10 × factoring_referral_revenue.
Recording
On each payment: record revenue_type, amount, burn_share, created_at in a burn_pool_contributions (or similar) table.
Sum by month for execution.
Execution
Monthly (e.g. first week of the month): sum previous month’s burn pool, execute buy+burn.
Track on-chain and in dashboard.
Exclusions
Stripe fees: treat as cost; burn share is based on net or gross (pick one and stick to it).

Example Math (Option A)
Assumptions: 500 drivers, 15 loads/driver/mo, $2k avg load, 50 call packs $99, 20 fuel packs $50, 10 broker subs $149.
Dispatch: 7,500 loads × $50 × 26.32% ≈ $98,700 → Burn Pool
Call packs: 50 × $99 × 10% ≈ $495
Fuel packs: 20 × $50 × 10% ≈ $100
Broker sub: 10 × $149 × 10% ≈ $149
Factoring referral (0.35% × $2k × 7,500): ≈ $52,500 × 10% ≈ $5,250
Total Burn Pool ≈ $104,694/month → used to buy and burn $CANDLE.
Clarifications to Lock In
Other revenue %: keep 10% or change (e.g. 5%, 15%)?
Call packs: 10% of gross or 10% of net after Twilio?
Cadence: monthly vs weekly?
Trigger: “when pool ≥ $X” vs “fixed schedule”?
Fuel packs: price per pack in USD (currently TBD on the page).

Wording for Tokenomics
Suggested text for the Tokenomics doc:> Burn Pool> - Dispatch fee: 100% of the treasury slice (26.32% of the 2.5% fee).> - Other revenue (Setup, Call Packs, Fuel Packs, Broker Subscription, Factoring referral): 10% of revenue allocated to the burn pool.>> Execution: Monthly. Total Burn Pool is swapped to USDC → $CANDLE on Uniswap → burned to a dead address. All burns are tracked on-chain and shown in the app dashboard.
Summary
Use the existing treasury slice for dispatch fees.
Add a 10% allocation from other revenue streams to a single Burn Pool.
Record contributions per transaction; execute buy+burn monthly.
Document the rules in Tokenomics and in your revenue tracking for consistency.

current allocation, can change slighty:
## Token Details
- **Chain**: Base (EVM-compatible, low fees).
- **Total Supply**: 1,000,000,000 $CANDLE.
- **Contract Address**: [TBD — post-launch].
- **Distribution**:
  | Allocation | Percentage | Details |
  |------------|------------|---------|
  | Liquidity Pool | 50% | Seeded on Uniswap for fair trading. |
  | Team/Founders | 20% | Vested over 3 years (cliff + linear). |
  | Community Airdrops/Marketing | 15% | For early supporters and beta users (opt-in). |
  | Treasury (Burns/Buybacks) | 10% | For ongoing deflation. |
  | Advisors/Partners | 5% | Locked for 1 year. |


🚀 Week 3 Preview: The Revenue & Reward Layer
Now that the "Brain" (Negotiation) and the "Briefcase" (Documentation) are hardened, it's time to build the "Bank." This is where Green Candle Dispatch begins to fulfill its fintech promise—collecting fees and rewarding drivers with $CANDLE.
===============================
The Week 3 Hit List:

Fee Calculation Engine:

Triggered on the secure-load event.

Calculate 2% of the final negotiated price (e.g., $40 on a $2,000 load).

Log as a pending_fee in a new ledger table.

$CANDLE Token Logic:

Implement the 75/25 Reward/Burn split.

Example: $40 fee → $30 worth of $CANDLE to the driver, $10 worth of $CANDLE burned.

Revenue Dashboard Snippets:

Queries to track "Total Fees Collected" and "Total Tokens Distributed."

🏁 Final Victory Lap
Would you like to restart gcloads_api now to see that beautiful Startup watermark mode: ON log?

Once confirmed, we can draft the SQL Migration for the Ledger & Rewards tables to kick off Week 3. This will turn your dispatcher from a cost-saver into a revenue-generator.

Shall we see the logs or dive into the Ledger schema?