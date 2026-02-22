GCD Scout — Chrome Extension (/srv/gcloads-app/scout-extension)
4 files: manifest.json, content.js, options.html, options.js
What it is
A Manifest V3 Chrome extension that injects a "SHIP TO GCD" button onto load board pages. Driver browses a load, clicks the button, and the load data is harvested and POSTed to your API.
Supported load boards
trucksmarter.com
dat.com
truckstop.com
How it works
content.js injects a fixed green button (top-right, z-index 999999) on every matching page. Uses a MutationObserver so it survives SPA navigation. On click:
Harvests load data from document.body.innerText via regex + DOM queries:
load_id — Load ID \n ([0-9-]+)
mc_number, dot_number
email — <a href="mailto:"> first, regex fallback
phone — <a href="tel:"> first, regex fallback
price — $1,234 pattern
origin, destination
raw_notes — catches "Email bids only", "Must call", "Call to book", "Phone calls only" — this is what drives contact_instructions on the Load model
source — detected from hostname
POSTs to {apiBase}/api/scout/ingest with x-api-key header
Reads response and updates button state:
BLACKLISTED → red "🛑 BLOCKED: {note}" — permanent, doesn't reset
PREFERRED → blue "🔵 PREFERRED BROKER"
NOTE → amber "🟡 NOTE: {note}"
email_sent: true → green "✅ LOAD SECURED"
email_sent: false → amber "⚠️ QUEUED ({reason})"
HTTP error / network failure → red error state
options.html/js — settings popup with:
API Base URL (default https://greencandledispatch.com — now outdated, should be https://codriverfreight.com)
Scout API Key (password field)
Save and Test Connection buttons — test hits /api/scout/parsing-rules with 8s timeout
One thing that stands out now
The default DEFAULT_API_BASE in content.js and the placeholder in options.html both say https://greencandledispatch.com. Since you rebranded to codriverfreight.com today and the nginx/cert swap is live, any driver who hasn't manually updated their extension settings will be hitting the old domain. The old domain still works (nginx config was left in place), so nothing is broken — but it's worth updating the default in the extension source for any future installs.

==========
What would make it fully connected
There are two realistic paths:
Path A — Keep it manual (what you have), just make onboarding clearer
Add a real CRX download link or Chrome Web Store link to the setup page
The "Scout Active" toggle could instead be a server-side flag that controls whether ingested loads auto-bid (which auto_negotiate already does)
Honest framing: "Install the extension, browse loads, click the button — your filter profile tells CoDriver what to do with each load"
Path B — Make the extension filter-aware (future work)
On extension load, fetch the driver's profile from /api/scout/driver-profile using the API key
Extension checks each load page against the profile before deciding whether to highlight/auto-submit
This requires the extension to know the driver's identity (via API key → driver lookup)
Right now you're on Path A, but the setup page implies Path B. The honest answer to "how does Scout get activated on the driver's Chrome" is: it doesn't activate automatically — the driver installs the extension manually and the button is always there. The setup page configures what happens server-side when they click it.