from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from app.chunking import SmartChunker
from app.embeddings.sparse import SparseEmbeddingService
from app.ingestion.pipeline import SourceIngestionPipeline
from app.ingestion.registry import load_source_registry

from src.config import Config
from src.gpu import GpuDenseEmbeddingService
from src.local_git import LocalGitSourceLoader
from src.rest_index import RestHybridIndexer

REGISTRY = Path("/opt/tractusmind/config/sources.toml")
STATE_DIR = Path("/state")
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def log(message: str) -> None:
    print(message, flush=True)


def clear_proxy_environment() -> None:
    removed = [name for name in _PROXY_ENV_VARS if os.environ.pop(name, None) is not None]
    if removed:
        log(f"[network] ignored proxy environment: {', '.join(removed)}")


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


async def ingest_source(
    *,
    source,
    pipeline: SourceIngestionPipeline | None,
    local_loader: LocalGitSourceLoader | None,
    retrieval: RestHybridIndexer,
    config: Config,
) -> dict[str, object]:
    started = time.perf_counter()
    previous = load_marker(source.id)

    if local_loader is not None:
        log(f"\n[{source.id}] resolving {source.full_name}@{source.ref} via git")
        snapshot_sha = await local_loader.resolve_snapshot(source)
        if (
            config.skip_completed
            and previous
            and previous.get("status") == "succeeded"
            and previous.get("snapshot_commit_sha") == snapshot_sha
        ):
            log(f"[{source.id}] already complete at {snapshot_sha[:12]} — skipping")
            return previous

        save_marker(
            source.id,
            {
                "status": "running",
                "source_id": source.id,
                "repository": source.full_name,
                "version_ref": source.ref,
                "snapshot_commit_sha": snapshot_sha,
                "started_at_unix": time.time(),
                "loader": "local-git",
            },
        )
        log(f"[{source.id}] shallow clone/fetch + local file selection")
        manifest, documents = await local_loader.load(source, expected_commit=snapshot_sha)
    else:
        if pipeline is None:
            raise RuntimeError("GitHub API pipeline is unavailable")
        log(f"\n[{source.id}] discovering {source.full_name}@{source.ref}")
        manifest = await pipeline.discover(source)
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
                "loader": "github-api",
            },
        )
        log(f"[{source.id}] fetching {len(manifest.files)} files")
        documents = await pipeline.fetch_files(manifest, manifest.files)

    log(f"[{source.id}] selected {len(manifest.files)} files; loaded {len(documents)} documents; chunking")
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
            log(f"[{source.id}] {indexed:,}/{total:,} indexed ({pct:5.1f}%) @ {rate:,.1f} chunks/s")
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
        "loader": "local-git" if local_loader is not None else "github-api",
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


async def run_registry(
    *,
    registry,
    pipeline: SourceIngestionPipeline | None,
    local_loader: LocalGitSourceLoader | None,
    retrieval: RestHybridIndexer,
    config: Config,
) -> tuple[list[dict[str, object]], list[tuple[str, str]]]:
    failures: list[tuple[str, str]] = []
    results: list[dict[str, object]] = []
    for source in registry:
        try:
            result = await ingest_source(
                source=source,
                pipeline=pipeline,
                local_loader=local_loader,
                retrieval=retrieval,
                config=config,
            )
            results.append(result)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            failures.append((source.id, message))
            save_marker(
                source.id,
                {
                    "status": "failed",
                    "source_id": source.id,
                    "repository": source.full_name,
                    "error": message,
                    "finished_at_unix": time.time(),
                },
            )
            log(f"[{source.id}] FAILED — {message}")
            if config.fail_fast:
                raise
    return results, failures


async def main_async() -> int:
    clear_proxy_environment()
    config = Config.from_env()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    dense = GpuDenseEmbeddingService(
        config.dense_model,
        batch_size=config.gpu_batch_size,
        device_ids=config.gpu_device_ids,
        parallel=config.gpu_parallel,
    )
    sparse = SparseEmbeddingService(config.sparse_model, batch_size=config.sparse_batch_size)
    retrieval = RestHybridIndexer(
        base_url=config.qdrant_url,
        api_key=config.qdrant_api_key,
        collection_name=config.qdrant_collection,
        dense_embedder=dense,
        sparse_embedder=sparse,
    )

    log("[network] using direct Qdrant REST via httpx trust_env=False")
    log("[preflight] checking Qdrant")
    await retrieval.preflight()
    log(f"[preflight] Qdrant OK: {config.qdrant_url}")
    log(
        f"[preflight] dense={config.dense_model} devices={list(config.gpu_device_ids)} "
        f"parallel={config.gpu_parallel} batch={config.gpu_batch_size}"
    )
    log(f"[preflight] sparse={config.sparse_model} batch={config.sparse_batch_size}")
    log(
        "[preflight] source loader="
        + (f"local-git cache={config.repo_cache_dir}" if config.local_git_bootstrap else "github-api")
    )

    registry = [source for source in load_source_registry(REGISTRY) if source.enabled]
    if config.source_ids:
        requested = set(config.source_ids)
        registry = [source for source in registry if source.id in requested]
        missing = requested - {source.id for source in registry}
        if missing:
            raise RuntimeError(f"Unknown or disabled SOURCE_IDS: {sorted(missing)}")

    log(f"[start] selected {len(registry)} source(s): {', '.join(s.id for s in registry)}")
    try:
        if config.local_git_bootstrap:
            local_loader = LocalGitSourceLoader(
                config.repo_cache_dir,
                git_timeout_seconds=config.git_timeout_seconds,
            )
            results, failures = await run_registry(
                registry=registry,
                pipeline=None,
                local_loader=local_loader,
                retrieval=retrieval,
                config=config,
            )
        else:
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
                results, failures = await run_registry(
                    registry=registry,
                    pipeline=pipeline,
                    local_loader=None,
                    retrieval=retrieval,
                    config=config,
                )
    finally:
        await retrieval.close()

    succeeded = sum(item.get("status") == "succeeded" for item in results)
    log("\n================ BULK INGEST SUMMARY ================")
    log(f"Succeeded: {succeeded}/{len(registry)}")
    for item in results:
        if item.get("status") == "succeeded":
            log(f"  ✓ {item['source_id']}: {int(item.get('indexed_count', 0)):,} chunks")
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
