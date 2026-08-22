#!/usr/bin/env bash
set -uo pipefail

chmod +x scripts/preflight.sh
./scripts/preflight.sh || exit $?
mkdir -p state .model-cache repo-cache

echo
echo "Starting TractusMind organization-scale GPU bootstrap..."
echo "Successful existing source markers will be skipped when upstream is unchanged."
echo

set +e
docker compose up --build --abort-on-container-exit 2>&1 | tee bootstrap.log
INGEST_STATUS=${PIPESTATUS[0]}
set -e

RECONCILE_STATUS=0
if grep -Eq '^TRACTUSMIND_API_URL=.+$' .env && grep -Eq '^TRACTUSMIND_ADMIN_KEY=.+$' .env; then
  echo
  echo "Reconciling every successful snapshot into TractusMind production state..."
  docker compose run --rm bulk-ingest python -m src.reconcile || RECONCILE_STATUS=$?
else
  echo
  echo "Skipping production-state reconciliation: TRACTUSMIND_API_URL/admin key not configured."
fi

echo
if [[ $INGEST_STATUS -eq 0 && $RECONCILE_STATUS -eq 0 ]]; then
  echo "Bootstrap + reconciliation completed successfully."
  exit 0
fi

echo "Bootstrap finished with ingest_status=$INGEST_STATUS reconcile_status=$RECONCILE_STATUS."
echo "Successful sources are preserved in state/; fix/retry failures with ./run.sh."
exit 1
