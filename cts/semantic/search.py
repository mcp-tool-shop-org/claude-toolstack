"""Pure Python cosine similarity search.

No FAISS, no external index — just numpy dot products.
Designed for workstation-scale corpora (<50K chunks).
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import List, Tuple


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
