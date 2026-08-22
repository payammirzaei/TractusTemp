#!/usr/bin/env bash
set -uo pipefail

chmod +x scripts/preflight.sh
./scripts/preflight.sh || exit $?
mkdir -p state .model-cache repo-cache

echo
echo "Building the fresh GPU bootstrap image..."
docker compose build bulk-ingest || exit $?

echo
echo "Restoring current production snapshot markers (safe after a full local delete)..."
if grep -Eq '^TRACTUSMIND_API_URL=.+$' .env && grep -Eq '^TRACTUSMIND_ADMIN_KEY=.+$' .env; then
  docker compose run --rm --no-deps bulk-ingest python -m src.seed_production_state || true
else
  echo "Production credentials not configured; local-state seeding skipped."
fi

echo
echo "Discovering enabled sources that still need work..."
mapfile -t SOURCES < <(
  docker compose run --rm --no-deps bulk-ingest python -m src.list_sources 2>/dev/null \
    | sed -n 's/^SOURCE_ID=//p'
)

echo "Remaining sources: ${#SOURCES[@]}"
if [[ ${#SOURCES[@]} -gt 0 ]]; then
  printf '  - %s\n' "${SOURCES[@]}"
fi

echo
echo "Starting crash-isolated organization-scale GPU bootstrap..."
echo "Each source runs in its own container process; a native crash cannot stop later sources."
echo "Successful sources are reconciled to production immediately."
echo

touch bootstrap.log
FAILED_SOURCES=()
RECONCILE_FAILURES=()

for source_id in "${SOURCES[@]}"; do
  echo | tee -a bootstrap.log
  echo "================ SOURCE: ${source_id} ================" | tee -a bootstrap.log

  set +e
  docker compose run --rm --no-deps \
    -e SOURCE_IDS="${source_id}" \
    bulk-ingest python -m src.ingest 2>&1 | tee -a bootstrap.log
  ingest_status=${PIPESTATUS[0]}
  set -e

  if [[ ${ingest_status} -ne 0 ]]; then
    echo "[orchestrator] ${source_id} failed with exit=${ingest_status}; continuing." | tee -a bootstrap.log
    FAILED_SOURCES+=("${source_id}:${ingest_status}")
    continue
  fi

  if grep -Eq '^TRACTUSMIND_API_URL=.+$' .env && grep -Eq '^TRACTUSMIND_ADMIN_KEY=.+$' .env; then
    set +e
    docker compose run --rm --no-deps \
      -e SOURCE_IDS="${source_id}" \
      bulk-ingest python -m src.reconcile 2>&1 | tee -a bootstrap.log
    reconcile_status=${PIPESTATUS[0]}
    set -e
    if [[ ${reconcile_status} -ne 0 ]]; then
      echo "[orchestrator] reconcile failed for ${source_id} exit=${reconcile_status}; continuing." | tee -a bootstrap.log
      RECONCILE_FAILURES+=("${source_id}:${reconcile_status}")
    fi
  fi
done

echo | tee -a bootstrap.log
echo "================ BOOTSTRAP ORCHESTRATOR SUMMARY ================" | tee -a bootstrap.log
echo "Attempted remaining sources: ${#SOURCES[@]}" | tee -a bootstrap.log
if [[ ${#FAILED_SOURCES[@]} -eq 0 ]]; then
  echo "Ingest failures: none" | tee -a bootstrap.log
else
  echo "Ingest failures (${#FAILED_SOURCES[@]}): ${FAILED_SOURCES[*]}" | tee -a bootstrap.log
fi
if [[ ${#RECONCILE_FAILURES[@]} -eq 0 ]]; then
  echo "Reconcile failures: none" | tee -a bootstrap.log
else
  echo "Reconcile failures (${#RECONCILE_FAILURES[@]}): ${RECONCILE_FAILURES[*]}" | tee -a bootstrap.log
fi
echo "================================================================" | tee -a bootstrap.log

if [[ ${#FAILED_SOURCES[@]} -eq 0 && ${#RECONCILE_FAILURES[@]} -eq 0 ]]; then
  echo "Bootstrap + reconciliation completed successfully."
  exit 0
fi

echo "Bootstrap completed with isolated failures. Successful sources are preserved and adopted."
echo "Re-run ./run.sh after fixing failures; completed sources will be skipped."
exit 1
