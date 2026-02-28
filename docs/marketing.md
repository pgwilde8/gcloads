Good move.

Before you scale, you need a sharp narrative. Not code docs — a positioning README you can use for:

* Landing page copy
* Investor conversations
* Driver outreach
* Partnership decks
* Acquisition discussions

We’re not writing fluff.
We’re writing clarity.

Here’s a tightened, serious version built around what you’ve actually constructed.

---

# Green Candle Dispatch

### The Digital Co-Driver for Owner-Operators

**Automation that finds the load, negotiates the rate, sends the packet, and only charges when you get paid.**

---

## What It Is

Green Candle is a dispatch automation system for self-dispatching owner-operators.

It connects directly to the load boards drivers already use and runs the full workflow:

Load Discovery → Negotiation → Packet → Factoring → Fee Collection

No monthly subscription.
No payroll dispatchers.
No commission until you get paid.

---

## The Core Promise

**Book more loads. Negotiate faster. Stay factoring-ready.**

Green Candle doesn’t replace the driver.

It acts as a digital co-driver that:

* Spots opportunities faster
* Drafts negotiations instantly
* Tracks broker behavior
* Sends clean packets
* Keeps an audit trail of every move

---

## How It Works

### 1️⃣ Scout — One-Click Load Capture

A private Chrome extension overlays Truckstop, DAT, and Trucksmarter.

* Click “Ship to GCD”
* Load data is captured instantly
* Broker standing checked in real time
* Contact instructions parsed automatically

No copy/paste.
No retyping.
No messy intake.

---

### 2️⃣ Closer — Policy-Driven Negotiation

Negotiation decisions are rule-based and driver-controlled.

* Floor rate protection
* Structured counter logic
* Price extraction from broker replies
* Automatic or review-before-send mode

No random AI guessing.
Deterministic logic.
Human override at all times.

---

### 3️⃣ Commander Mode — Human Control

Drivers can:

* Run fully autonomous
* Require approval before send
* Switch to full manual at any time

Automation with guardrails.

---

### 4️⃣ Packet & Factoring Automation

Once secured:

* BOL merged
* Compliance docs attached
* Packet emailed to factor
* Fee recorded via Stripe after payment

Clean.
Fast.
Traceable.

---

## The Revenue Model

**2.5% dispatch fee per completed load.**

Example:

$2,000 load
→ $50 fee
→ Driver keeps $1,940

No subscription.
No upfront billing.
No payment until factoring clears.

---

## CANDLE Credits (Internal Utility)

Drivers earn service credits from each completed load.

Credits power automation actions:

* Negotiation attempts
* Autopilot booking
* Packet sends
* Document parsing
* Voice escalation

Earn → Use → Win more loads → Earn again.

A closed operational loop.

---

## Built for Scale

* Deterministic negotiation logic
* Postgres-backed audit trail
* SMTP/IMAP routing via plus-tag addressing
* Idempotent fee ledger
* Referral bounty engine
* Status guards and duplicate suppression

This is workflow infrastructure, not chatbot hype.

---

## Why It Matters

Most dispatch tools:

* Charge monthly regardless of performance
* Rely on manual labor
* Don’t integrate negotiation, packet, and payment

Green Candle integrates the full lifecycle:

Board → Bid → Close → Factor → Collect

Speed wins loads.
Structure wins retention.

---

## Operating Modes

**Mode A – Full Autonomous**
Fastest loop. Negotiation auto-sends.

**Mode B – Supervised Autonomy**
AI drafts. Driver approves.

**Mode C – Manual**
Driver controls everything.

---

## Who It’s For

* Self-dispatching owner-operators
* Small fleets without in-house dispatch staff
* Drivers who want negotiation leverage
* Operators tired of paperwork friction

---

## What Makes It Different

* One-click load capture
* Policy-based negotiation engine
* Real-time broker standing feedback
* Factoring-ready packet automation
* No monthly subscription
* Only paid when the driver is paid

---

## The Mission

