Perfect. Here’s a clean, implementation-ready README you can drop into your repo as:

`docs/driver_accounting.md`

---

# CoDriver Freight — Driver Accounting & Settlement System

## Overview

The Driver Accounting system provides a transparent, load-level financial record for every driver using CoDriver Freight.

It functions as:

* A weekly billing ledger
* A load-level fee breakdown
* A payment status tracker
* An accountant-ready export tool

This system supports the 2.5% weekly autopay billing model and strict enforcement policy.

---

# Business Model Summary

* Drivers are charged **2.5% of gross load revenue**
* Fee is accrued when a load is marked **Delivered** (POD/BOL uploaded)
* Fees are **aggregated weekly**
* One Stripe charge per driver per week
* Strict enforcement on failed payments

---

# Core Concepts

## 1. Load-Level Fee Accrual

Trigger:

* POD/BOL uploaded
* Load marked Delivered

Action:

* Create invoice record
* Status = `pending`
* Fee = `gross_rate * 0.025`

No immediate charge occurs.

---

## 2. Weekly Auto-Pay

Every Friday:

1. Sum all pending invoices per driver
2. Create one Stripe PaymentIntent (off-session)
3. On success:

   * Mark invoices as `paid`
4. On failure:

   * Mark invoices as `failed`
   * Flag account as delinquent
   * Trigger enforcement logic

---

## 3. Enforcement Policy

If weekly charge fails:

* Immediate:

  * Email notification
  * In-app banner
* 24 hours:

  * Block new negotiations
* Continued failure:

  * Full account lock until resolved

Card-on-file required for platform use.

---

# Data Model

## Table: `driver_invoices`

One row per load fee.

Fields:

* `id`
* `driver_id`
* `negotiation_id`
* `gross_amount_usd`
* `fee_rate` (0.025)
* `fee_amount_usd`
* `status` (`pending`, `paid`, `failed`, `disputed`, `void`)
* `stripe_payment_intent_id`
* `billed_week_ending`
* `created_at`
* `paid_at`

This is the source of truth for billing.

---

## Stripe Integration

At driver onboarding:

* Create Stripe Customer
* Use SetupIntent to store card
* Save `stripe_customer_id`

No Stripe Connect required.

---

# Driver Accounting Page

Navigation:

Dashboard → **Accounting**

Page Sections:

## 1. Transactions Table

Columns:

* Date
* Load ID / Broker
* Event Type
* Gross Amount
* CoDriver Fee
* Status
* Documents

Event Types:

* Load Delivered
* Fee Accrued
* Weekly Payment
* Payment Failed
* Payment Retried

---

## 2. Weekly Billing Summary

Per week:

* Week ending date
* Total fees billed
* Payment status
* Receipt link

---

## 3. Export Tools

Options:

* Download CSV (date range)
* Download Annual CSV
* Download Accountant Pack (CSV + BOLs/Rate Cons ZIP)

Exports include:

* Date
* Load ID
* Gross
* Fee
* Payment status

---

# Timeline Event Model

Unified output shape for API:

```
event_id
event_time
event_type
amount_usd
status
negotiation_id
document_links
```

Events generated from:

* `negotiations`
* `driver_invoices`
* Stripe payment results

Ordered by `event_time DESC`.

---

# Gross Revenue Source of Truth

Fee calculations are based on:

* Negotiated rate stored in database at booking time

Adjustments must be explicitly recorded to avoid billing disputes.

---

# Key Design Principles

* One charge per week per driver
* No manual driver billing actions required
* Document-based trigger (not driver memory)
* Transparent fee breakdown
* Deterministic event ordering
* Strict enforcement

---

# Future Enhancements

* Factoring funded informational events
* Volume-based pricing tiers
* Fleet-level rollups
* Subscription + percentage hybrid model

---

# Non-Goals (For Now)

* Direct fuel card processing
* Multi-factor API integrations
* Marketplace-style payment splitting
* Tokenized credits

---

# Why This Matters

This system transforms CoDriver Freight from:

“Dispatch software”

Into:

“A transparent financial partner for owner-operators.”

Drivers see:

* Exactly what they earned
* Exactly what was charged
* Exactly when it was paid

No hidden math.
No per-load surprises.
No spreadsheet chaos.

---

If you want next, we can:

* Design the exact weekly billing job flow in FastAPI terms
* Define the Stripe failure handling logic
* Or map this into your existing Postgres schemas cleanly without violating your ARCHITECTURE_CONTRACT.md rules
