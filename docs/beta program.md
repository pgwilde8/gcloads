# CoDriver Freight Beta Program

This document describes the beta program implementation for future engineers.

---

## Overview

Beta drivers use the full platform at no cost during their trial. They are created when users sign up via the **beta host** (e.g. `beta.codriverfreight.com`). Hostname influences **initial driver state only**; all enforcement uses **driver billing state**, not hostname.

**Key principle:** Beta is a policy (billing_mode + exemption), not a deployment. Same app, same DB, same endpoints.

---

## 1. Driver Billing State

| Column | Type | Purpose |
|--------|------|---------|
| `billing_mode` | `VARCHAR(20)` | `'beta'` or `'paid'` |
| `billing_exempt_until` | `DATE` | Optional trial end date |
| `billing_exempt_reason` | `TEXT` | Internal (e.g. `beta_host`) |

**Exemption rules:**
- `billing_mode = 'beta'` → always exempt
- `billing_exempt_until >= today` → exempt
- Otherwise → charged per normal billing

**Payment method rule:** `has_payment_method = stripe_customer_id AND stripe_default_payment_method_id`

---

## 2. Beta Host Detection

**Where:** `app/core/config.py` — `is_beta_request(request, beta_hosts, trusted_proxy_ips)`

**Logic:**
- Resolve host from `Host` or `X-Forwarded-Host` (only when client IP is in `TRUSTED_PROXY_IPS`)
- Normalize: strip port, lowercase
- Match against `BETA_HOSTS` (comma-separated)

**Security:** `X-Forwarded-Host` is honored **only** when the request comes from a trusted proxy IP. Otherwise spoofing would allow free account creation.

**Config:**
- `BETA_HOSTS` — default `beta.codriverfreight.com`
- `TRUSTED_PROXY_IPS` — comma-separated IPs (e.g. Cloudflare egress IPs)

---

## 3. Driver Creation (Beta Signup)

**Where:** `app/routes/auth.py` — `POST /register-trucker` (called after magic link verify)

**Flow:**
1. User visits `https://beta.codriverfreight.com/start`
2. Enters email → magic link sent (see §6)
3. Clicks link → lands on beta host → redirected to `/register-trucker`
4. Completes form (name, MC/DOT, factoring choice)
5. On driver create:
   - If `is_beta_request(request)` → set `billing_mode='beta'`, `billing_exempt_reason='beta_host'`, `billing_exempt_until = today + 60 days`
   - Else → default `billing_mode='paid'`, no exemption

**Important:** `billing_mode` is never from user input. It is set server-side only.

---

## 4. Magic Link URL (Request-Based)

**Problem:** Magic links were built with `APP_BASE_URL`, so beta users received links to prod and landed there.

**Solution:** `get_safe_base_url_from_request(request)` in `app/core/config.py` builds the base URL from the request host when it is whitelisted.

**Whitelist:** `ALLOWED_BASE_HOSTS` — `app.greencandledispatch.com`, `beta.codriverfreight.com`, `localhost`, `127.0.0.1`

**Used in:** `POST /auth/magic/send` — verify URL = `{base_url}/auth/magic/verify?token=...`

Result: Beta users get links back to beta; prod users get prod links.

---

## 5. Payment Method Gate (Step 4)

**Where:** `app/dependencies/billing_gate.py` — `require_payment_method_if_paid`

**Logic:**
1. Resolve driver from session
2. Load `driver_info` (stripe + billing fields)
3. If `is_driver_billing_exempt(driver_info, today)` → allow
4. Else if `billing_mode == 'paid'` and not `has_payment_method(driver_info)` → **402 Payment Required**
5. Else → allow

**Gated endpoints:**
- `POST /api/negotiations/{id}/compose-packet`
- `POST /api/negotiations/secure-load`
- `POST /api/negotiations/retry-secure-email`
- `POST /api/negotiations/quick-reply`
- `POST /api/negotiations/approve-draft`

**Not gated:** Onboarding, profile, payment setup, read-only, BOL upload.

---

## 6. Weekly Billing Job

**Where:** `app/services/billing.py` — `run_weekly_billing()`

**Logic:**
- For each driver with pending invoices: load `driver_info`
- If `is_driver_billing_exempt(driver_info, week_ending)` → create run, mark `exempt_success`, do NOT call Stripe
- Else → require payment method; charge via Stripe if present

**Invoice invariants:**
- Exempt-settled invoices: `is_exempt = TRUE`, `stripe_payment_intent_id = NULL`
- Cash-paid invoices: `is_exempt = FALSE`, `stripe_payment_intent_id` set

---

## 7. Admin Beta Panel

**Routes:**
- `GET /admin/beta` — list beta drivers (password-gated)
- `POST /admin/beta/promote` — set `billing_mode='paid'`, clear exempt fields
- `POST /admin/beta/extend` — set `billing_exempt_until = GREATEST(existing, new_date)`

