#!/usr/bin/env bash
# Weekly billing job runner — called by cron every Friday at 8:00 PM ET.
# Hits the internal billing endpoint inside the running app container.
#
# Cron entry (run as root on the host):
#   0 20 * * 5 /srv/gcloads-app/scripts/run_weekly_billing.sh >> /var/log/gcd_billing.log 2>&1
#
# Requires ADMIN_TOKEN to be set in /srv/gcloads-app/.env (or exported in environment).

set -euo pipefail

APP_URL="${APP_URL:-http://127.0.0.1:8369}"
DRY_RUN="${DRY_RUN:-false}"
LOG_PREFIX="[billing_cron $(date -u +%Y-%m-%dT%H:%M:%SZ)]"

# Load ADMIN_TOKEN from .env if not already in environment
if [ -z "${ADMIN_TOKEN:-}" ]; then
    ENV_FILE="/srv/gcloads-app/.env"
    if [ -f "$ENV_FILE" ]; then
        ADMIN_TOKEN=$(grep -E '^ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi
fi

if [ -z "${ADMIN_TOKEN:-}" ]; then
    echo "$LOG_PREFIX ERROR: ADMIN_TOKEN not set — aborting"
    exit 1
fi

echo "$LOG_PREFIX Starting weekly billing job dry_run=$DRY_RUN"

RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "x-admin-token: ${ADMIN_TOKEN}" \
    "${APP_URL}/internal/billing/run?dry_run=${DRY_RUN}")

HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

echo "$LOG_PREFIX HTTP $HTTP_CODE"
echo "$LOG_PREFIX Response: $HTTP_BODY"

if [ "$HTTP_CODE" != "200" ]; then
    echo "$LOG_PREFIX ERROR: billing job returned HTTP $HTTP_CODE"
    exit 1
fi

echo "$LOG_PREFIX Done"
