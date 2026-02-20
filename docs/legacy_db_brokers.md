# Broker Database Reference

Master reference for the `webwise` broker directory: schema, columns, lookup commands, and enrichment workflows.

---

## Database Connection

Use the connection URL from your environment (e.g. `.env`):

```bash
set -a && source .env && set +a && psql "$DATABASE_URL"
```

Or, if `DATABASE_URL` is already set in your shell:

```bash
psql "$DATABASE_URL"
```

---

## Schema Overview

| Table | Purpose |
|-------|---------|
| **webwise.brokers** | One row per MC# — primary contact info, address, source |
| **webwise.broker_emails** | Multiple emails per MC with source and confidence |

**Primary key:** `mc_number` (MC digits only, no "MC" prefix)

---

## webwise.brokers — Columns

| Column | Type | Description |
|--------|------|-------------|
| `mc_number` | VARCHAR(20) PK | Motor Carrier number (digits only, e.g. `322572`) |
| `dot_number` | VARCHAR(20) | USDOT number |
| `company_name` | VARCHAR(255) | Legal name from FMCSA |
| `dba_name` | VARCHAR(255) | Doing-Business-As name |
| `website` | VARCHAR(255) | Domain only (no http/https/www) |
| `primary_email` | VARCHAR(255) | Best email for dispatch (canonical, no +tags) |
| `primary_phone` | VARCHAR(50) | Main dispatch/contact phone |
| `secondary_phone` | VARCHAR(50) | Additional phone (e.g. regional, backup) |
| `fax` | VARCHAR(50) | Fax number |
| `phy_street` | VARCHAR(255) | Physical street address |
| `phy_city` | VARCHAR(100) | City |
| `phy_state` | VARCHAR(50) | State code |
| `phy_zip` | VARCHAR(20) | ZIP code |
| `rating` | DECIMAL(3,2) | Optional broker rating |
| `source` | VARCHAR(50) | `fmcsa_api` \| `FMCSA` \| `manual` \| `enriched` |
| `preferred_contact_method` | VARCHAR(20) | `email` \| `phone` \| `call_to_book` |
| `created_at` | TIMESTAMP | When record was created |
| `updated_at` | TIMESTAMP | Last update |

**Note:** `secondary_phone` and `preferred_contact_method` are added via migrations. Run `sql/add_secondary_phone.sql` and `sql/add_preferred_contact_method.sql` if missing.

---

## webwise.broker_emails — Columns

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL PK | Auto-increment |
| `mc_number` | VARCHAR(20) FK | References brokers(mc_number) |
| `email` | TEXT | Email address (canonical, no +tags) |
| `source` | TEXT | `manual` \| `carrier_packet` \| `website` \| `search` \| `fmcsa` |
| `confidence` | NUMERIC(4,3) | 0.000–1.000 (e.g. 0.90 for manual) |
| `evidence` | TEXT | Optional context (filename, URL, snippet) |
| `created_at` | TIMESTAMPTZ | When added |

**Unique:** (mc_number, email)

---

## Commands Overview

