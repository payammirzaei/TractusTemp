from __future__ import annotations

import json
import os
from pathlib import Path

from app.ingestion.registry import load_source_registry

REGISTRY = Path("/opt/tractusmind/config/sources.toml")
STATE_DIR = Path("/state")


def _is_succeeded(source_id: str) -> bool:
    path = STATE_DIR / f"{source_id}.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("status") == "succeeded"


def main() -> None:
    requested = {
        item.strip()
        for item in os.getenv("SOURCE_IDS", "").split(",")
        if item.strip()
    }
    skip_completed = os.getenv("SKIP_COMPLETED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    for source in load_source_registry(REGISTRY):
        if not source.enabled:
            continue
        if requested and source.id not in requested:
            continue
        if skip_completed and _is_succeeded(source.id):
            continue
        print(f"SOURCE_ID={source.id}", flush=True)


if __name__ == "__main__":
    main()
