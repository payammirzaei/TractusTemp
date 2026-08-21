from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import cached_property

from fastembed import TextEmbedding


class GpuDenseEmbeddingService:
    """FastEmbed GPU adapter matching TractusMind's dense embedder interface."""

    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int,
        device_ids: Sequence[int],
        parallel: int,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.device_ids = list(device_ids)
        self.parallel = parallel

    @cached_property
    def model(self) -> TextEmbedding:
        return TextEmbedding(
            model_name=self.model_name,
            lazy_load=True,
            cuda=True,
            device_ids=self.device_ids,
        )

    @cached_property
    def dimension(self) -> int:
        return int(TextEmbedding.get_embedding_size(self.model_name))

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_documents, list(texts))

    async def embed_query(self, query: str) -> list[float]:
        normalized = query.strip()
        if not normalized:
            raise ValueError("Query must not be empty")
        return await asyncio.to_thread(self._embed_query, normalized)

    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.passage_embed(
            texts,
            batch_size=self.batch_size,
            parallel=self.parallel,
        )
        return [vector.astype("float32").tolist() for vector in vectors]

    def _embed_query(self, query: str) -> list[float]:
        vector = next(
            iter(
                self.model.query_embed(
                    query,
                    batch_size=self.batch_size,
                )
            )
        )
        return vector.astype("float32").tolist()
