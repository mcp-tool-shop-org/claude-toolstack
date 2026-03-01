"""Capture lexical-only KPI baseline snapshots.

Produces a frozen baseline artifact recording the current state of
corpus KPIs.  Used as the control arm for A/B experiments (e.g.
lexical vs lexical+semantic).

Output shape:
  {
    "baseline_version": 1,
    "label": "lexical-only",
    "created_at": <epoch>,
    "corpus_hash": <sha256 of JSONL>,
    "corpus_records": <int>,
    "tool_versions": { "cts": "...", "bundle_schema": ..., ... },
    "filters": { "mode": ..., "repo": ..., "since_days": ... },
    "kpis": { <extract_kpis output> },
    "distributions": { "confidence_final": {...}, "bundle_bytes": {...} }
  }
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Dict, List, Optional

from cts.corpus.evaluate import extract_kpis
from cts.corpus.report import load_corpus

BASELINE_VERSION = 1


# ---------------------------------------------------------------------------
# Stats helpers (lightweight, no numpy)
# ---------------------------------------------------------------------------


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (p / 100.0) * (len(s) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Distribution snapshots
# ---------------------------------------------------------------------------


def _distribution(values: List[float]) -> Dict[str, float]:
    """Compute p10/p25/p50/p75/p90/p99/mean for a list of values."""
    if not values:
        return {}
    return {
        "count": len(values),
        "mean": round(_mean(values), 4),
        "p10": round(_percentile(values, 10), 4),
        "p25": round(_percentile(values, 25), 4),
        "p50": round(_percentile(values, 50), 4),
        "p75": round(_percentile(values, 75), 4),
        "p90": round(_percentile(values, 90), 4),
        "p99": round(_percentile(values, 99), 4),
    }


# ---------------------------------------------------------------------------
# Corpus hashing
# ---------------------------------------------------------------------------


def _hash_file(path: str) -> str:
    """SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_baseline(
    corpus_path: str,
    *,
    label: str = "lexical-only",
    mode_filter: Optional[str] = None,
    repo_filter: Optional[str] = None,
    since_days: Optional[float] = None,
) -> Dict[str, Any]:
    """Capture a KPI baseline snapshot from a corpus JSONL file.

    Args:
        corpus_path: Path to corpus JSONL.
        label: Human-readable label for this baseline.
        mode_filter: Only include records with this mode.
        repo_filter: Only include records from this repo.
        since_days: Only include records from last N days.

    Returns:
        Baseline snapshot dict, ready for JSON serialization.
    """
    from cts import __version__
    from cts.schema import BUNDLE_SCHEMA_VERSION
    from cts.corpus.experiment_schema import EXPERIMENT_SCHEMA_VERSION
    from cts.corpus.tuning_schema import TUNING_SCHEMA_VERSION

    records = load_corpus(corpus_path)

    # Apply filters
    if mode_filter:
        records = [r for r in records if r.get("mode") == mode_filter]
    if repo_filter:
        records = [r for r in records if r.get("repo") == repo_filter]
    if since_days is not None:
        cutoff = time.time() - (since_days * 86400)
        records = [r for r in records if r.get("created_at", 0) >= cutoff]

    # Extract KPIs (reuse evaluate.py logic)
    kpis = extract_kpis(records)

    # Compute distributions for key metrics
    confidence_finals = [
        r["confidence_final"] for r in records if r.get("confidence_final") is not None
    ]
    confidence_deltas = [
        r["confidence_delta"] for r in records if r.get("confidence_delta") is not None
    ]
    bundle_sizes = [
        float(r["bundle_bytes_final"])
        for r in records
        if r.get("bundle_bytes_final") is not None
    ]

    distributions = {
        "confidence_final": _distribution(confidence_finals),
        "confidence_delta": _distribution(confidence_deltas),
        "bundle_bytes": _distribution(bundle_sizes),
    }

    # Build filter record
    filters: Dict[str, Any] = {}
    if mode_filter:
        filters["mode"] = mode_filter
    if repo_filter:
        filters["repo"] = repo_filter
    if since_days is not None:
        filters["since_days"] = since_days

    return {
        "baseline_version": BASELINE_VERSION,
        "label": label,
        "created_at": time.time(),
        "corpus_hash": _hash_file(corpus_path),
        "corpus_records": len(records),
        "tool_versions": {
            "cts": __version__,
            "bundle_schema": BUNDLE_SCHEMA_VERSION,
            "experiment_schema": EXPERIMENT_SCHEMA_VERSION,
            "tuning_schema": TUNING_SCHEMA_VERSION,
        },
        "filters": filters,
        "kpis": kpis,
        "distributions": distributions,
    }


