"""Source file chunking for semantic indexing.

Chunks source files by line count with configurable overlap.
Produces stable chunk IDs for incremental updates.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional

from cts.semantic import DEFAULTS


@dataclass
class Chunk:
    """One chunk of a source file."""

    chunk_id: str
    repo: str
    path: str
    start_line: int
    end_line: int
    content: str
    content_hash: str

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line


def _content_hash(text: str) -> str:
    """SHA-256 hex digest of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_id(repo: str, path: str, start: int, end: int, chash: str) -> str:
    """Stable chunk ID = sha256(repo + path + start + end + content_hash)."""
    key = f"{repo}:{path}:{start}:{end}:{chash}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _is_minified(lines: List[str], threshold: int = 500) -> bool:
    """Heuristic: file is minified if any line exceeds threshold chars."""
    return any(len(line) > threshold for line in lines[:20])


def _should_skip(
    path: str,
    skip_patterns: Optional[List[str]] = None,
) -> bool:
    """Check if a file path matches skip patterns."""
    if skip_patterns is None:
        return False
    normalized = path.replace("\\", "/")
    return any(pat in normalized for pat in skip_patterns)


def chunk_file(
    content: str,
    repo: str,
    path: str,
    *,
    chunk_lines: int = DEFAULTS["chunk_lines"],
    overlap_lines: int = DEFAULTS["overlap_lines"],
) -> List[Chunk]:
    """Chunk file content into overlapping segments.

    Args:
        content: Full file content as string.
        repo: Repository identifier (e.g., "org/repo").
        path: File path relative to repo root.
        chunk_lines: Lines per chunk.
        overlap_lines: Lines of overlap between consecutive chunks.

    Returns:
        List of Chunk objects with stable IDs.
    """
    if not content:
        return []

    lines = content.split("\n")
    total = len(lines)

    chunks: List[Chunk] = []
    start = 0
    step = max(1, chunk_lines - overlap_lines)

    while start < total:
        end = min(start + chunk_lines, total)
        chunk_content = "\n".join(lines[start:end])
        chash = _content_hash(chunk_content)
        cid = _chunk_id(repo, path, start, end, chash)

        chunks.append(
            Chunk(
                chunk_id=cid,
                repo=repo,
                path=path,
                start_line=start,
                end_line=end,
                content=chunk_content,
                content_hash=chash,
            )
        )

        if end >= total:
            break
        start += step

    return chunks


def chunk_directory(
    root: str,
    repo: str,
    *,
    chunk_lines: int = DEFAULTS["chunk_lines"],
    overlap_lines: int = DEFAULTS["overlap_lines"],
    max_file_bytes: int = DEFAULTS["max_file_bytes"],
    skip_patterns: Optional[List[str]] = None,
    max_files: int = 0,
) -> List[Chunk]:
    """Chunk all eligible files in a directory tree.

    Args:
        root: Directory root to scan.
        repo: Repository identifier.
        chunk_lines: Lines per chunk.
        overlap_lines: Lines of overlap.
        max_file_bytes: Skip files larger than this.
        skip_patterns: Path patterns to skip.
        max_files: Max files to process (0 = unlimited).

    Returns:
        List of all chunks across all files.
    """
    all_chunks: List[Chunk] = []
    file_count = 0

    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace("\\", "/")

            if _should_skip(rel, skip_patterns):
                continue

            # Skip binary / oversized
            try:
                size = os.path.getsize(full)
            except OSError:
                continue

            if size > max_file_bytes or size == 0:
                continue

            # Read and check for minification
            try:
                with open(full, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            lines = content.split("\n")
            if _is_minified(lines):
                continue

            chunks = chunk_file(
                content,
                repo,
                rel,
                chunk_lines=chunk_lines,
                overlap_lines=overlap_lines,
            )
            all_chunks.extend(chunks)

            file_count += 1
            if max_files and file_count >= max_files:
                return all_chunks

    return all_chunks
