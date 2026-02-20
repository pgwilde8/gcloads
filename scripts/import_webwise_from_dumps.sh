#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <brokers_dump.sql> <broker_emails_dump.sql>"
  exit 1
fi

BROKERS_DUMP="$1"
BROKER_EMAILS_DUMP="$2"

if [[ ! -f "$BROKERS_DUMP" ]]; then
  echo "Missing file: $BROKERS_DUMP"
  exit 1
fi

if [[ ! -f "$BROKER_EMAILS_DUMP" ]]; then
  echo "Missing file: $BROKER_EMAILS_DUMP"
  exit 1
fi

cd /srv/gcloads-app

echo "Resetting webwise tables..."
docker-compose exec -T db psql -v ON_ERROR_STOP=1 -U gcd_admin -d gcloads_db -c "TRUNCATE TABLE webwise.broker_emails, webwise.brokers RESTART IDENTITY CASCADE;"

echo "Importing brokers..."
sed "s/INSERT INTO webwise\\.brokers VALUES (/INSERT INTO webwise.brokers (mc_number,dot_number,company_name,dba_name,website,primary_email,primary_phone,secondary_phone,phy_street,phy_city,phy_state,phy_zip,rating,source,created_at,updated_at,fax,preferred_contact_method) VALUES (/g" "$BROKERS_DUMP" \
  | docker-compose exec -T db psql -v ON_ERROR_STOP=1 -U gcd_admin -d gcloads_db

echo "Importing broker_emails..."
docker-compose exec -T db psql -v ON_ERROR_STOP=1 -U gcd_admin -d gcloads_db < "$BROKER_EMAILS_DUMP"

echo "Row counts:"
docker-compose exec -T db psql -U gcd_admin -d gcloads_db -c "SELECT 'brokers' AS table_name, COUNT(*) AS rows FROM webwise.brokers UNION ALL SELECT 'broker_emails' AS table_name, COUNT(*) AS rows FROM webwise.broker_emails;"

echo "Done."
