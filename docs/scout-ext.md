# CoDriver Scout — Chrome Web Store Submission Guide

## What's in the extension folder

```
scout-extension/
├── manifest.json       ← Extension config (v1.2, Manifest V3)
├── content.js          ← Injected script — adds "Ship to Dispatch" button on load boards
├── options.html        ← Settings page — where driver pastes their API key
├── options.js          ← Settings page logic
├── popup.html          ← Toolbar popup — shows connection status
├── popup.js            ← Popup logic
└── icons/
    ├── icon16.png      ← 16×16 toolbar icon
    ├── icon48.png      ← 48×48 extensions page icon
    └── icon128.png     ← 128×128 Web Store listing icon
```

---

## Step 1 — Register as a Chrome Web Store developer (one-time, $5)

1. Go to: https://chrome.google.com/webstore/devconsole
2. Sign in with your Google account
3. Pay the one-time $5 developer registration fee
4. Accept the developer agreement

---

## Step 2 — Create the ZIP file

On your server or local machine, zip **only the contents** of the `scout-extension/` folder.
The ZIP must contain `manifest.json` at the root — not inside a subfolder.

```bash
cd /srv/gcloads-app/scout-extension
zip -r ../codriver-scout-v1.2.zip .
```

This creates `/srv/gcloads-app/codriver-scout-v1.2.zip`.

To download it to your local machine (run this in your local terminal):
```bash
scp root@YOUR_SERVER_IP:/srv/gcloads-app/codriver-scout-v1.2.zip ~/Desktop/
```

---

## Step 3 — Create the listing

1. Go to: https://chrome.google.com/webstore/devconsole
2. Click **"New Item"**
3. Upload the ZIP file
4. Google will parse `manifest.json` and pre-fill the name and version

---

## Step 4 — Fill in the listing details

### Store listing tab

| Field | Value |
|---|---|
| **Name** | CoDriver Scout |
| **Short description** (132 chars max) | Adds a "Ship to Dispatch" button on DAT, TruckSmarter, and TruckStop. Sends load data to your CoDriver dashboard instantly. |
| **Detailed description** | See below |
| **Category** | Productivity |
| **Language** | English |

**Detailed description (paste this):**
```
CoDriver Scout is the load harvesting companion for CoDriver Freight (codriverfreight.com).

When you're browsing DAT, TruckSmarter, or TruckStop, Scout adds a green "SHIP TO DISPATCH" button to each load listing. Click it and the load details — origin, destination, price, equipment type, broker MC number — are sent directly to your CoDriver dashboard. If auto-negotiate is enabled, CoDriver emails the broker immediately.

REQUIREMENTS:
• A CoDriver account at codriverfreight.com (free 7-day trial, no card required)
• Your personal API key from your Scout Setup page (Dashboard → Scout Setup)

HOW TO SET UP:
1. Sign up at codriverfreight.com
2. Go to Dashboard → Scout Setup → copy your API key
3. Click the Scout icon in your Chrome toolbar → Settings → paste your API key
4. Browse DAT, TruckSmarter, or TruckStop — the "Ship to Dispatch" button will appear

SUPPORTED LOAD BOARDS:
• DAT One (dat.com)
• TruckSmarter (trucksmarter.com)
• TruckStop (truckstop.com)

Your API key is unique to your account. Without it the extension does nothing.
```

---

## Step 5 — Upload images

### Icons (already in the ZIP — no action needed)
The ZIP includes `icons/icon16.png`, `icons/icon48.png`, `icons/icon128.png`.

### Screenshots (you must provide these — Google requires at least 1)
Size: **1280×800 px** or **640×400 px**, PNG or JPEG.

Take a screenshot of:
1. TruckSmarter or DAT with the green "SHIP TO DISPATCH" button visible on a load
2. The CoDriver dashboard showing a load that just came in from Scout

If you don't have a real screenshot yet, use a browser window at 1280×800 and load a test page.

### Promotional tile (optional but recommended)
Size: **440×280 px**. A simple branded image with the CoDriver Scout logo and tagline.

---

## Step 6 — Privacy tab (REQUIRED — Google will reject without this)

You must provide a **Privacy Policy URL**.

Add a simple privacy policy page to your site at:
```
https://codriverfreight.com/privacy
```

Minimum content needed:
- What data the extension collects (load details from load board pages)
- Where it sends it (your CoDriver account on codriverfreight.com)
- That it does not collect personal browsing data outside of supported load board sites
- Contact email for privacy questions

Set the **Privacy Policy URL** field in the listing to: `https://codriverfreight.com/privacy`

---

## Step 7 — Visibility and distribution

| Field | Value |
|---|---|
| **Visibility** | Unlisted |
| **Distribution** | All regions |

**Unlisted** means only people with the direct Chrome Web Store link can install it.
It will not appear in search results. This is correct for your stage.

---

## Step 8 — Submit for review

1. Click **"Submit for Review"**
2. Google will review within **1–3 business days** (usually 1 day for simple content script extensions)
3. You'll receive an email when approved or if changes are needed

---

## Step 9 — After approval

1. You'll get a permanent Chrome Web Store URL like:
   `https://chrome.google.com/webstore/detail/codriver-scout/EXTENSION_ID`

2. Update these two files with the real URL (replace the placeholder `https://chrome.google.com/webstore`):
   - `/srv/gcloads-app/app/templates/public/faq.html` — the "Install Scout" button
   - `/srv/gcloads-app/app/templates/drivers/scout_setup.html` — the "Install Scout" button
   - `/srv/gcloads-app/app/templates/drivers/dashboard.html` — the nudge card

3. Sync the templates into the container:
   ```bash
   docker cp app/templates/public/faq.html gcloads_api:/code/app/templates/public/faq.html
   docker cp app/templates/drivers/scout_setup.html gcloads_api:/code/app/templates/drivers/scout_setup.html
   docker cp app/templates/drivers/dashboard.html gcloads_api:/code/app/templates/drivers/dashboard.html
   docker restart gcloads_api
   ```

---

## Pushing updates later

1. Edit the extension files
2. Bump the version in `manifest.json` (e.g. `"version": "1.3"`)
3. Re-zip: `cd /srv/gcloads-app/scout-extension && zip -r ../codriver-scout-v1.3.zip .`
4. Go to the Developer Console → your listing → **"Upload new package"**
5. Submit — updates usually approve same day

---

## Common rejection reasons and fixes

| Rejection reason | Fix |
|---|---|
| Missing privacy policy | Add `codriverfreight.com/privacy` page |
| Screenshots missing or wrong size | Must be exactly 1280×800 or 640×400 |
| `host_permissions` too broad | Already scoped to specific domains — should be fine |
| Misleading description | Keep description accurate to what the extension actually does |
| localhost in host_permissions | Already removed from manifest.json |
