#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose v2 is not available" >&2
  exit 1
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "ERROR: nvidia-smi is not available on the host" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "ERROR: .env is missing. Run: cp .env.example .env" >&2
  exit 1
fi

if ! grep -Eq '^GITHUB_TOKEN=.+$' .env; then
  echo "ERROR: GITHUB_TOKEN is empty in .env" >&2
  exit 1
fi

if ! grep -Eq '^QDRANT_URL=.+$' .env; then
  echo "ERROR: QDRANT_URL is empty in .env" >&2
  exit 1
fi

echo "Host GPUs:"
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader

echo
echo "Checking NVIDIA Container Toolkit..."
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 \
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

echo
echo "Preflight OK"
