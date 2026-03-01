"""Semantic search augmentation for Claude Toolstack.

Optional module — requires ``pip install .[semantic]`` for dependencies.

Provides:
  - Chunking strategies for source files
  - SQLite-backed embedding persistence
  - Pluggable embedder (sentence-transformers default)
  - Pure Python cosine retrieval
  - Indexing pipeline (incremental + rebuild)
"""

from __future__ import annotations

SEMANTIC_SCHEMA_VERSION = 1

# Default knobs (workstation-safe)
DEFAULTS = {
    "chunk_lines": 180,
    "overlap_lines": 30,
    "topk_chunks": 8,
    "max_slices": 4,
    "max_seconds": 4,
    "max_file_bytes": 512 * 1024,  # 512 KB
    "confidence_gate": 0.45,
    "match_gate": 5,
}


def _check_deps() -> None:
    """Verify optional dependencies are installed."""
    missing = []
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")

    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        missing.append("sentence-transformers")

    if missing:
        deps = ", ".join(missing)
        raise ImportError(
            f"Semantic search requires: {deps}. Install with: pip install .[semantic]"
        )
