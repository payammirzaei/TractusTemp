from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from app.ingestion.content import _decode_source_text, _document_id, language_for_path
from app.ingestion.github import _content_type, _is_selected
from app.ingestion.models import RawDocument, SourceDefinition, SourceFile, SourceManifest


class LocalGitError(RuntimeError):
    pass


def _run(*args: str, cwd: Path | None = None, timeout: float = 900.0) -> str:
    try:
        result = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()[-2000:]
        raise LocalGitError(f"git command failed: {' '.join(args)} :: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LocalGitError(f"git command timed out: {' '.join(args)}") from exc
    return result.stdout.strip()


class LocalGitSourceLoader:
    """Fast bootstrap loader that avoids one GitHub API request per source file.

    Public Eclipse repositories are shallow-cloned into a persistent host cache.
    We still reuse TractusMind's source filters and RawDocument provenance model,
    so local GPU bootstrap converges on the same Qdrant payload contract as the
    production ingestion pipeline.
    """

    def __init__(self, cache_dir: str | Path, *, git_timeout_seconds: float = 900.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.git_timeout_seconds = git_timeout_seconds

    def _repo_dir(self, source: SourceDefinition) -> Path:
        safe = source.full_name.replace("/", "__").replace(".", "_")
        return self.cache_dir / safe

    def _remote_url(self, source: SourceDefinition) -> str:
        return f"https://github.com/{source.full_name}.git"

    async def resolve_snapshot(self, source: SourceDefinition) -> str:
        return await asyncio.to_thread(self._resolve_snapshot_sync, source)

    def _resolve_snapshot_sync(self, source: SourceDefinition) -> str:
        remote = self._remote_url(source)
        for ref in (f"refs/heads/{source.ref}", f"refs/tags/{source.ref}"):
            output = _run(
                "git",
                "ls-remote",
                remote,
                ref,
                timeout=self.git_timeout_seconds,
            )
            if output:
                return output.split()[0]
        output = _run(
            "git",
            "ls-remote",
            remote,
            source.ref,
            timeout=self.git_timeout_seconds,
        )
        if output:
            return output.splitlines()[0].split()[0]
        raise LocalGitError(f"Could not resolve {source.full_name}@{source.ref}")

    async def load(
        self,
        source: SourceDefinition,
        *,
        expected_commit: str | None = None,
    ) -> tuple[SourceManifest, list[RawDocument]]:
        return await asyncio.to_thread(self._load_sync, source, expected_commit)

    def _checkout(self, source: SourceDefinition, expected_commit: str | None) -> tuple[Path, str]:
        repo_dir = self._repo_dir(source)
        remote = self._remote_url(source)
        if not (repo_dir / ".git").exists():
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            _run(
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                source.ref,
                remote,
                str(repo_dir),
                timeout=self.git_timeout_seconds,
            )
        else:
            _run("git", "remote", "set-url", "origin", remote, cwd=repo_dir)
            _run(
                "git",
                "fetch",
                "--depth",
                "1",
                "origin",
                source.ref,
                cwd=repo_dir,
                timeout=self.git_timeout_seconds,
            )
            _run("git", "reset", "--hard", "FETCH_HEAD", cwd=repo_dir)
            _run("git", "clean", "-fdx", cwd=repo_dir)

        commit_sha = _run("git", "rev-parse", "HEAD", cwd=repo_dir)
        if expected_commit and commit_sha != expected_commit:
            # A push can happen between ls-remote and clone/fetch. Re-resolve to
            # avoid recording a stale or mismatched snapshot identity.
            remote_now = self._resolve_snapshot_sync(source)
            if commit_sha != remote_now:
                _run(
                    "git",
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    source.ref,
                    cwd=repo_dir,
                    timeout=self.git_timeout_seconds,
                )
                _run("git", "reset", "--hard", "FETCH_HEAD", cwd=repo_dir)
                commit_sha = _run("git", "rev-parse", "HEAD", cwd=repo_dir)
        return repo_dir, commit_sha

    def _load_sync(
        self,
        source: SourceDefinition,
        expected_commit: str | None,
    ) -> tuple[SourceManifest, list[RawDocument]]:
        repo_dir, commit_sha = self._checkout(source, expected_commit)
        raw_index = subprocess.run(
            ["git", "ls-files", "-s", "-z"],
            cwd=repo_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.git_timeout_seconds,
        ).stdout

        files: list[SourceFile] = []
        documents: list[RawDocument] = []
        for raw_entry in raw_index.split(b"\0"):
            if not raw_entry:
                continue
            try:
                metadata, raw_path = raw_entry.split(b"\t", 1)
                mode, blob_sha, _stage = metadata.decode("ascii").split(" ", 2)
                path = raw_path.decode("utf-8", errors="surrogateescape")
            except (ValueError, UnicodeDecodeError):
                continue

            if mode not in {"100644", "100755"} or not _is_selected(path, source):
                continue
            full_path = repo_dir / path
            try:
                size = full_path.stat().st_size
            except OSError:
                continue
            if size > source.max_file_bytes:
                continue

            try:
                raw_bytes = full_path.read_bytes()
                content = _decode_source_text(raw_bytes)
            except (OSError, UnicodeDecodeError):
                # The source registry intentionally targets text/code/config.
                # A binary-looking or legacy file that still cannot decode is
                # skipped rather than poisoning the full overnight bootstrap.
                continue

            content_type = _content_type(path)
            source_file = SourceFile(
                path=path,
                sha=blob_sha,
                size=size,
                content_type=content_type,
            )
            files.append(source_file)
            documents.append(
                RawDocument(
                    document_id=_document_id(source.full_name, commit_sha, path),
                    source_id=source.id,
                    repository=source.full_name,
                    component=source.component,
                    version_ref=source.ref,
                    commit_sha=commit_sha,
                    path=path,
                    blob_sha=blob_sha,
                    content_type=content_type,
                    language=language_for_path(path),
                    content=content.replace("\r\n", "\n").replace("\r", "\n"),
                    content_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    source_url=(
                        f"https://github.com/{source.full_name}/blob/{commit_sha}/"
                        f"{quote(path, safe='/')}"
                    ),
                    size_bytes=len(raw_bytes),
                )
            )

        files.sort(key=lambda item: item.path)
        documents.sort(key=lambda item: item.path)
        manifest = SourceManifest(
            source_id=source.id,
            repository=source.full_name,
            component=source.component,
            requested_ref=source.ref,
            commit_sha=commit_sha,
            archived=False,
            files=files,
            tree_truncated=False,
        )
        return manifest, documents
