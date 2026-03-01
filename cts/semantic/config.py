"""Configuration for semantic search.

Reads from environment variables with sane defaults.
All settings are workstation-safe out of the box.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from cts.semantic import DEFAULTS


@dataclass
class SemanticConfig:
    """Semantic search configuration.

    Values are read from environment or overridden programmatically.
    """

    # Chunking
    chunk_lines: int = DEFAULTS["chunk_lines"]
    overlap_lines: int = DEFAULTS["overlap_lines"]
    max_file_bytes: int = DEFAULTS["max_file_bytes"]

    # Retrieval
    topk_chunks: int = DEFAULTS["topk_chunks"]
    max_slices: int = DEFAULTS["max_slices"]
    max_seconds: float = DEFAULTS["max_seconds"]

    # Autopilot gates
    confidence_gate: float = DEFAULTS["confidence_gate"]
    match_gate: int = DEFAULTS["match_gate"]

    # Embedder
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: str = "auto"  # auto | cpu | cuda

    # Storage
    store_dir: str = ""  # default: gw-cache/<repo>/semantic.sqlite3

    # Skip patterns
    skip_patterns: List[str] = field(
        default_factory=lambda: [
            "vendor/",
            "node_modules/",
            ".git/",
            "__pycache__/",
            "dist/",
            "build/",
            ".venv/",
        ]
    )


def load_config(**overrides: object) -> SemanticConfig:
    """Load semantic config from environment + overrides.

    Environment variables (all optional):
      CTS_SEMANTIC_CHUNK_LINES
      CTS_SEMANTIC_OVERLAP_LINES
      CTS_SEMANTIC_MAX_FILE_BYTES
      CTS_SEMANTIC_TOPK
      CTS_SEMANTIC_MAX_SLICES
      CTS_SEMANTIC_MAX_SECONDS
      CTS_SEMANTIC_CONFIDENCE_GATE
      CTS_SEMANTIC_MATCH_GATE
      CTS_SEMANTIC_MODEL
      CTS_SEMANTIC_DEVICE
      CTS_SEMANTIC_STORE_DIR
    """
    cfg = SemanticConfig()

    env_map = {
        "CTS_SEMANTIC_CHUNK_LINES": ("chunk_lines", int),
        "CTS_SEMANTIC_OVERLAP_LINES": ("overlap_lines", int),
        "CTS_SEMANTIC_MAX_FILE_BYTES": ("max_file_bytes", int),
        "CTS_SEMANTIC_TOPK": ("topk_chunks", int),
        "CTS_SEMANTIC_MAX_SLICES": ("max_slices", int),
        "CTS_SEMANTIC_MAX_SECONDS": ("max_seconds", float),
        "CTS_SEMANTIC_CONFIDENCE_GATE": ("confidence_gate", float),
        "CTS_SEMANTIC_MATCH_GATE": ("match_gate", int),
        "CTS_SEMANTIC_MODEL": ("model_name", str),
        "CTS_SEMANTIC_DEVICE": ("device", str),
        "CTS_SEMANTIC_STORE_DIR": ("store_dir", str),
    }

    for env_var, (attr, conv) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            setattr(cfg, attr, conv(val))

    # Apply explicit overrides last
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    return cfg
