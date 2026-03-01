"""Pure Python cosine similarity search.

No FAISS, no external index — just numpy dot products.
Designed for workstation-scale corpora (<50K chunks).

Candidate narrowing (Phase 4.2):
  narrowed_search() integrates candidate selection, filtered
  store retrieval, and fallback logic into a single call.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SearchHit:
    """One semantic search result."""

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    score: float


def _deserialize_vec(blob: bytes, dim: int) -> List[float]:
    """Deserialize a float32 blob to a list of floats."""
    return list(struct.unpack(f"{dim}f", blob))


def _dot(a: List[float], b: List[float]) -> float:
    """Dot product of two vectors."""
    return sum(x * y for x, y in zip(a, b))


def _normalize(vec: List[float]) -> List[float]:
    """L2-normalize a vector."""
    mag = sum(x * x for x in vec) ** 0.5
    if mag == 0:
        return vec
    return [x / mag for x in vec]


def cosine_search(
    query_vec: bytes,
    candidates: List[Tuple[str, str, int, int, bytes]],
    dim: int,
    *,
    topk: int = 8,
    max_seconds: float = 4.0,
) -> List[SearchHit]:
    """Search candidates by cosine similarity.

    Args:
        query_vec: Serialized float32 query embedding.
        candidates: List of (chunk_id, path, start_line, end_line, vec_blob).
        dim: Embedding dimension.
        topk: Number of top results to return.
        max_seconds: Time budget for search.

    Returns:
        Top-K search hits sorted by score descending.
    """
    if not candidates:
        return []

    start_time = time.time()
    q = _normalize(_deserialize_vec(query_vec, dim))

    scored: List[SearchHit] = []
    for chunk_id, path, start_line, end_line, vec_blob in candidates:
        # Time budget check
        if time.time() - start_time > max_seconds:
            break

        c = _deserialize_vec(vec_blob, dim)
        score = _dot(q, c)

        scored.append(
            SearchHit(
                chunk_id=chunk_id,
                path=path,
                start_line=start_line,
                end_line=end_line,
                score=score,
            )
        )

    # Sort by score descending
    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:topk]


def cosine_search_numpy(
    query_vec: bytes,
    candidates: List[Tuple[str, str, int, int, bytes]],
    dim: int,
    *,
    topk: int = 8,
) -> List[SearchHit]:
    """Vectorized cosine search using numpy (faster for large corpora).

    Falls back to pure-python cosine_search if numpy unavailable.
    """
    try:
        import numpy as np
    except ImportError:
        return cosine_search(query_vec, candidates, dim, topk=topk)

    if not candidates:
        return []

    # Parse query
    q = np.frombuffer(query_vec, dtype=np.float32).copy()
    q_norm = np.linalg.norm(q)
    if q_norm > 0:
        q = q / q_norm

    # Build candidate matrix
    ids: List[str] = []
    paths: List[str] = []
    starts: List[int] = []
    ends: List[int] = []

    vecs = []
    for chunk_id, path, start_line, end_line, vec_blob in candidates:
        ids.append(chunk_id)
        paths.append(path)
        starts.append(start_line)
        ends.append(end_line)
        vecs.append(np.frombuffer(vec_blob, dtype=np.float32).copy())

    mat = np.stack(vecs)  # (N, dim)
    scores = mat @ q  # (N,) cosine similarities (vectors already normalized)

    # Top-K
    k = min(topk, len(scores))
    top_indices = np.argsort(scores)[-k:][::-1]

    return [
        SearchHit(
            chunk_id=ids[i],
            path=paths[i],
            start_line=starts[i],
            end_line=ends[i],
            score=float(scores[i]),
        )
        for i in top_indices
    ]


# ---------------------------------------------------------------------------
# Narrowed search (Phase 4.2)
# ---------------------------------------------------------------------------


@dataclass
class NarrowedSearchResult:
    """Result of a narrowed semantic search with full debug metadata."""

    hits: List[SearchHit]
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": [
                {
                    "chunk_id": h.chunk_id,
                    "path": h.path,
                    "start_line": h.start_line,
                    "end_line": h.end_line,
                    "score": h.score,
                }
                for h in self.hits
            ],
            "debug": self.debug,
        }


def narrowed_search(
    query_vec: bytes,
    store: Any,  # SemanticStore — avoid circular import
    dim: int,
    *,
    allowed_paths: Optional[List[str]] = None,
    max_chunks: int = 0,
    topk: int = 8,
    max_seconds: float = 4.0,
    fallback: str = "global_tight",
    fallback_topk: int = 5,
    candidate_debug: Optional[Dict[str, Any]] = None,
) -> NarrowedSearchResult:
    """Search with candidate narrowing and fallback.

    Args:
        query_vec: Serialized float32 query embedding.
        store: SemanticStore instance.
        dim: Embedding dimension.
        allowed_paths: File paths to search (empty/None = all).
        max_chunks: Maximum candidate chunks to load (0 = unlimited).
        topk: Number of top results to return.
        max_seconds: Time budget for search.
        fallback: What to do when candidate pool is empty
            ("global_tight" = search all with tighter topk, "skip" = no results).
        fallback_topk: topK to use for global_tight fallback.
        candidate_debug: Debug metadata from candidate selector.

    Returns:
        NarrowedSearchResult with hits and debug metadata.
    """
    debug: Dict[str, Any] = {}
    if candidate_debug:
        debug["candidate_selection"] = candidate_debug

    start_time = time.time()

    # Load embeddings with optional path filter
    paths = allowed_paths or []
    candidates, capped = store.get_embeddings_filtered(paths, max_chunks=max_chunks)

    debug["candidate_chunks_considered"] = len(candidates)
    debug["candidate_chunks_capped"] = capped
    debug["fallback_used"] = False

    # Check if candidate pool is empty
    if not candidates and paths:
        # No candidates from narrowing — apply fallback
        if fallback == "global_tight":
            debug["fallback_used"] = True
            debug["fallback_strategy"] = "global_tight"
            debug["fallback_topk"] = fallback_topk
            candidates, capped = store.get_embeddings_filtered(
                [], max_chunks=max_chunks
            )
            debug["fallback_chunks_loaded"] = len(candidates)
            topk = fallback_topk
        else:
            # fallback == "skip"
            debug["fallback_used"] = True
            debug["fallback_strategy"] = "skip"
            return NarrowedSearchResult(hits=[], debug=debug)

    # Run search
    hits = cosine_search_numpy(query_vec, candidates, dim, topk=topk)

    debug["search_time_ms"] = round((time.time() - start_time) * 1000, 1)

    return NarrowedSearchResult(hits=hits, debug=debug)
