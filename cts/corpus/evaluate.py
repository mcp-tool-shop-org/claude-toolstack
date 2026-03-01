"""Evaluation engine: compare before vs after corpora.

Given a baseline corpus (before tuning) and an updated corpus (after
tuning), compute KPI deltas that prove whether the changes improved
things.

KPIs tracked:
  - confidence_final_mean: average final confidence
  - confidence_delta_mean: average autopilot lift
  - truncation_rate: fraction of truncated artifacts
  - autopilot_low_lift_rate: fraction of low-lift autopilot runs
  - bundle_bytes_p90: 90th percentile bundle size
  - should_autopilot_count: artifacts that should have had autopilot

Verdict logic:
  - ``improved``: majority of tracked KPIs got better
  - ``regressed``: majority got worse
  - ``mixed``: some better, some worse
  - ``no_data``: insufficient data for comparison
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# KPI extraction (from corpus records directly)
# ---------------------------------------------------------------------------


def _percentile(values: List[float], p: float) -> float:
    """Compute percentile (same as in report.py)."""
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


def extract_kpis(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract KPI values from corpus records.

    Returns a dict of KPI name → value, suitable for comparison.
    """
    total = len(records)
    if total == 0:
        return {"total": 0}

    # Confidence
    finals = [
        r["confidence_final"] for r in records if r.get("confidence_final") is not None
    ]
    deltas = [
        r["confidence_delta"] for r in records if r.get("confidence_delta") is not None
    ]

    # Truncation
    truncated = sum(
        1 for r in records if r.get("truncation_flags", {}).get("truncated", False)
    )

    # Autopilot waste
    autopilot_runs = [r for r in records if r.get("passes_count", 0) > 0]
    low_lift = [
        r
        for r in autopilot_runs
        if r.get("confidence_delta") is not None and r["confidence_delta"] < 0.05
    ]

    # Should-have-autopiloted
    should_auto = [
        r
        for r in records
        if r.get("passes_count", 0) == 0
        and r.get("confidence_pass1") is not None
        and r["confidence_pass1"] < 0.6
    ]

    # Bundle sizes
    sizes = [
        r["bundle_bytes_final"]
        for r in records
        if r.get("bundle_bytes_final") is not None
    ]

    autopilot_count = len(autopilot_runs)

    # Semantic augmentation metrics (Phase 4)
    semantic_runs = [r for r in records if r.get("semantic_invoked", False)]
    semantic_action_runs = [
        r for r in records if r.get("semantic_action_fired", False)
    ]
    semantic_lifts = [
        r["semantic_lift"]
        for r in records
        if r.get("semantic_lift") is not None
    ]

    return {
        "total": total,
        "confidence_final_mean": round(_mean(finals), 4),
        "confidence_delta_mean": round(_mean(deltas), 4),
        "truncation_rate": round(truncated / total, 4) if total else 0.0,
        "autopilot_low_lift_rate": (
            round(len(low_lift) / autopilot_count, 4) if autopilot_count else 0.0
        ),
        "bundle_bytes_p90": round(_percentile(sizes, 90), 0),
        "should_autopilot_count": len(should_auto),
        "autopilot_runs": autopilot_count,
        "truncated_count": truncated,
        # Semantic KPIs
        "semantic_invoked_rate": (
            round(len(semantic_runs) / total, 4) if total else 0.0
        ),
        "semantic_action_rate": (
            round(len(semantic_action_runs) / total, 4) if total else 0.0
        ),
        "semantic_lift_mean": round(_mean(semantic_lifts), 4),
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

# KPIs where higher is better
_HIGHER_BETTER = {
    "confidence_final_mean",
    "confidence_delta_mean",
    "semantic_lift_mean",
}

# KPIs where lower is better
_LOWER_BETTER = {
    "truncation_rate",
    "autopilot_low_lift_rate",
    "bundle_bytes_p90",
    "should_autopilot_count",
}

# Informational KPIs (tracked but not used for verdict direction)
_INFORMATIONAL = {
    "semantic_invoked_rate",
    "semantic_action_rate",
}

# Minimum absolute change to count as a real delta (avoid noise)
_NOISE_THRESHOLDS: Dict[str, float] = {
    "confidence_final_mean": 0.005,
    "confidence_delta_mean": 0.005,
    "truncation_rate": 0.01,
    "autopilot_low_lift_rate": 0.02,
    "bundle_bytes_p90": 1024,
    "should_autopilot_count": 1,
    "semantic_lift_mean": 0.01,
}


def compare_kpis(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare two KPI snapshots and produce a verdict.

    Returns:
        comparison dict with per-KPI deltas, directions, and overall verdict.
    """
    if before.get("total", 0) == 0 or after.get("total", 0) == 0:
        return {
            "verdict": "no_data",
            "reason": "Insufficient data in one or both corpora",
            "kpis": {},
        }

    kpi_results: Dict[str, Dict[str, Any]] = {}
    improved_count = 0
    regressed_count = 0
    tracked_count = 0

    all_kpis = _HIGHER_BETTER | _LOWER_BETTER
    for kpi_name in sorted(all_kpis):
        before_val = before.get(kpi_name)
        after_val = after.get(kpi_name)

        if before_val is None or after_val is None:
            continue

        tracked_count += 1
        delta = after_val - before_val
        threshold = _NOISE_THRESHOLDS.get(kpi_name, 0.001)
        abs_delta = abs(delta)

        # Direction
        if abs_delta < threshold:
            direction = "unchanged"
        elif kpi_name in _HIGHER_BETTER:
            direction = "improved" if delta > 0 else "regressed"
        else:
            direction = "improved" if delta < 0 else "regressed"

        if direction == "improved":
            improved_count += 1
        elif direction == "regressed":
            regressed_count += 1

        kpi_results[kpi_name] = {
            "before": before_val,
            "after": after_val,
            "delta": round(delta, 4),
            "direction": direction,
        }

    # Verdict
    if tracked_count == 0:
        verdict = "no_data"
        reason = "No comparable KPIs found"
    elif improved_count > regressed_count:
        verdict = "improved"
        reason = (
            f"{improved_count}/{tracked_count} KPIs improved, "
            f"{regressed_count} regressed"
        )
    elif regressed_count > improved_count:
        verdict = "regressed"
        reason = (
            f"{regressed_count}/{tracked_count} KPIs regressed, "
            f"{improved_count} improved"
        )
    elif improved_count > 0:
        verdict = "mixed"
        reason = (
            f"{improved_count} improved, {regressed_count} regressed "
            f"out of {tracked_count}"
        )
    else:
        verdict = "unchanged"
        reason = f"No significant changes across {tracked_count} KPIs"

    return {
        "verdict": verdict,
        "reason": reason,
        "improved_count": improved_count,
        "regressed_count": regressed_count,
        "tracked_count": tracked_count,
        "kpis": kpi_results,
    }


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------


def evaluate(
    before_records: List[Dict[str, Any]],
    after_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run full evaluation comparing before and after corpora.

    Args:
        before_records: Corpus records before tuning.
        after_records: Corpus records after tuning.

    Returns:
        Evaluation report dict.
    """
    before_kpis = extract_kpis(before_records)
    after_kpis = extract_kpis(after_records)
    comparison = compare_kpis(before_kpis, after_kpis)

    return {
        "before": before_kpis,
        "after": after_kpis,
        "comparison": comparison,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_evaluation_text(result: Dict[str, Any]) -> str:
    """Render evaluation result as human-readable text."""
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("TUNING EVALUATION REPORT")
    lines.append("=" * 60)

    comparison = result.get("comparison", {})
    verdict = comparison.get("verdict", "no_data")
    reason = comparison.get("reason", "")

    lines.append("")
    v_label = verdict.upper()
    lines.append(f"Verdict: {v_label}")
    lines.append(f"Reason:  {reason}")

    before_kpis = result.get("before", {})
    after_kpis = result.get("after", {})
    lines.append("")
    lines.append(
        f"Before: {before_kpis.get('total', 0)} artifacts  "
        f"After: {after_kpis.get('total', 0)} artifacts"
    )

    kpis = comparison.get("kpis", {})
    if kpis:
        lines.append("")
        lines.append("-" * 60)
        header = f"{'KPI':<30} {'Before':>10} {'After':>10} {'Delta':>10} Dir"
        lines.append(header)
        lines.append("-" * 60)
        for name, kpi in sorted(kpis.items()):
            bv = kpi["before"]
            av = kpi["after"]
            delta = kpi["delta"]
            direction = kpi["direction"]

            # Format values
            if isinstance(bv, float) and bv < 10:
                bv_s = f"{bv:.4f}"
                av_s = f"{av:.4f}"
                delta_s = f"{delta:+.4f}"
            else:
                bv_s = f"{bv}"
                av_s = f"{av}"
                delta_s = f"{delta:+}"

            # Direction indicator
            if direction == "improved":
                ind = " ✓"
            elif direction == "regressed":
                ind = " ✗"
            else:
                ind = " ="

            lines.append(f"{name:<30} {bv_s:>10} {av_s:>10} {delta_s:>10}{ind}")

    lines.append("")
    return "\n".join(lines)


def render_evaluation_json(result: Dict[str, Any]) -> str:
    """Render evaluation result as JSON."""
    return json.dumps(result, indent=2, default=str)


def render_evaluation_markdown(result: Dict[str, Any]) -> str:
    """Render evaluation result as markdown."""
    lines: List[str] = []
    comparison = result.get("comparison", {})
    verdict = comparison.get("verdict", "no_data")
    reason = comparison.get("reason", "")

    lines.append("# Tuning Evaluation Report")
    lines.append("")
    lines.append(f"**Verdict:** {verdict.upper()}")
    lines.append(f"**Reason:** {reason}")

    before_kpis = result.get("before", {})
    after_kpis = result.get("after", {})
    lines.append("")
    lines.append(f"- Before: {before_kpis.get('total', 0)} artifacts")
    lines.append(f"- After: {after_kpis.get('total', 0)} artifacts")

    kpis = comparison.get("kpis", {})
    if kpis:
        lines.append("")
        lines.append("## KPI Comparison")
        lines.append("")
        lines.append("| KPI | Before | After | Delta | Direction |")
        lines.append("|-----|--------|-------|-------|-----------|")
        for name, kpi in sorted(kpis.items()):
            bv = kpi["before"]
            av = kpi["after"]
            delta = kpi["delta"]
            direction = kpi["direction"]

            if isinstance(bv, float) and bv < 10:
                bv_s = f"{bv:.4f}"
                av_s = f"{av:.4f}"
                delta_s = f"{delta:+.4f}"
            else:
                bv_s = f"{bv}"
                av_s = f"{av}"
                delta_s = f"{delta:+}"

            lines.append(f"| {name} | {bv_s} | {av_s} | {delta_s} | {direction} |")

    lines.append("")
    return "\n".join(lines)
