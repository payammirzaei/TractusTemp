from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.config import Config
from src.production_state import fetch_production_snapshots

STATE_DIR = Path("/state")


async def main_async() -> int:
    config = Config.from_env()
    if not config.skip_completed:
        print("[seed] SKIP_COMPLETED=false; production-state seeding disabled", flush=True)
        return 0
    if not config.tractusmind_api_url or not config.tractusmind_admin_key:
        print("[seed] production credentials not configured; using local state only", flush=True)
        return 0

    try:
        snapshots = await fetch_production_snapshots(
            api_url=config.tractusmind_api_url,
            admin_key=config.tractusmind_admin_key,
        )
    except Exception as exc:
        print(f"[seed] warning: could not read production snapshots: {type(exc).__name__}: {exc}", flush=True)
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    seeded = 0
    preserved = 0
    for source_id, snapshot in snapshots.items():
        path = STATE_DIR / f"{source_id}.json"
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
            if current.get("status") == "succeeded" and current.get("snapshot_commit_sha"):
                preserved += 1
                continue

        payload = {
            "status": "succeeded",
            "source_id": source_id,
            "snapshot_commit_sha": snapshot,
            "production_state": "adopted",
            "production_run_id": "existing-production-snapshot",
            "indexed_count": 0,
            "chunk_count": 0,
            "seeded_from_production": True,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        seeded += 1

    print(
        f"[seed] production snapshots ready: {len(snapshots)} known, {seeded} seeded, {preserved} preserved",
        flush=True,
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
