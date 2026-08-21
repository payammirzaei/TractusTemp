from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Config:
    github_token: str
    qdrant_url: str
    qdrant_api_key: str | None
    qdrant_collection: str
    tractusmind_api_url: str | None
    tractusmind_admin_key: str | None
    dense_model: str
    sparse_model: str
    gpu_device_ids: tuple[int, ...]
    gpu_parallel: int
    gpu_batch_size: int
    sparse_batch_size: int
    source_ids: tuple[str, ...]
    skip_completed: bool
    fail_fast: bool
    github_timeout_seconds: float
    github_max_attempts: int

    @classmethod
    def from_env(cls) -> "Config":
        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        qdrant_url = os.getenv("QDRANT_URL", "").strip()
        if not github_token:
            raise RuntimeError("GITHUB_TOKEN is required")
        if not qdrant_url:
            raise RuntimeError("QDRANT_URL is required")

        raw_devices = os.getenv("GPU_DEVICE_IDS", "0,1")
        device_ids = tuple(int(item.strip()) for item in raw_devices.split(",") if item.strip())
        if not device_ids:
            raise RuntimeError("GPU_DEVICE_IDS must contain at least one GPU id")

        raw_sources = os.getenv("SOURCE_IDS", "")
        source_ids = tuple(item.strip() for item in raw_sources.split(",") if item.strip())

        api_url = os.getenv("TRACTUSMIND_API_URL", "").strip() or None
        admin_key = os.getenv("TRACTUSMIND_ADMIN_KEY", "").strip() or None
        if bool(api_url) != bool(admin_key):
            raise RuntimeError(
                "TRACTUSMIND_API_URL and TRACTUSMIND_ADMIN_KEY must be set together"
            )

        return cls(
            github_token=github_token,
            qdrant_url=qdrant_url.rstrip("/"),
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "tractusmind_knowledge"),
            tractusmind_api_url=api_url.rstrip("/") if api_url else None,
            tractusmind_admin_key=admin_key,
            dense_model=os.getenv("DENSE_MODEL", "BAAI/bge-small-en-v1.5"),
            sparse_model=os.getenv("SPARSE_MODEL", "Qdrant/bm25"),
            gpu_device_ids=device_ids,
            gpu_parallel=_int("GPU_PARALLEL", len(device_ids)),
            gpu_batch_size=_int("GPU_BATCH_SIZE", 256),
            sparse_batch_size=_int("SPARSE_BATCH_SIZE", 256),
            source_ids=source_ids,
            skip_completed=_bool("SKIP_COMPLETED", True),
            fail_fast=_bool("FAIL_FAST", False),
            github_timeout_seconds=float(os.getenv("GITHUB_TIMEOUT_SECONDS", "60")),
            github_max_attempts=_int("GITHUB_MAX_ATTEMPTS", 6),
        )
