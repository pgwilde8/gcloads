Driver Templates & Partials Report
Full pages (/drivers/)
dashboard.html (~686 lines)
Purpose: Main driver hub (GCD Command Center).
Sections:
Header: Dispatch identity (trucker.dispatch_handle@gcdloads.com), MC, GCD Training link
Scout Picked Loads card → /drivers/scout-loads
Paperwork card → /drivers/uploads
Packet readiness panel (included partial)
Recent Scout Activity (last 5 ingests)
Scout status (HTMX, 60s poll)
Active negotiations (HTMX, 30s poll)
Automation Fuel balance
Rate settings modal trigger
Bottom nav (Dashboard, Negotiations, Scout Loads, Scout Setup, Paperwork, Help)
Help drawer (included partial)
Dependencies: balance, trucker, packet_readiness, scout_activity, driver_email, assigned_handle, show_beta_banner, user
driver_uploads.html (~179 lines)
Purpose: Paperwork – BOL uploads and send-to-factoring for won loads.
Features:
Won-load cards: origin → destination, rate, factoring status
BOL upload (HTMX, multipart)
Send-to-factoring (HTMX)
Manage Load / Terminal links
Empty state when no won loads
Dependencies: balance, won_loads
Note: Title uses "Green Candle"; nav links to /drivers/fleet (may be unimplemented).
gcdtraining.html (~22 lines)
Purpose: Placeholder for training videos.
Features: Heading, Back to Dashboard, “Training videos coming soon”.
load_board.html (~42 lines)
Purpose: Staging area for Scout loads.
Features:
“Top Available Loads” list (origin → destination, equipment, price)
“Deploy Scout” buttons (non-functional – no real Scout launch)
Dependencies: balance, loads
century_apply.html (~76 lines)
Purpose: Century Finance factoring application form.
Layout: Extends layout/base.html.
Fields: Full name, email, phone, MC, DOT, trucks, company, volume, factoring company, funding speed.
Submit: POST to /century/apply
onboarding_factoring.html (~37 lines)
Purpose: Factoring choice (already have vs need help).
Features: Yes/No radio, factor packet email if Yes, POST to /onboarding/factoring.
Layout: Extends layout/base.html.
onboarding_pending_century.html (~20 lines)
Purpose: Post-submit confirmation for Century.
Features: “Submitted — Century Will Reach Out”, packet readiness panel, “Open Digital Briefcase”.
Layout: Extends layout/base.html.
onboarding_step3.html (~87 lines)
Purpose: Digital Briefcase – Golden Trio uploads (MC Auth, COI, W9).
Features:
HTMX upload forms per doc
“ENTER DASHBOARD” enabled after all 3 docs
Links to /drivers/dashboard?welcome=true
Layout: Extends layout/base.html.
Note: Uses ?welcome=true; functionally redundant with session-only routing.
rate_con_sign.html (~93 lines)
Purpose: Sign rate con PDF after winning a load.
Features:
PDF viewer iframe
Signature Pad (signature_pad.js)
Clear / Apply signature
POST to /api/negotiations/{id}/apply-signature
Dependencies: negotiation_id, viewer_url
scout_loads.html (~333 lines)
Purpose: Scout loads and approval queue.
Features:
Tabs: “Needs Approval” (queued) and “All Loads”
Queued: Approve & Send / Dismiss (HTMX), match score badge, criteria pills
All loads: Filtered load list
Profile setup prompt when incomplete
Bottom nav
Dependencies: loads, queued_items, queued_count, driver, profile_complete, active_tab
scout_setup.html (~319 lines)
Purpose: Scout extension setup and preferences.
Features:
Personal API key (copy, regenerate, test)
Form: origin/destination regions, equipment, min CPM, min flat, auto-negotiate, review-before-send, auto-send on 4/4, Scout Active
POST to /drivers/scout-setup
“How Scout Works” explainer
Dependencies: driver, scout_api_key, saved
Partials (/drivers/partials/)
active_loads_list.html (~202 lines)
Purpose: Active negotiations list (HTMX target for dashboard).
States per card:
Call required: CALL badge, broker phone, “Call Broker”, call panel
Pending review: AI draft, Approve & Send
RATE_CON_RECEIVED: View & Sign
CLOSED_PENDING_EMAIL: warning
Broker email: message preview, Send Quick Reply
Includes: call_panel.html (with context)
Note: Duplicate “Call Broker” block (lines 15–27 and 28–34). call_panel uses load_ref, broker_phone, etc.; card context may need explicit mapping.
beta_banner.html (~4 lines)
Purpose: Beta access notice.
Content: Single div: “Beta access is enabled. Features and pricing can change during rollout.”
call_panel.html (~52 lines)
Purpose: Slide-out call-log panel for call-required negotiations.
Features: Broker phone (copy), call script (copy), outcome select, rate, notes, next follow-up, POST to /api/call-log.
Expected vars: load_ref, broker_phone, driver_name/carrier_name, origin, destination, negotiation_id, broker_id
Note: Included from active_loads_list; variable mapping may rely on with context and card structure.
first_mission.html (~8 lines)
Purpose: First-time welcome overlay.
Features: “Welcome to your first mission” modal, “Got it” dismiss.
help_drawer.html (~12 lines)
Purpose: Slide-out help drawer.
Features: Mission Help, short definitions for Scout Loads, Active Negotiations, Automation Fuel.
packet_readiness_panel.html (~34 lines)
Purpose: Digital Briefcase status (W9, COI, MC Auth).
Features: Ready / Missing status, doc grid, link to /onboarding/step3.
Dependencies: packet_readiness (docs, uploaded_count, required_count, is_ready, missing_labels)
rate_settings.html (~69 lines)
Purpose: Floor rate / negotiation rules modal.
Features: Min CPM, min flat rate, auto-negotiate, review-before-send. POST to /api/drivers/update-rates.
Note: Still sends hidden email field; backend uses session, so this is legacy and can be removed.
scout_status_indicator.html (~101 lines)
Purpose: Scout status in dashboard (HTMX target, 60s poll).
States:
Not configured: “Set Up Scout”
Active: profile summary, “N queued” badge, Edit
Paused: profile summary, queued badge if any, Activate
Dependencies: profile_complete, status, driver, queued_count
system_health.html (~36 lines)
Purpose: “System Vitals” panel.
Features: AI spend, efficiency, CPU bar, “The Closer: Active”.
Dependencies: stats (ai_spend, cpu_usage)
Summary
File	Type	Notes
dashboard.html	Page	Main hub; many included partials
driver_uploads.html	Page	BOL/factoring; links to /drivers/fleet
gcdtraining.html	Page	Placeholder
load_board.html	Page	“Deploy Scout” not wired
century_apply.html	Page	Century form; extends base
onboarding_.html | Page | Onboarding flow | | rate_con_sign.html | Page | PDF signing | | 