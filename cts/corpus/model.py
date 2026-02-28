"""Dataclasses for corpus metrics extracted from sidecar artifacts.

Each ingested sidecar produces one :class:`CorpusRecord` and,
optionally, one :class:`PassRecord` per refinement pass.  Both
carry a ``to_dict()`` method for JSONL serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CorpusRecord:
    """One record per ingested sidecar artifact."""

    # Identity
    schema_version: int = 0
    repo: str = ""
    mode: str = ""
    created_at: float = 0.0
    request_id: str = ""
    source_path: str = ""

    # Pass metrics
    passes_count: int = 0
    confidence_pass1: Optional[float] = None
    confidence_final: Optional[float] = None
    confidence_delta: Optional[float] = None

    # Actions across all passes (ordered)
    actions: List[Dict[str, Any]] = field(default_factory=list)

    # Size metrics (bytes)
    bundle_bytes_final: int = 0
    section_bytes: Dict[str, int] = field(default_factory=dict)

    # Truncation
    truncation_flags: Dict[str, bool] = field(default_factory=dict)

    # Timings (from _debug)
    timings_ms: Dict[str, float] = field(default_factory=dict)

    # Quality metadata
    missing_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dict for JSONL output."""
        return {
            "schema_version": self.schema_version,
            "repo": self.repo,
            "mode": self.mode,
            "created_at": self.created_at,
            "request_id": self.request_id,
            "source_path": self.source_path,
            "passes_count": self.passes_count,
            "confidence_pass1": self.confidence_pass1,
            "confidence_final": self.confidence_final,
            "confidence_delta": self.confidence_delta,
            "actions": self.actions,
            "bundle_bytes_final": self.bundle_bytes_final,
            "section_bytes": self.section_bytes,
            "truncation_flags": self.truncation_flags,
            "timings_ms": self.timings_ms,
            "missing_fields": self.missing_fields,
        }


@dataclass
class PassRecord:
    """One record per refinement pass within a sidecar artifact."""

    request_id: str = ""
    pass_index: int = 0
    confidence: Optional[float] = None
    actions_this_pass: List[str] = field(default_factory=list)
    action_details: List[Dict[str, Any]] = field(default_factory=list)
    status: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dict for JSONL output."""
        return {
            "request_id": self.request_id,
            "pass_index": self.pass_index,
            "confidence": self.confidence,
            "actions_this_pass": self.actions_this_pass,
            "action_details": self.action_details,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
        }
