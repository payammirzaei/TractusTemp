from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.config import Config

STATE_DIR = Path("/state")


async def reconcile_file(
    client: httpx.AsyncClient,
    *,
    api_url: str,
    admin_key: str,
    path: Path,
) -> tuple[str, bool, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    source_id = str(payload.get("source_id", path.stem))
    if payload.get("status") != "succeeded":
        return source_id, False, "local state is not succeeded"

    body = {
        "version_ref": payload["version_ref"],
        "snapshot_commit_sha": payload["snapshot_commit_sha"],
        "files": payload.get("files", []),
        "chunk_count": payload["chunk_count"],
        "indexed_count": payload["indexed_count"],
        "force_unlock": True,
    }
    response = await client.post(
        f"{api_url}/v1/ops/sources/{source_id}/adopt-snapshot",
        headers={"X-TractusMind-Admin-Key": admin_key},
        json=body,
    )
    if response.is_success:
        result = response.json()
        payload["production_state"] = "adopted"
        payload["production_run_id"] = result.get("run_id")
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return source_id, True, str(result.get("run_id", "adopted"))

    detail = response.text[:500]
    return source_id, False, f"HTTP {response.status_code}: {detail}"


async def main_async() -> int:
    config = Config.from_env()
    if not config.tractusmind_api_url or not config.tractusmind_admin_key:
        print("Reconciliation disabled: set TRACTUSMIND_API_URL and TRACTUSMIND_ADMIN_KEY")
        return 2

    files = sorted(STATE_DIR.glob("*.json"))
    if config.source_ids:
        selected = set(config.source_ids)
        files = [path for path in files if path.stem in selected]

    if not files:
        print("No local state bundles found to reconcile")
        return 1

    failures = 0
    async with httpx.AsyncClient(timeout=120) as client:
        for path in files:
            source_id, ok, message = await reconcile_file(
                client,
                api_url=config.tractusmind_api_url,
                admin_key=config.tractusmind_admin_key,
                path=path,
            )
            if ok:
                print(f"[reconcile] ✓ {source_id}: {message}", flush=True)
            else:
                failures += 1
                print(f"[reconcile] ✗ {source_id}: {message}", flush=True)

    return 1 if failures else 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
