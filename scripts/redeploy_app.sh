#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[redeploy] Rebuilding app image and recreating app service..."
if docker-compose up -d --build app; then
  echo "[redeploy] App redeployed successfully."
  docker-compose ps
  exit 0
fi

echo "[redeploy] docker-compose recreate failed. Trying ContainerConfig fallback..."
stale_ids="$(docker ps -a --format '{{.ID}} {{.Names}}' | awk '/gcloads_api/ {print $1}')"
if [[ -n "$stale_ids" ]]; then
  echo "$stale_ids" | xargs -r docker rm -f
fi

docker-compose up -d app
docker-compose ps

echo "[redeploy] Recovery path completed."
