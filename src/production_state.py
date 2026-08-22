from __future__ import annotations

import httpx


async def fetch_production_snapshots(
    *,
    api_url: str | None,
    admin_key: str | None,
) -> dict[str, str]:
    """Return source_id -> snapshot_commit_sha from TractusMind production.

    Bootstrap remains usable without production credentials; in that case this
    optimization is simply disabled and local state remains the fallback.
    """

    if not api_url or not admin_key:
        return {}

    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.get(
            f"{api_url.rstrip('/')}/v1/ops/sources",
            headers={"X-TractusMind-Admin-Key": admin_key},
        )
        response.raise_for_status()
        payload = response.json()

    snapshots: dict[str, str] = {}
    for item in payload:
        source_id = item.get("source_id")
        snapshot = item.get("snapshot_commit_sha")
        if isinstance(source_id, str) and isinstance(snapshot, str) and snapshot:
            snapshots[source_id] = snapshot
    return snapshots