**Promote behavior:**
- Driver becomes immediately non-exempt
- Past invoices remain `is_exempt = true` (not modified)
- If no payment method, they hit 402 on money actions until they add a card

**Extend behavior:**
- Never shortens exemption
- `new_exempt_until` must be `>= today` (server-validated)
- Reason only overwritten if non-empty

**UI:** `app/templates/admin/beta.html` — shows `billing_mode`, `billing_exempt_until`, "currently exempt" badge, exempt invoice count/amount, has PM badge, Promote and Extend actions.

---

## 8. Frontend Bootstrap & Banner

**Bootstrap endpoint:** `GET /api/drivers/billing-bootstrap` — returns:
- `billing_mode`
- `billing_exempt_until` (ISO date or null)
- `is_currently_billing_exempt`
- `has_payment_method`

(Does not expose `billing_exempt_reason`.)

**Banner:** `app/templates/drivers/partials/beta_banner.html` — shown when `billing_mode == 'beta'` or `is_currently_billing_exempt`

**Messaging:**
- Beta: "Beta mode — you won't be charged until {date}." or "…during your trial."
- Paid exempt: "Promotional free period active — you won't be charged until {date}."
- No PM: "Add a payment method anytime…" + button to `/drivers/add-payment`

**Dashboard:** Persistent "Billing & Payment" link; Add payment method is optional for exempt drivers.

---

## 9. Beta Landing Page

**Routes:** `GET /beta`, `GET /drivers_beta` — both render `public/drivers_beta.html`

**CTA:** "Join beta" → `/start` (magic link form). Do not link to `/register` or `/register-trucker` directly; magic link is the entry point.

---

## 10. Key Files

| File | Purpose |
|------|---------|
| `app/core/config.py` | `is_beta_request`, `get_safe_base_url_from_request`, `BETA_HOSTS`, `TRUSTED_PROXY_IPS`, `ALLOWED_BASE_HOSTS` |
| `app/repositories/billing_repo.py` | `is_driver_billing_exempt`, `has_payment_method`, `billing_bootstrap_for_driver`, `promote_beta_to_paid`, `extend_billing_exemption`, `list_beta_drivers_with_exempt_stats` |
| `app/dependencies/billing_gate.py` | `require_payment_method_if_paid` |
| `app/routes/auth.py` | Driver creation with beta fields; magic link send uses request-based base URL |
| `app/services/billing.py` | Weekly billing; skips Stripe for exempt drivers |
| `app/templates/drivers/partials/beta_banner.html` | Beta/exempt banner |
| `app/templates/admin/beta.html` | Admin beta panel |
| `app/templates/public/drivers_beta.html` | Beta landing page |

---

## 11. Config / Env Vars

| Var | Purpose |
|-----|---------|
| `BETA_HOSTS` | Comma-separated beta hostnames |
| `TRUSTED_PROXY_IPS` | Comma-separated IPs; `X-Forwarded-Host` / `X-Forwarded-Proto` honored only from these |
| `ALLOWED_BASE_HOSTS` | Comma-separated hosts allowed for magic-link URLs |
| `APP_BASE_URL` | Fallback when request host not whitelisted |

---

## 12. Test Matrix

| Scenario | Expected |
|----------|----------|
| Paid + no customer | 402 on money actions |
| Paid + customer + no default PM | 402 on money actions |
| Paid + customer + default PM | Allow |
| Beta | Allow (no 402) |
| Paid but exempt_until >= today | Allow |
| Sign up on beta host | `billing_mode=beta`, exempt set |
| Sign up on prod host | `billing_mode=paid` |
| Magic link from beta | Link points to beta host |
| Promote beta → paid | `billing_mode=paid`, exempt cleared; hits 402 if no PM |

---

## 13. What Beta Drivers Can Do

- **Onboarding:** Same as paid (profile, factoring choice, packet docs)
- **Finding loads:** Scout setup, Scout loads, load board
- **Negotiating & securing:** Compose packet, secure load, quick reply, approve draft (no payment gate)
- **Paperwork:** Upload BOL, upload packet docs
- **Add payment method:** Optional; `/drivers/add-payment`

**Known gap:** The "Send to factoring" button on `/drivers/uploads` posts to `/drivers/loads/{load_id}/send-to-factoring`, which has no route. BOL upload works; factor submission is not wired.

---

## 14. Migration References

- `migrations/020_driver_billing_mode.sql` — `billing_mode`, `billing_exempt_until`, `billing_exempt_reason`
- `migrations/021_driver_invoices_is_exempt.sql` — `is_exempt` on driver_invoices
- `migrations/022_driver_invoices_exempt_pi_check.sql` — CHECK constraint for exempt/Stripe consistency
