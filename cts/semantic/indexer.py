"""Indexing pipeline: chunk → embed → store.

Supports incremental updates (skip unchanged content) and
full rebuild. Progress reporting via callbacks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from cts.semantic.chunker import Chunk, chunk_directory
from cts.semantic.config import SemanticConfig
from cts.semantic.store import SemanticStore


@dataclass
class IndexResult:
    """Summary of an indexing operation."""

    files_scanned: int = 0
    chunks_total: int = 0
    chunks_new: int = 0
    chunks_embedded: int = 0
    chunks_skipped: int = 0
    elapsed_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "chunks_total": self.chunks_total,
            "chunks_new": self.chunks_new,
            "chunks_embedded": self.chunks_embedded,
            "chunks_skipped": self.chunks_skipped,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "errors": self.errors,
        }


def index_repo(
    root: str,
    repo: str,
    store: SemanticStore,
    embedder: Any,
    config: Optional[SemanticConfig] = None,
    *,
    max_files: int = 0,
    max_chunks: int = 0,
    max_seconds: float = 0,
    batch_size: int = 32,
    progress_fn: Optional[Callable[[str, int, int], None]] = None,
) -> IndexResult:
    """Index a repository directory into the semantic store.

    Args:
        root: Repository root directory.
        repo: Repository identifier (e.g., "org/repo").
        store: SemanticStore instance.
        embedder: Embedder instance (must have embed_texts and dim).
        config: SemanticConfig (uses defaults if None).
        max_files: Limit files to process (0 = unlimited).
        max_chunks: Limit total chunks to embed (0 = unlimited).
        max_seconds: Time budget (0 = unlimited).
        batch_size: Embedding batch size.
        progress_fn: Optional callback(stage, current, total).

    Returns:
        IndexResult with summary stats.
    """
    if config is None:
        from cts.semantic.config import load_config

        config = load_config()

    start_time = time.time()
    result = IndexResult()

    # Step 1: Chunk the directory
    if progress_fn:
        progress_fn("chunking", 0, 0)

    chunks = chunk_directory(
        root,
        repo,
        chunk_lines=config.chunk_lines,
        overlap_lines=config.overlap_lines,
        max_file_bytes=config.max_file_bytes,
        skip_patterns=config.skip_patterns,
        max_files=max_files,
    )

    result.chunks_total = len(chunks)
    result.files_scanned = len({c.path for c in chunks})

    if progress_fn:
        progress_fn("chunking_done", result.files_scanned, result.chunks_total)

    # Step 2: Upsert chunks to store (detect changed content)
    new_count = store.upsert_chunks(chunks)
    result.chunks_new = new_count

    # Step 3: Find chunks needing embeddings
    need_embedding = store.get_chunks_without_embeddings()
    if max_chunks and len(need_embedding) > max_chunks:
        need_embedding = need_embedding[:max_chunks]

    # Build a lookup for chunk content
    chunk_map: Dict[str, Chunk] = {c.chunk_id: c for c in chunks}

    # Step 4: Embed in batches
    embedded = 0
    batch_ids: List[str] = []
    batch_texts: List[str] = []

    for cid in need_embedding:
        # Time budget check
        if max_seconds and (time.time() - start_time) > max_seconds:
            break

        chunk = chunk_map.get(cid)
        if chunk is None:
            result.chunks_skipped += 1
            continue

        batch_ids.append(cid)
        batch_texts.append(chunk.content)

        if len(batch_texts) >= batch_size:
            try:
                vecs = embedder.embed_texts(batch_texts)
                store.store_embeddings(
                    batch_ids, vecs, embedder.model_name, embedder.dim
                )
                embedded += len(vecs)
            except Exception as exc:
                result.errors.append(f"Embedding batch failed: {exc}")

            if progress_fn:
                progress_fn("embedding", embedded, len(need_embedding))

            batch_ids = []
            batch_texts = []

    # Final batch
    if batch_texts:
        try:
            vecs = embedder.embed_texts(batch_texts)
            store.store_embeddings(batch_ids, vecs, embedder.model_name, embedder.dim)
            embedded += len(vecs)
        except Exception as exc:
            result.errors.append(f"Embedding batch failed: {exc}")

    result.chunks_embedded = embedded
    result.chunks_skipped = len(need_embedding) - embedded
    result.elapsed_seconds = time.time() - start_time

    return result
