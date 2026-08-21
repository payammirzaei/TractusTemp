#!/usr/bin/env bash
set -euo pipefail

chmod +x scripts/preflight.sh
./scripts/preflight.sh
mkdir -p state .model-cache

echo
echo "Starting one-shot TractusMind GPU bootstrap..."
docker compose up --build --abort-on-container-exit

if grep -Eq '^TRACTUSMIND_API_URL=.+$' .env && grep -Eq '^TRACTUSMIND_ADMIN_KEY=.+$' .env; then
  echo
  echo "Reconciling successful snapshots into TractusMind production state..."
  docker compose run --rm bulk-ingest python -m src.reconcile
else
  echo
  echo "Skipping production-state reconciliation: TRACTUSMIND_API_URL/admin key not configured."
fi