| Goal | Section | What you use |
|------|---------|----------------|
| Connect to the DB | [Database connection](#database-connection) | `psql "$DATABASE_URL"` |
| Look up a broker (MC, DOT, name) | [Lookups (SQL)](#lookups-sql) | SQL in `psql` |
| See email coverage / stats | [Lookups (SQL)](#lookups-sql) | SQL in `psql` |
| Add or update one broker’s contact info | [Add/update one broker](#addupdate-one-broker) | `add_broker_contact.py` |
| Bulk-load brokers from FMCSA | [Bulk ingestion](#bulk-ingestion) | `ingest_active_brokers.py` |
| Find or scrape emails/websites | [Enrichment](#enrichment) | Scripts in table below |

Run all Python commands from project root with `PYTHONPATH=.` (e.g. `cd /srv/projects/client/dispatch` first).

---

## Lookups (SQL)

Use these inside `psql "$DATABASE_URL"` to read broker data.

### Look up one broker by MC (preferred)

**What it does:** Returns one broker row by Motor Carrier number (digits only). Most reliable key.

```sql
SELECT mc_number, company_name, primary_email, primary_phone, secondary_phone, website, dot_number
FROM webwise.brokers
WHERE mc_number = '322572';
```

### Look up one broker by DOT

**What it does:** Finds broker by USDOT number when you don’t have MC.

```sql
SELECT * FROM webwise.brokers WHERE dot_number = '2223295';
```

### Look up one broker with all emails

**What it does:** Same broker row plus a comma-separated list of all emails from `broker_emails` (with source), sorted by confidence.

```sql
SELECT b.mc_number, b.company_name, b.primary_email, b.primary_phone, b.website,
       string_agg(e.email || ' (' || e.source || ')', ', ' ORDER BY e.confidence DESC) AS all_emails
FROM webwise.brokers b
LEFT JOIN webwise.broker_emails e ON b.mc_number = e.mc_number
WHERE b.mc_number = '322572'
GROUP BY b.mc_number, b.company_name, b.primary_email, b.primary_phone, b.website;
```

### Search by company or DBA name

**What it does:** Finds brokers by partial name. Use `ILIKE` so punctuation (comma, period) doesn’t break the match.

```sql
-- One pattern (e.g. "GENPRO INC", "GENPRO, INC.", "GEN PRO INC")
SELECT * FROM webwise.brokers WHERE company_name ILIKE '%GENPRO%INC%';

-- Name or DBA
SELECT * FROM webwise.brokers
WHERE company_name ILIKE '%CARDINAL%' OR dba_name ILIKE '%CARDINAL%';
```

### Expanded view (one column per line in psql)

**What it does:** Toggles psql “expand” mode so `SELECT *` prints each column on its own line. Easier to read many columns.

```sql
\x on
SELECT * FROM webwise.brokers WHERE mc_number = '143059';
\x off
```

### Email coverage stats

**What it does:** Counts total brokers, how many have `primary_email`, how many are missing email, and the email rate as a percentage.

```sql
SELECT
    COUNT(*) AS total_brokers,
    COUNT(primary_email) AS with_emails,
    COUNT(*) - COUNT(primary_email) AS missing_emails,
    ROUND((COUNT(primary_email)::numeric / NULLIF(COUNT(*), 0)) * 100, 2) AS email_rate_pct
FROM webwise.brokers;
```

### Find brokers missing email (for enrichment)

**What it does:** Lists brokers with no `primary_email` so you can run enrichment or manual add.

```sql
SELECT mc_number, company_name FROM webwise.brokers WHERE primary_email IS NULL LIMIT 20;
```

---

## Add/update one broker

**What it does:** Adds or updates one broker’s email, phone, website, and/or DOT. Creates the broker row if missing. Emails go into `broker_emails`; the best one is set as `primary_email`. If you add a second phone, it goes to `secondary_phone`. Use this for manual contact updates.

| Option | Meaning |
|--------|--------|
| `--mc` | MC number (required) |
| `--email` | Dispatch email (canonical; plus-addresses like `+tag` are stripped) |
| `--phone` | Primary or secondary phone |
| `--website` | Broker website (URL ok; stored as domain) |
| `--dot` | USDOT number |
| `--call-to-book` | Sets `preferred_contact_method = call_to_book` (use with `--phone`) |

**Examples:**

```bash
cd /srv/projects/client/dispatch

# Full: email + phone + website + DOT
PYTHONPATH=. python3 app/scripts/add_broker_contact.py --mc 322572 \
  --email "carrier@tql.com" --phone "+1 (800) 580-3101" \
  --website "https://tql.com" --dot 2223295

# Email only
PYTHONPATH=. python3 app/scripts/add_broker_contact.py --mc 965005 --email "loads@freshfreight.com"

# Phone only (marks as call-to-book)
PYTHONPATH=. python3 app/scripts/add_broker_contact.py --mc 945637 --phone "+1 (602) 755-3668" --call-to-book
```

---

## Bulk ingestion

**What these do:** Load many brokers into `webwise.brokers` from an external source (FMCSA API or a CSV). Use after initial setup or to refresh broker list. They do **not** add email/phone; use enrichment or add_broker_contact for that.

| Script | What it does | Source |
|--------|----------------|--------|
| `app/scripts/ingest_active_brokers.py` | Inserts/updates active brokers from FMCSA API (~25k+). Loads MC, DOT, name, DBA, physical address. No phone/email. | FMCSA Socrata API; needs `DOT_API_TOKEN` in `.env` |
| `scripts/load_fmcsa_brokers.py` | Loads a small sample from a CSV (e.g. ~187 brokers where `carship` contains 'B'). | Local CSV (e.g. az4n-8mr2.csv) |

**Run FMCSA ingest (from project root):**

```bash
cd /srv/projects/client/dispatch
PYTHONPATH=. python3 app/scripts/ingest_active_brokers.py
```

---

## Enrichment

**What these do:** Add contact data (emails, phones, websites) to brokers already in the DB. Use after ingestion or when you have new sources (carrier packets, search, scrape).

| Script | What it does |
|--------|----------------|
| `app/scripts/add_broker_contact.py` | Manually add/update one broker’s email, phone, website, DOT. See [Add/update one broker](#addupdate-one-broker). |
| `app/scripts/find_dispatch_contacts.py` | Search (e.g. DuckDuckGo) for broker emails/phones and attach to brokers. |
| `app/scripts/scrape_emails_from_websites.py` | Scrape broker websites for email addresses and add to `broker_emails`. |
| `app/scripts/enrich_broker_websites.py` | For brokers with no website: find website URLs via search and set `website`. |
| `scripts/attach_packet_emails.py` | Parse carrier-packet or forwarded email text, extract emails, and attach to the matching broker. |

---

## Potentially Missing Columns (for consideration)

| Column | Type | Use case |
|--------|------|----------|
| `load_board_source` | VARCHAR(50)[] or JSONB | Which boards list this broker (DAT, Truckstop, TruckSmarter) |
| `last_contact_verified` | DATE | When contact info was last confirmed |
| `contact_source` | VARCHAR(50) | Where primary contact came from (e.g. `trucksmarter`, `dat`, `manual`) |
| `mailing_street`, `mailing_city`, `mailing_state`, `mailing_zip` | VARCHAR | Mailing address (FMCSA has both physical and mailing) |
| `fmcsa_status` | VARCHAR(20) | ACTIVE, INACTIVE, OUT_OF_SERVICE |
| `operating_authority` | VARCHAR(100) | e.g. "AUTHORIZED FOR BROKER Property" |

To add any of these:

```sql
ALTER TABLE webwise.brokers ADD COLUMN IF NOT EXISTS contact_source VARCHAR(50);
ALTER TABLE webwise.brokers ADD COLUMN IF NOT EXISTS last_contact_verified DATE;
```

---

## Email Source Values

| Source | Meaning |
|--------|---------|
| `manual` | Added via add_broker_contact.py |
| `carrier_packet` | Parsed from carrier packet / forwarded email |
| `website` | Scraped from broker website |
| `search` | Found via find_dispatch_contacts.py |
| `fmcsa` | From FMCSA registration (rare) |

---

## Quick reference (cheat sheet)

| What you want | Command or SQL |
|---------------|-----------------|
| Connect to DB | `psql "$DATABASE_URL"` (or `source .env` first) |
| Look up by MC | `SELECT * FROM webwise.brokers WHERE mc_number = '123456';` |
| Look up by DOT | `SELECT * FROM webwise.brokers WHERE dot_number = '2223295';` |
| Search by name | `SELECT * FROM webwise.brokers WHERE company_name ILIKE '%PARTIAL%NAME%';` |
| Brokers missing email | `SELECT mc_number, company_name FROM webwise.brokers WHERE primary_email IS NULL LIMIT 20;` |
| Add/update one contact | `PYTHONPATH=. python3 app/scripts/add_broker_contact.py --mc 123456 --email x@y.com [--phone ...]` |
| Bulk ingest from FMCSA | `PYTHONPATH=. python3 app/scripts/ingest_active_brokers.py` |

---

## Data Sources

- **FMCSA SAFER:** https://safer.fmcsa.dot.gov/CompanySnapshot.aspx — free lookup by MC/DOT
- **FMCSA QCMobile API:** https://mobile.fmcsa.dot.gov/QCDevsite/ — free API (phone, address; no email)
- **Load boards (DAT, Truckstop):** Best for per-load contact; MC# required for reliable matching