def render_baseline_text(baseline: Dict[str, Any]) -> str:
    """Render a baseline snapshot as plain text."""
    lines: List[str] = []
    lines.append("=" * 50)
    lines.append("BASELINE SNAPSHOT")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Label:    {baseline.get('label', '?')}")
    lines.append(f"Records:  {baseline.get('corpus_records', 0)}")
    lines.append(f"Hash:     {baseline.get('corpus_hash', '?')[:16]}...")
    lines.append("")

    kpis = baseline.get("kpis", {})
    if kpis:
        lines.append("KPIs:")
        for key in sorted(kpis):
            if key == "total":
                continue
            val = kpis[key]
            if isinstance(val, float):
                lines.append(f"  {key:<30} {val:.4f}")
            else:
                lines.append(f"  {key:<30} {val}")
        lines.append("")

    dists = baseline.get("distributions", {})
    if dists:
        lines.append("Distributions:")
        for dist_name, dist in dists.items():
            if not dist:
                continue
            p50 = dist.get("p50", 0)
            p90 = dist.get("p90", 0)
            count = dist.get("count", 0)
            lines.append(f"  {dist_name:<25} p50={p50:.4f} p90={p90:.4f} n={count}")

    lines.append("")
    return "\n".join(lines)


def render_baseline_json(baseline: Dict[str, Any]) -> str:
    """Render a baseline snapshot as JSON."""
    return json.dumps(baseline, indent=2, default=str)


def render_baseline_markdown(baseline: Dict[str, Any]) -> str:
    """Render a baseline snapshot as markdown."""
    lines: List[str] = []
    lines.append("# Baseline Snapshot")
    lines.append("")
    lines.append(f"**Label:** {baseline.get('label', '?')}")
    lines.append(f"**Records:** {baseline.get('corpus_records', 0)}")
    lines.append(f"**Corpus hash:** `{baseline.get('corpus_hash', '?')[:16]}...`")
    lines.append("")

    # Tool versions
    tv = baseline.get("tool_versions", {})
    if tv:
        lines.append("## Tool versions")
        lines.append("")
        for k, v in sorted(tv.items()):
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    # Filters
    filters = baseline.get("filters", {})
    if filters:
        lines.append("## Filters applied")
        lines.append("")
        for k, v in sorted(filters.items()):
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    # KPIs table
    kpis = baseline.get("kpis", {})
    if kpis:
        lines.append("## KPIs")
        lines.append("")
        lines.append("| KPI | Value |")
        lines.append("|-----|------:|")
        for key in sorted(kpis):
            if key == "total":
                continue
            val = kpis[key]
            if isinstance(val, float):
                lines.append(f"| {key} | {val:.4f} |")
            else:
                lines.append(f"| {key} | {val} |")
        lines.append("")

    # Distributions table
    dists = baseline.get("distributions", {})
    if dists:
        lines.append("## Distributions")
        lines.append("")
        lines.append("| Metric | p10 | p25 | p50 | p75 | p90 | p99 |")
        lines.append("|--------|----:|----:|----:|----:|----:|----:|")
        for name, dist in sorted(dists.items()):
            if not dist:
                continue
            lines.append(
                f"| {name} "
                f"| {dist.get('p10', 0):.4f} "
                f"| {dist.get('p25', 0):.4f} "
                f"| {dist.get('p50', 0):.4f} "
                f"| {dist.get('p75', 0):.4f} "
                f"| {dist.get('p90', 0):.4f} "
                f"| {dist.get('p99', 0):.4f} |"
            )
        lines.append("")

    return "\n".join(lines)
