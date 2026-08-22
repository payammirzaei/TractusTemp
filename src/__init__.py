from __future__ import annotations

from app.chunking.code import CodeChunker

_NATIVE_PARSER_DENYLIST = {"industry-core-hub"}
_NATIVE_LANGUAGES = {"kotlin", "typescript", "javascript"}
_ORIGINAL_CHUNK = CodeChunker.chunk


def _crash_safe_chunk(self: CodeChunker, document):
    """Avoid native tree-sitter crashes for known problematic source corpora.

    Python already uses stdlib AST and Java already uses the production safe
    line-bounded path. For the denylisted source, route the remaining native
    tree-sitter languages through the same deterministic line-bounded chunker.
    Provenance, stable chunk IDs and embedding/index contracts remain unchanged.
    """
    if (
        getattr(document, "source_id", None) in _NATIVE_PARSER_DENYLIST
        and getattr(document, "language", None) in _NATIVE_LANGUAGES
    ):
        return self._safe_code_chunks(document)
    return _ORIGINAL_CHUNK(self, document)


CodeChunker.chunk = _crash_safe_chunk
