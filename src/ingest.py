from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from app.chunking import SmartChunker
from app.embeddings.sparse import SparseEmbeddingService
from app.ingestion.pipeline import SourceIngestionPipeline
from app.ingestion.registry import load_source_registry
from app.retrieval.hybrid import HybridRetrievalService

from src.config import Config
from src.gpu import GpuDenseEmbeddingService

REGISTRY = Path("/opt/tractusmind/config/sources.toml")
STATE_DIR = Path("/state")


def log(message: str) -> None:
    print(message, flush=True)


def marker_path(source_id: str) -> Path:
    return STATE_DIR / f"{source_id}.json"


def load_marker(source_id: str) -> dict[str, object] | None:
    path = marker_path(source_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_marker(source_id: str, payload: dict[str, object]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = marker_path(source_id)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(target)


async def preflight(config: Config, qdrant: AsyncQdrantClient) -> None:
    log("[preflight] checking Qdrant")
    await qdrant.get_collections()
    log(f"[preflight] Qdrant OK: {config.qdrant_url}")

    log(
        "[preflight] dense="
        f"{config.dense_model} devices={list(config.gpu_device_ids)} "
        f"parallel={config.gpu_parallel} batch={config.gpu_batch_size}"
    )
    log(f"[preflight] sparse={config.sparse_model} batch={config.sparse_batch_size}")


async def ingest_source(
    *,
    source,
    pipeline: SourceIngestionPipeline,
    retrieval: HybridRetrievalService,
    config: Config,
) -> dict[str, object]:
    started = time.perf_counter()
    log(f"\n[{source.id}] discovering {source.full_name}@{source.ref}")
    manifest = await pipeline.discover(source)

    previous = load_marker(source.id)
    if (
        config.skip_completed
        and previous
        and previous.get("status") == "succeeded"
        and previous.get("snapshot_commit_sha") == manifest.commit_sha
    ):
        log(f"[{source.id}] already complete at {manifest.commit_sha[:12]} — skipping")
        return previous

    save_marker(
        source.id,
        {
            "status": "running",
            "source_id": source.id,
            "repository": manifest.repository,
            "version_ref": manifest.requested_ref,
            "snapshot_commit_sha": manifest.commit_sha,
            "discovered_count": len(manifest.files),
            "started_at_unix": time.time(),
        },
    )

    log(f"[{source.id}] fetching {len(manifest.files)} files")
    documents = await pipeline.fetch_files(manifest, manifest.files)
    log(f"[{source.id}] fetched {len(documents)} documents; chunking")

    chunks = SmartChunker().chunk_many(documents)
    total = len(chunks)
    log(f"[{source.id}] produced {total:,} chunks; indexing")

    last_reported = 0

    async def report(indexed: int) -> None:
        nonlocal last_reported
        if indexed == total or indexed - last_reported >= config.gpu_batch_size:
            elapsed = max(time.perf_counter() - started, 0.001)
            rate = indexed / elapsed
            pct = (indexed / total * 100.0) if total else 100.0
            log(
                f"[{source.id}] {indexed:,}/{total:,} indexed "
                f"({pct:5.1f}%) @ {rate:,.1f} chunks/s"
            )
            last_reported = indexed

    indexed = await retrieval.index(
        chunks,
        remove_stale_source_versions=True,
        progress_callback=report,
    )

    elapsed = time.perf_counter() - started
    payload: dict[str, object] = {
        "status": "succeeded",
        "source_id": source.id,
        "repository": manifest.repository,
        "version_ref": manifest.requested_ref,
        "snapshot_commit_sha": manifest.commit_sha,
        "discovered_count": len(manifest.files),
        "fetched_count": len(documents),
        "chunk_count": total,
        "indexed_count": indexed,
        "elapsed_seconds": round(elapsed, 3),
        # This bundle is sufficient for a later TractusMind DB-state reconciliation
        # without downloading or embedding the source again.
        "files": [
            {
                "path": item.path,
                "blob_sha": item.sha,
                "size_bytes": item.size,
                "content_type": item.content_type,
            }
            for item in manifest.files
        ],
    }
    save_marker(source.id, payload)
    log(f"[{source.id}] SUCCEEDED — {indexed:,} chunks in {elapsed / 60:.1f} min")
    return payload


async def main_async() -> int:
    config = Config.from_env()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    dense = GpuDenseEmbeddingService(
        config.dense_model,
        batch_size=config.gpu_batch_size,
        device_ids=config.gpu_device_ids,
        parallel=config.gpu_parallel,
    )
    sparse = SparseEmbeddingService(
        config.sparse_model,
        batch_size=config.sparse_batch_size,
    )
    qdrant = AsyncQdrantClient(
        url=config.qdrant_url,
        api_key=config.qdrant_api_key,
        timeout=180,
    )
    retrieval = HybridRetrievalService(
        qdrant=qdrant,
        collection_name=config.qdrant_collection,
        dense_embedder=dense,
        sparse_embedder=sparse,
    )

    await preflight(config, qdrant)

    registry = [source for source in load_source_registry(REGISTRY) if source.enabled]
    if config.source_ids:
        requested = set(config.source_ids)
        registry = [source for source in registry if source.id in requested]
        missing = requested - {source.id for source in registry}
        if missing:
            raise RuntimeError(f"Unknown or disabled SOURCE_IDS: {sorted(missing)}")

    log(f"[start] selected {len(registry)} source(s): {', '.join(s.id for s in registry)}")
    failures: list[tuple[str, str]] = []
    results: list[dict[str, object]] = []

    try:
        async with SourceIngestionPipeline(
            token=config.github_token,
            timeout=config.github_timeout_seconds,
            concurrency=12,
            max_attempts=config.github_max_attempts,
            retry_base_seconds=1.0,
            retry_max_seconds=30.0,
            circuit_failure_threshold=5,
            circuit_cooldown_seconds=60.0,
        ) as pipeline:
            for source in registry:
                try:
                    result = await ingest_source(
                        source=source,
                        pipeline=pipeline,
                        retrieval=retrieval,
                        config=config,
                    )
                    results.append(result)
                except Exception as exc:  # noqa: BLE001 - one source must not kill the batch
                    message = f"{type(exc).__name__}: {exc}"
                    failures.append((source.id, message))
                    save_marker(
                        source.id,
                        {
                            "status": "failed",
                            "source_id": source.id,
                            "error": message,
                            "finished_at_unix": time.time(),
                        },
                    )
                    log(f"[{source.id}] FAILED — {message}")
                    if config.fail_fast:
                        raise
    finally:
        await qdrant.close()

    succeeded = sum(item.get("status") == "succeeded" for item in results)
    log("\n================ BULK INGEST SUMMARY ================")
    log(f"Succeeded: {succeeded}/{len(registry)}")
    for item in results:
        if item.get("status") == "succeeded":
            log(
                f"  ✓ {item['source_id']}: "
                f"{int(item.get('indexed_count', 0)):,} chunks"
            )
    for source_id, error in failures:
        log(f"  ✗ {source_id}: {error}")
    log("=====================================================")
    return 1 if failures else 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        log("\n[stop] interrupted; safe to re-run with SKIP_COMPLETED=true")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
