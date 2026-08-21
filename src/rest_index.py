from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from math import ceil

import httpx

from app.embeddings.text import build_embedding_text, build_sparse_text

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"
IndexProgressCallback = Callable[[int], Awaitable[None]]


def model_scoped_collection_name(base_name: str, embedding_model: str, sparse_model: str) -> str:
    identity = f"{embedding_model}|{sparse_model}"
    model_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{base_name}__{model_hash}"


class RestHybridIndexer:
    def __init__(self, *, base_url: str, api_key: str, collection_name: str, dense_embedder, sparse_embedder) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection_name = model_scoped_collection_name(
            collection_name,
            dense_embedder.model_name,
            sparse_embedder.model_name,
        )
        self.dense_embedder = dense_embedder
        self.sparse_embedder = sparse_embedder
        self.http = httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(180.0),
            follow_redirects=True,
            headers={"api-key": api_key, "content-type": "application/json"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def preflight(self) -> None:
        response = await self.http.get(f"{self.base_url}/collections")
        response.raise_for_status()

    async def ensure_collection(self) -> None:
        response = await self.http.get(f"{self.base_url}/collections/{self.collection_name}")
        if response.status_code == 404:
            create = await self.http.put(
                f"{self.base_url}/collections/{self.collection_name}",
                json={
                    "vectors": {
                        DENSE_VECTOR_NAME: {
                            "size": self.dense_embedder.dimension,
                            "distance": "Cosine",
                        }
                    },
                    "sparse_vectors": {
                        SPARSE_VECTOR_NAME: {"modifier": "idf"}
                    },
                },
            )
            create.raise_for_status()
            return
        response.raise_for_status()

    async def index(self, chunks: Sequence[object], *, remove_stale_source_versions: bool = False, progress_callback: IndexProgressCallback | None = None) -> int:
        if not chunks:
            return 0
        source_ids = {chunk.source_id for chunk in chunks}
        commit_shas = {chunk.commit_sha for chunk in chunks}
        if remove_stale_source_versions and (len(source_ids) != 1 or len(commit_shas) != 1):
            raise ValueError("Stale-version cleanup requires chunks from one source and one commit")

        await self.ensure_collection()
        batch_size = max(1, min(self.dense_embedder.batch_size, self.sparse_embedder.batch_size))
        batch_count = ceil(len(chunks) / batch_size)
        indexed = 0

        for batch_index, offset in enumerate(range(0, len(chunks), batch_size), start=1):
            batch = list(chunks[offset : offset + batch_size])
            print(f"[index] batch {batch_index}/{batch_count}: embedding {len(batch)} chunks", flush=True)
            dense_texts = [build_embedding_text(chunk) for chunk in batch]
            sparse_texts = [build_sparse_text(chunk) for chunk in batch]
            dense_vectors = await self.dense_embedder.embed_documents(dense_texts)
            sparse_vectors = await self.sparse_embedder.embed_documents(sparse_texts)

            points = []
            for chunk, dense_vector, sparse_vector in zip(batch, dense_vectors, sparse_vectors, strict=True):
                sparse_indices = list(getattr(sparse_vector, "indices"))
                sparse_values = list(getattr(sparse_vector, "values"))
                points.append(
                    {
                        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"tractusmind:{chunk.chunk_id}")),
                        "vector": {
                            DENSE_VECTOR_NAME: list(dense_vector),
                            SPARSE_VECTOR_NAME: {
                                "indices": sparse_indices,
                                "values": sparse_values,
                            },
                        },
                        "payload": self._payload(chunk),
                    }
                )

            response = await self.http.put(
                f"{self.base_url}/collections/{self.collection_name}/points",
                params={"wait": "true"},
                json={"points": points},
            )
            response.raise_for_status()
            indexed += len(points)
            if progress_callback is not None:
                await progress_callback(indexed)

        if remove_stale_source_versions:
            await self.remove_stale_source_versions(
                source_id=next(iter(source_ids)),
                current_commit_sha=next(iter(commit_shas)),
            )
        return indexed

    async def remove_stale_source_versions(self, *, source_id: str, current_commit_sha: str) -> None:
        response = await self.http.post(
            f"{self.base_url}/collections/{self.collection_name}/points/delete",
            params={"wait": "true"},
            json={
                "filter": {
                    "must": [
                        {"key": "source_id", "match": {"value": source_id}},
                    ],
                    "must_not": [
                        {"key": "snapshot_commit_sha", "match": {"value": current_commit_sha}},
                    ],
                }
            },
        )
        response.raise_for_status()

    def _payload(self, chunk) -> dict[str, object]:
        values = [chunk.path]
        if chunk.parent_symbol:
            values.append(chunk.parent_symbol)
        if chunk.symbol:
            values.append(chunk.symbol)
        values.extend(chunk.section_path)
        values.append(chunk.text)
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "source_id": chunk.source_id,
            "repository": chunk.repository,
            "component": chunk.component,
            "version_ref": chunk.version_ref,
            "snapshot_commit_sha": chunk.commit_sha,
            "commit_sha": chunk.commit_sha,
            "path": chunk.path,
            "blob_sha": chunk.blob_sha,
            "content_type": chunk.content_type,
            "language": chunk.language,
            "kind": chunk.kind.value,
            "text": chunk.text,
            "debug_text": "\n".join(values),
            "text_sha256": chunk.text_sha256,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "symbol": chunk.symbol,
            "parent_symbol": chunk.parent_symbol,
            "section_path": chunk.section_path,
            "part": chunk.part,
            "source_url": chunk.source_url,
            "line_source_url": chunk.line_source_url,
            "embedding_model": self.dense_embedder.model_name,
            "sparse_model": self.sparse_embedder.model_name,
        }
