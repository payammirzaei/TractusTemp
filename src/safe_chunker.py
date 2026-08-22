from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class ChunkKind(StrEnum):
    TEXT = "text"


@dataclass(frozen=True)
class SafeKnowledgeChunk:
    chunk_id: str
    document_id: str
    source_id: str
    repository: str
    component: str
    version_ref: str
    commit_sha: str
    path: str
    blob_sha: str
    content_type: str
    language: str | None
    kind: ChunkKind
    text: str
    text_sha256: str
    source_url: str
    start_line: int
    end_line: int
    symbol: str | None = None
    parent_symbol: str | None = None
    section_path: tuple[str, ...] = ()
    part: int = 1

    @property
    def line_source_url(self) -> str:
        if self.start_line == self.end_line:
            return f"{self.source_url}#L{self.start_line}"
        return f"{self.source_url}#L{self.start_line}-L{self.end_line}"


class SafeLineChunker:
    """Parser-free bootstrap chunker used only by the temporary HP ingest path.

    It deliberately avoids AST/tree-sitter/format parsers so a malformed source file
    cannot terminate the Python interpreter. Content and exact line provenance are
    preserved; production remains free to use the richer SmartChunker on normal syncs.
    """

    def __init__(self, max_chars: int = 1_800, overlap_lines: int = 3) -> None:
        self.max_chars = max_chars
        self.overlap_lines = overlap_lines

    def chunk_many(self, documents) -> list[SafeKnowledgeChunk]:
        chunks: list[SafeKnowledgeChunk] = []
        for document in documents:
            chunks.extend(self.chunk(document))
        return chunks

    def chunk(self, document) -> list[SafeKnowledgeChunk]:
        lines = document.content.splitlines(keepends=True)
        if not lines or not document.content.strip():
            return []

        chunks: list[SafeKnowledgeChunk] = []
        cursor = 0
        part = 1

        while cursor < len(lines):
            end = cursor
            chars = 0
            while end < len(lines):
                size = len(lines[end])
                if end > cursor and chars + size > self.max_chars:
                    break
                chars += size
                end += 1

            if end == cursor:
                end = cursor + 1

            text = "".join(lines[cursor:end]).strip()
            if text:
                text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                identity = ":".join(
                    [
                        document.document_id,
                        "text",
                        str(cursor + 1),
                        str(end),
                        str(part),
                        text_sha256,
                    ]
                )
                chunks.append(
                    SafeKnowledgeChunk(
                        chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                        document_id=document.document_id,
                        source_id=document.source_id,
                        repository=document.repository,
                        component=document.component,
                        version_ref=document.version_ref,
                        commit_sha=document.commit_sha,
                        path=document.path,
                        blob_sha=document.blob_sha,
                        content_type=document.content_type,
                        language=document.language,
                        kind=ChunkKind.TEXT,
                        text=text,
                        text_sha256=text_sha256,
                        source_url=document.source_url,
                        start_line=cursor + 1,
                        end_line=end,
                        part=part,
                    )
                )
                part += 1

            if end >= len(lines):
                break
            cursor = max(cursor + 1, end - max(0, self.overlap_lines))

        return chunks
