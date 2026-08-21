#!/usr/bin/env bash
set -euo pipefail
watch -n 1 'nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw --format=csv,noheader'
