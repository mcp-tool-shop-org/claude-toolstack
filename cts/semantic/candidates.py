"""Candidate narrowing for semantic search.

Selects which files semantic search should consider, based on
lexical ranking signals.  The goal: search only where lexical
didn't already produce good evidence, reducing both latency
and irrelevant noise.

Strategies:
  - exclude_top_k: drop the top K lexically-ranked files and
    take the next best candidates (default, conservative)
  - none: no narrowing — all files are candidates

All strategies respect max_files and path prefer/avoid filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class CandidateSelection:
    """Result of candidate selection with debug metadata."""

    strategy: str
    allowed_paths: List[str]
    excluded_top_k: int = 0
    candidate_files: int = 0
    excluded_files_sample: List[str] = field(default_factory=list)
    candidate_rules_hit: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "excluded_top_k": self.excluded_top_k,
            "candidate_files": self.candidate_files,
            "excluded_files_sample": self.excluded_files_sample,
            "candidate_rules_hit": self.candidate_rules_hit,
        }


def _extract_paths(
    ranked_sources: List[Dict[str, Any]],
) -> List[str]:
    """Extract unique file paths from ranked sources in order."""
    seen: Set[str] = set()
    paths: List[str] = []
    for src in ranked_sources:
        p = src.get("path", "")
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


def _apply_path_filters(
    paths: List[str],
    *,
    prefer_paths: Optional[List[str]] = None,
    avoid_paths: Optional[List[str]] = None,
    rules_hit: List[str],
) -> List[str]:
    """Filter paths by prefer/avoid patterns.

    - prefer_paths: if set, only include paths containing at least
      one preferred prefix (e.g. "src/", "app/")
    - avoid_paths: exclude paths containing any avoided prefix
      (e.g. "vendor/", "test/")
    """
    result = paths

    if avoid_paths:
        before = len(result)
        result = [
            p
            for p in result
            if not any(avoid in p for avoid in avoid_paths)
        ]
        if len(result) < before:
            rules_hit.append(f"avoid_paths removed {before - len(result)}")

    if prefer_paths:
        preferred = [
            p
            for p in result
            if any(pref in p for pref in prefer_paths)
        ]
        if preferred:
            rules_hit.append(
                f"prefer_paths kept {len(preferred)} of {len(result)}"
            )
            result = preferred

    return result


def select_candidates(
    ranked_sources: List[Dict[str, Any]],
    *,
    strategy: str = "exclude_top_k",
    exclude_top_k: int = 10,
    max_files: int = 200,
    prefer_paths: Optional[List[str]] = None,
    avoid_paths: Optional[List[str]] = None,
) -> CandidateSelection:
    """Select candidate files for semantic search.

    Args:
        ranked_sources: Lexically-ranked source list from the bundle.
        strategy: Selection strategy ("exclude_top_k" or "none").
        exclude_top_k: Number of top-ranked files to exclude.
        max_files: Maximum candidate files to return.
        prefer_paths: Path prefixes to prefer (e.g. ["src/", "app/"]).
        avoid_paths: Path prefixes to avoid (e.g. ["vendor/", "test/"]).

    Returns:
        CandidateSelection with allowed_paths and debug metadata.
    """
    if strategy == "none":
        return CandidateSelection(
            strategy="none",
            allowed_paths=[],  # empty = "search everything"
            candidate_files=0,
            candidate_rules_hit=["no narrowing"],
        )

    # Default: exclude_top_k
    all_paths = _extract_paths(ranked_sources)
    rules_hit: List[str] = []

    # Split into excluded top-K and remaining candidates
    k = min(exclude_top_k, len(all_paths))
    excluded = all_paths[:k]
    candidates = all_paths[k:]

    if k > 0:
        rules_hit.append(f"excluded top {k} lexical files")

    # Apply path filters
    candidates = _apply_path_filters(
        candidates,
        prefer_paths=prefer_paths,
        avoid_paths=avoid_paths,
        rules_hit=rules_hit,
    )

    # Cap at max_files
    if len(candidates) > max_files:
        rules_hit.append(f"capped at {max_files} files")
        candidates = candidates[:max_files]

    return CandidateSelection(
        strategy="exclude_top_k",
        allowed_paths=candidates,
        excluded_top_k=k,
        candidate_files=len(candidates),
        excluded_files_sample=excluded[:10],
        candidate_rules_hit=rules_hit,
    )
