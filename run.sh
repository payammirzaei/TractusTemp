#!/usr/bin/env bash
set -euo pipefail

chmod +x scripts/preflight.sh
./scripts/preflight.sh
mkdir -p state .model-cache

echo
echo "Starting one-shot TractusMind GPU bootstrap..."
docker compose up --build --abort-on-container-exit