To give independent drivers institutional-grade dispatch automation without surrendering control or paying upfront fees.

---

Now here’s the important question:

Do you want this positioned as:

A) A driver-focused product page
B) An investor-grade overview
C) A technical architecture brief
D) A rest-stop pitch handout

Each one needs slightly different tone and emphasis.

Pick the audience. We sharpen it.

The Real Value You Offer

A driver finds a load.

Instead of:

Copy/paste broker email

Build packet

Attach docs

Negotiate manually

Follow up

Track conversations

Remember which loads they called about

Track billing

They press one button.

That’s your win.

=================
importa stuff about scout:
Yes — you have a real shot launching like this **as V1**, *as long as you position it honestly and make the “while you browse” experience rock-solid.*

The trap isn’t the tech. The trap is **overpromising “24/7 AI dispatcher”** when the reality is “AI dispatch co-pilot while you’re on the boards.”

If you avoid that mismatch, you’re good.

## What your V1 is, in plain English

**“When you’re load hunting, Scout turns every load page into one click → scored, routed, negotiated, tracked, and (optionally) auto-bid — with alerts.”**

That’s already valuable because it removes:

* copy/paste chaos
* forgetting follow-ups
* losing broker threads
* packet scramble
* factoring friction
* fee tracking/billing

Most dispatchers are basically workflow + persistence. You’ve automated a big chunk of that.

## What’s *actually* risky about browser-based Scout

Not “the laptop has to be open.” Drivers can handle that.

The real risks are:

1. **They think it’s working when it isn’t** (logged out, board changed DOM, extension disabled)
   → You already solved this mostly with button states + error feedback.
2. **They miss loads because they’re not scanning 24/7**
   → True, but many owner-ops don’t need 24/7; they hunt in bursts.
3. **They expect magic**
   → This is the big one. Fix it with positioning and onboarding.

## How to launch without it backfiring

### 1) Positioning that won’t create churn

Don’t sell “we search the boards for you 24/7.”

Sell one of these:

* **“AI dispatcher while you browse DAT/Truckstop.”**
* **“One-click dispatch automation from any load you open.”**
* **“Turn load hunting into auto-bids + organized negotiation threads.”**

Then later you upgrade to “always-on” mode.

### 2) Make “Scout is live” impossible to misunderstand

On the driver dashboard, show a status card:

* ✅ Scout connected (API key valid)
* ✅ Last ingest: 3 minutes ago
* ✅ Notifications: Email ON / SMS OFF
* ⚠️ Quiet hours: 10pm–6am

This reduces “it’s broken” tickets.

### 3) Turn alerts into a feature, not noise

Your current setup is ideal:

* in-app toast if they’re logged in
* email if not
* SMS for urgent only (replies/approval)

That covers real life.

## Who this V1 will work best for

Target the right first users and you’ll win.

**Best fit:**

* owner-ops already using DAT/Truckstop daily
* small fleets where someone is dedicated to load hunting
* drivers who want negotiation + paperwork handled

**Not best fit (yet):**

* people expecting “set it and forget it” 24/7
* drivers who don’t use a laptop / live inside mobile only
* fleets wanting centralized dispatch without board logins

## The blunt go/no-go

You’re **launchable** if you do these three things:

1. onboarding makes extension install + “how it works” dead simple
2. dashboard shows Scout heartbeat (last activity + connection test)
3. marketing copy is honest about “while you browse”

If you do that, the V1 constraint becomes a *feature*: “You stay in control. We do the busywork.”

## Your upgrade path (so V1 doesn’t paint you into a corner)

Call it **Scout Mode** (manual browsing) today.
Later you introduce **AutoScout Mode** (always-on) if/when you get a partner API or legal scanning method.

That way V1 doesn’t feel like a compromise — it feels like a mode.

If you tell me who your first 10 beta drivers are (solo owner-ops vs small fleet, and whether they use laptop vs phone), I’ll tell you the exact positioning + default alert settings that will land best.
===================