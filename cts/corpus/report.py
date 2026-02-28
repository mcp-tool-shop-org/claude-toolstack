"""Reporting engine for corpus analytics.

Generates aggregate reports from corpus JSONL with zero external deps.
Supports text, markdown, and JSON output formats.

Report sections:
  - Coverage (total artifacts, repos, modes)
  - Mode distribution
  - Autopilot usage
  - Action effectiveness (confidence lift)
  - Trigger reasons (top)
  - Truncation hot spots
  - Bundle size distribution
  - Timing hot spots
  - Low-lift autopilot cases
  - Recommendations (automatic hints)
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Load corpus JSONL
# ---------------------------------------------------------------------------


def load_corpus(path: str) -> List[Dict[str, Any]]:
    """Load corpus records from a JSONL file."""
    records: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


def _percentile(values: List[float], p: float) -> float:
    """Compute the *p*-th percentile (0-100) of *values*.

    Uses linear interpolation between data points (same method as
    NumPy's default).
    """
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


def _median(values: List[float]) -> float:
    return _percentile(values, 50)


def _bucket_deltas(deltas: List[float]) -> Dict[str, int]:
    """Bucket confidence deltas into ranges."""
    buckets = {"<0": 0, "0-0.1": 0, "0.1-0.25": 0, ">0.25": 0}
    for d in deltas:
        if d < 0:
            buckets["<0"] += 1
        elif d <= 0.1:
            buckets["0-0.1"] += 1
        elif d <= 0.25:
            buckets["0.1-0.25"] += 1
        else:
            buckets[">0.25"] += 1
    return buckets


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(
    records: List[Dict[str, Any]],
    *,
    mode_filter: Optional[str] = None,
    repo_filter: Optional[str] = None,
    action_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute all aggregations from corpus records.

    Returns a dict of aggregated stats suitable for rendering.
    """
    # Apply filters
    filtered = records
    if mode_filter:
        filtered = [r for r in filtered if r.get("mode") == mode_filter]
    if repo_filter:
        filtered = [r for r in filtered if r.get("repo") == repo_filter]
    if action_filter:
        filtered = [
            r
            for r in filtered
            if any(a.get("name") == action_filter for a in r.get("actions", []))
        ]

    total = len(filtered)
    if total == 0:
        return {"total": 0}

    # --- Coverage ---
    repos = set()
    modes_set = set()
    for r in filtered:
        repos.add(r.get("repo", ""))
        modes_set.add(r.get("mode", ""))

    # --- Mode distribution ---
    mode_counts: Dict[str, int] = {}
    for r in filtered:
        m = r.get("mode", "unknown")
        mode_counts[m] = mode_counts.get(m, 0) + 1

    # --- Autopilot usage ---
    autopilot_enabled = sum(1 for r in filtered if r.get("passes_count", 0) > 0)
    autopilot_disabled = total - autopilot_enabled

    # --- Action distribution ---
    action_counts: Dict[str, int] = {}
    action_counts_by_mode: Dict[str, Dict[str, int]] = {}
    for r in filtered:
        mode = r.get("mode", "unknown")
        for a in r.get("actions", []):
            name = a.get("name", "unknown")
            action_counts[name] = action_counts.get(name, 0) + 1
            if mode not in action_counts_by_mode:
                action_counts_by_mode[mode] = {}
            action_counts_by_mode[mode][name] = (
                action_counts_by_mode[mode].get(name, 0) + 1
            )

    # --- Trigger reason distribution ---
    trigger_counts: Dict[str, int] = {}
    trigger_by_action: Dict[str, Dict[str, int]] = {}
    for r in filtered:
        for a in r.get("actions", []):
            reason = a.get("trigger_reason", "")
            if reason:
                # Normalize: take first 80 chars to group similar reasons
                key = reason[:80]
                trigger_counts[key] = trigger_counts.get(key, 0) + 1
                aname = a.get("name", "unknown")
                if aname not in trigger_by_action:
                    trigger_by_action[aname] = {}
                trigger_by_action[aname][key] = trigger_by_action[aname].get(key, 0) + 1

    # --- Confidence lift ---
    deltas_all: List[float] = []
    deltas_by_action: Dict[str, List[float]] = {}
    pass1_by_mode: Dict[str, List[float]] = {}
    final_by_mode: Dict[str, List[float]] = {}

    for r in filtered:
        delta = r.get("confidence_delta")
        if delta is not None:
            deltas_all.append(delta)

        mode = r.get("mode", "unknown")
        p1 = r.get("confidence_pass1")
        pf = r.get("confidence_final")
        if p1 is not None:
            pass1_by_mode.setdefault(mode, []).append(p1)
        if pf is not None:
            final_by_mode.setdefault(mode, []).append(pf)

        # Per-action deltas: attribute the artifact's delta to each action
        if delta is not None:
            for a in r.get("actions", []):
                aname = a.get("name", "unknown")
                deltas_by_action.setdefault(aname, []).append(delta)

    delta_stats = {
        "mean": _mean(deltas_all),
        "median": _median(deltas_all),
        "buckets": _bucket_deltas(deltas_all),
    }

    action_lift: Dict[str, Dict[str, float]] = {}
    for aname, deltas in deltas_by_action.items():
        action_lift[aname] = {
            "mean": round(_mean(deltas), 4),
            "median": round(_median(deltas), 4),
            "count": len(deltas),
        }

    mode_confidence: Dict[str, Dict[str, float]] = {}
    for mode in set(list(pass1_by_mode.keys()) + list(final_by_mode.keys())):
        p1_vals = pass1_by_mode.get(mode, [])
        pf_vals = final_by_mode.get(mode, [])
        mode_confidence[mode] = {
            "pass1_mean": round(_mean(p1_vals), 4),
            "pass1_median": round(_median(p1_vals), 4),
            "final_mean": round(_mean(pf_vals), 4),
            "final_median": round(_median(pf_vals), 4),
        }

    # --- Truncation ---
    truncated_count = sum(
        1 for r in filtered if r.get("truncation_flags", {}).get("truncated", False)
    )
    truncation_by_section: Dict[str, int] = {}
    truncation_by_mode: Dict[str, int] = {}
    for r in filtered:
        flags = r.get("truncation_flags", {})
        if flags.get("truncated"):
            mode = r.get("mode", "unknown")
            truncation_by_mode[mode] = truncation_by_mode.get(mode, 0) + 1
            # Per-section: count which sections are largest
            sb = r.get("section_bytes", {})
            if sb:
                biggest = max(sb, key=sb.get)  # type: ignore[arg-type]
                truncation_by_section[biggest] = (
                    truncation_by_section.get(biggest, 0) + 1
                )

    # --- Size distributions ---
    bundle_sizes = [r.get("bundle_bytes_final", 0) for r in filtered]
    section_sizes_by_mode: Dict[str, Dict[str, List[int]]] = {}
    for r in filtered:
        mode = r.get("mode", "unknown")
        sb = r.get("section_bytes", {})
        if mode not in section_sizes_by_mode:
            section_sizes_by_mode[mode] = {}
        for sec, size in sb.items():
            section_sizes_by_mode[mode].setdefault(sec, []).append(size)

    size_stats = {
        "p50": _percentile(bundle_sizes, 50),
        "p90": _percentile(bundle_sizes, 90),
        "p99": _percentile(bundle_sizes, 99),
    }

    section_p90_by_mode: Dict[str, Dict[str, float]] = {}
    for mode, sections in section_sizes_by_mode.items():
        section_p90_by_mode[mode] = {}
        for sec, vals in sections.items():
            section_p90_by_mode[mode][sec] = _percentile([float(v) for v in vals], 90)

    # --- Timing distributions ---
    timing_all: Dict[str, List[float]] = {}
    for r in filtered:
        for lap, ms in r.get("timings_ms", {}).items():
            timing_all.setdefault(lap, []).append(ms)

    timing_stats: Dict[str, Dict[str, float]] = {}
    for lap, vals in timing_all.items():
        timing_stats[lap] = {
            "p50": round(_percentile(vals, 50), 1),
            "p90": round(_percentile(vals, 90), 1),
        }

    # Slowest 20 artifacts
    slowest = sorted(
        [
            (r.get("request_id", "?"), sum(r.get("timings_ms", {}).values()))
            for r in filtered
            if r.get("timings_ms")
        ],
        key=lambda x: x[1],
        reverse=True,
    )[:20]

    # --- Low-lift autopilot cases ---
    low_lift: List[Dict[str, Any]] = []
    for r in filtered:
        if r.get("passes_count", 0) > 0:
            delta = r.get("confidence_delta")
            if delta is not None and delta < 0.05:
                actions = [a.get("name", "?") for a in r.get("actions", [])]
                low_lift.append(
                    {
                        "request_id": r.get("request_id", "?"),
                        "mode": r.get("mode", "?"),
                        "delta": delta,
                        "actions": actions,
                    }
                )

    # --- Should-have-autopiloted ---
    should_autopilot: List[Dict[str, Any]] = []
    for r in filtered:
        if r.get("passes_count", 0) == 0:
            p1 = r.get("confidence_pass1")
            if p1 is not None and p1 < 0.6:
                should_autopilot.append(
                    {
                        "request_id": r.get("request_id", "?"),
                        "mode": r.get("mode", "?"),
                        "confidence": p1,
                    }
                )

    # --- Overfetch signals ---
    overfetch: List[Dict[str, Any]] = []
    for r in filtered:
        if r.get("passes_count", 0) > 0:
            delta = r.get("confidence_delta")
            size_final = r.get("bundle_bytes_final", 0)
            if delta is not None and delta < 0.05 and size_final > 50000:
                overfetch.append(
                    {
                        "request_id": r.get("request_id", "?"),
                        "mode": r.get("mode", "?"),
                        "delta": delta,
                        "bundle_kb": round(size_final / 1024, 1),
                    }
                )

    # --- Recommendations ---
    recommendations: List[str] = []
    if truncated_count > total * 0.2:
        recommendations.append(
            f"High truncation rate ({truncated_count}/{total}). "
            "Consider increasing default max_bytes or reducing slice context."
        )
    for mode, mc in mode_confidence.items():
        if mc["final_mean"] < 0.5:
            recommendations.append(
                f"Mode '{mode}' has low final confidence "
                f"(mean={mc['final_mean']:.3f}). "
                "Consider tuning mode-specific signals."
            )
    for aname, lift in action_lift.items():
        if lift["mean"] < 0.01 and lift["count"] >= 3:
            recommendations.append(
                f"Action '{aname}' has near-zero lift (mean={lift['mean']:.4f}, "
                f"n={lift['count']}). Consider disabling or revising trigger logic."
            )
    if low_lift and len(low_lift) > autopilot_enabled * 0.3:
        recommendations.append(
            f"{len(low_lift)}/{autopilot_enabled} autopilot runs had <0.05 lift. "
            "Consider raising the sufficient threshold or tightening planner."
        )

    return {
        "total": total,
        "repos": sorted(repos),
        "modes": sorted(modes_set),
        "mode_counts": mode_counts,
        "autopilot_enabled": autopilot_enabled,
        "autopilot_disabled": autopilot_disabled,
        "action_counts": action_counts,
        "action_counts_by_mode": action_counts_by_mode,
        "trigger_counts": trigger_counts,
        "trigger_by_action": trigger_by_action,
        "delta_stats": delta_stats,
        "action_lift": action_lift,
        "mode_confidence": mode_confidence,
        "truncated_count": truncated_count,
        "truncation_by_section": truncation_by_section,
        "truncation_by_mode": truncation_by_mode,
        "size_stats": size_stats,
        "section_p90_by_mode": section_p90_by_mode,
        "timing_stats": timing_stats,
        "slowest": slowest,
        "low_lift": low_lift,
        "should_autopilot": should_autopilot,
        "overfetch": overfetch,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_markdown(agg: Dict[str, Any]) -> str:
    """Render aggregated stats as a markdown report."""
    lines: List[str] = []

    total = agg["total"]
    if total == 0:
        return "# Sidecar Corpus Report\n\nNo records to report.\n"

    lines.append("# Sidecar Corpus Report")
    lines.append("")

    # --- Coverage ---
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- **Artifacts:** {total}")
    lines.append(f"- **Repos:** {len(agg['repos'])}")
    lines.append(f"- **Modes:** {', '.join(agg['modes'])}")
    lines.append("")

    # --- Mode distribution ---
    lines.append("## Mode distribution")
    lines.append("")
    lines.append("| Mode | Count | % |")
    lines.append("|------|------:|--:|")
    for mode in sorted(agg["mode_counts"], key=agg["mode_counts"].get, reverse=True):
        count = agg["mode_counts"][mode]
        pct = round(100 * count / total, 1)
        lines.append(f"| {mode} | {count} | {pct}% |")
    lines.append("")

    # --- Autopilot usage ---
    lines.append("## Autopilot usage")
    lines.append("")
    lines.append(f"- **Enabled:** {agg['autopilot_enabled']}")
    lines.append(f"- **Disabled:** {agg['autopilot_disabled']}")
    if total:
        pct = round(100 * agg["autopilot_enabled"] / total, 1)
        lines.append(f"- **Rate:** {pct}%")
    lines.append("")

    # --- Action effectiveness ---
    lines.append("## Action effectiveness (confidence lift)")
    lines.append("")
    ds = agg["delta_stats"]
    lines.append(f"Overall: mean={ds['mean']:.4f}, median={ds['median']:.4f}")
    lines.append("")

    # Bucketed deltas
    buckets = ds["buckets"]
    lines.append("| Delta range | Count |")
    lines.append("|-------------|------:|")
    for bucket, count in buckets.items():
        lines.append(f"| {bucket} | {count} |")
    lines.append("")

    # Per-action lift
    if agg["action_lift"]:
        lines.append("### Per-action lift")
        lines.append("")
        lines.append("| Action | Mean | Median | Count |")
        lines.append("|--------|-----:|-------:|------:|")
        for aname in sorted(
            agg["action_lift"],
            key=lambda a: agg["action_lift"][a]["mean"],
            reverse=True,
        ):
            lift = agg["action_lift"][aname]
            lines.append(
                f"| {aname} | {lift['mean']:.4f} | "
                f"{lift['median']:.4f} | {lift['count']} |"
            )
        lines.append("")

    # Per-mode confidence
    if agg["mode_confidence"]:
        lines.append("### Per-mode confidence (pass1 vs final)")
        lines.append("")
        lines.append("| Mode | Pass1 mean | Pass1 median | Final mean | Final median |")
        lines.append("|------|----------:|------------:|----------:|------------:|")
        for mode in sorted(agg["mode_confidence"]):
            mc = agg["mode_confidence"][mode]
            lines.append(
                f"| {mode} | {mc['pass1_mean']:.4f} | "
                f"{mc['pass1_median']:.4f} | {mc['final_mean']:.4f} | "
                f"{mc['final_median']:.4f} |"
            )
        lines.append("")

    # --- Trigger reasons ---
    lines.append("## Trigger reasons (top)")
    lines.append("")
    if agg["trigger_counts"]:
        top_triggers = sorted(
            agg["trigger_counts"].items(), key=lambda x: x[1], reverse=True
        )[:10]
        lines.append("| Trigger | Count |")
        lines.append("|---------|------:|")
        for reason, count in top_triggers:
            lines.append(f"| {reason} | {count} |")
        lines.append("")
    else:
        lines.append("No trigger reasons recorded.")
        lines.append("")

    # --- Truncation hot spots ---
    lines.append("## Truncation hot spots")
    lines.append("")
    tc = agg["truncated_count"]
    pct = round(100 * tc / total, 1) if total else 0
    lines.append(f"- **Truncated:** {tc}/{total} ({pct}%)")
    lines.append("")

    if agg["truncation_by_mode"]:
        lines.append("| Mode | Truncated |")
        lines.append("|------|----------:|")
        for mode, count in sorted(
            agg["truncation_by_mode"].items(), key=lambda x: x[1], reverse=True
        ):
            lines.append(f"| {mode} | {count} |")
        lines.append("")

    if agg["truncation_by_section"]:
        lines.append("Largest section at truncation:")
        lines.append("")
        lines.append("| Section | Count |")
        lines.append("|---------|------:|")
        for sec, count in sorted(
            agg["truncation_by_section"].items(), key=lambda x: x[1], reverse=True
        ):
            lines.append(f"| {sec} | {count} |")
        lines.append("")

    # --- Bundle size distribution ---
    lines.append("## Bundle size distribution")
    lines.append("")
    ss = agg["size_stats"]
    lines.append(
        f"- p50: {ss['p50'] / 1024:.1f} KB, "
        f"p90: {ss['p90'] / 1024:.1f} KB, "
        f"p99: {ss['p99'] / 1024:.1f} KB"
    )
    lines.append("")

    if agg["section_p90_by_mode"]:
        lines.append("### Section p90 by mode (bytes)")
        lines.append("")
        all_secs = set()
        for mode_secs in agg["section_p90_by_mode"].values():
            all_secs.update(mode_secs.keys())
        sorted_secs = sorted(all_secs)

        header = "| Mode | " + " | ".join(sorted_secs) + " |"
        separator = "|------|" + "|".join(["-----:" for _ in sorted_secs]) + "|"
        lines.append(header)
        lines.append(separator)
        for mode in sorted(agg["section_p90_by_mode"]):
            vals = agg["section_p90_by_mode"][mode]
            cells = [f"{vals.get(s, 0):.0f}" for s in sorted_secs]
            lines.append(f"| {mode} | " + " | ".join(cells) + " |")
        lines.append("")

    # --- Timing hot spots ---
    lines.append("## Timing hot spots")
    lines.append("")
    if agg["timing_stats"]:
        lines.append("| Lap | p50 (ms) | p90 (ms) |")
        lines.append("|-----|--------:|---------:|")
        for lap in sorted(
            agg["timing_stats"],
            key=lambda t: agg["timing_stats"][t]["p90"],
            reverse=True,
        ):
            ts = agg["timing_stats"][lap]
            lines.append(f"| {lap} | {ts['p50']:.1f} | {ts['p90']:.1f} |")
        lines.append("")

        if agg["slowest"]:
            lines.append("### Slowest artifacts")
            lines.append("")
            lines.append("| Request ID | Total (ms) |")
            lines.append("|------------|----------:|")
            for rid, total_ms in agg["slowest"][:10]:
                lines.append(f"| {rid} | {total_ms:.1f} |")
            lines.append("")
    else:
        lines.append("No timing data available (artifacts may lack _debug).")
        lines.append("")

    # --- Low-lift autopilot cases ---
    lines.append("## Low-lift autopilot cases")
    lines.append("")
    if agg["low_lift"]:
        lines.append(
            f"{len(agg['low_lift'])} runs had autopilot enabled but "
            f"confidence delta < 0.05:"
        )
        lines.append("")
        lines.append("| Request ID | Mode | Delta | Actions |")
        lines.append("|------------|------|------:|---------|")
        for ll in agg["low_lift"][:20]:
            actions_str = ", ".join(ll["actions"])
            lines.append(
                f"| {ll['request_id']} | {ll['mode']} | "
                f"{ll['delta']:.4f} | {actions_str} |"
            )
        lines.append("")
    else:
        lines.append("No low-lift cases detected.")
        lines.append("")

    # Should-have-autopiloted
    if agg["should_autopilot"]:
        lines.append(
            f"**Should-have-autopiloted:** {len(agg['should_autopilot'])} runs "
            "had no autopilot but confidence < 0.6"
        )
        lines.append("")

    # Overfetch
    if agg["overfetch"]:
        lines.append(
            f"**Overfetch signals:** {len(agg['overfetch'])} runs had large "
            "bundles but small confidence lift"
        )
        lines.append("")

    # --- Recommendations ---
    lines.append("## Recommendations")
    lines.append("")
    if agg["recommendations"]:
        for rec in agg["recommendations"]:
            lines.append(f"- {rec}")
    else:
        lines.append("No issues detected. Corpus looks healthy.")
    lines.append("")

    return "\n".join(lines)


def _render_text(agg: Dict[str, Any]) -> str:
    """Render aggregated stats as plain text."""
    lines: List[str] = []

    total = agg["total"]
    if total == 0:
        return "Sidecar Corpus Report\n\nNo records to report.\n"

    lines.append("Sidecar Corpus Report")
    lines.append("=" * 50)
    lines.append("")

    # Coverage
    lines.append(f"Artifacts:  {total}")
    lines.append(f"Repos:      {len(agg['repos'])}")
    lines.append(f"Modes:      {', '.join(agg['modes'])}")
    lines.append("")

    # Mode distribution
    lines.append("Mode distribution:")
    for mode in sorted(agg["mode_counts"], key=agg["mode_counts"].get, reverse=True):
        count = agg["mode_counts"][mode]
        pct = round(100 * count / total, 1)
        lines.append(f"  {mode:<12} {count:>5} ({pct}%)")
    lines.append("")

    # Autopilot
    lines.append(
        f"Autopilot:  {agg['autopilot_enabled']} enabled, "
        f"{agg['autopilot_disabled']} disabled"
    )
    lines.append("")

    # Action effectiveness
    ds = agg["delta_stats"]
    lines.append(f"Confidence lift: mean={ds['mean']:.4f}, median={ds['median']:.4f}")
    for aname in sorted(
        agg["action_lift"],
        key=lambda a: agg["action_lift"][a]["mean"],
        reverse=True,
    ):
        lift = agg["action_lift"][aname]
        lines.append(
            f"  {aname:<25} mean={lift['mean']:.4f} "
            f"median={lift['median']:.4f} n={lift['count']}"
        )
    lines.append("")

    # Truncation
    tc = agg["truncated_count"]
    pct = round(100 * tc / total, 1) if total else 0
    lines.append(f"Truncation: {tc}/{total} ({pct}%)")
    lines.append("")

    # Sizes
    ss = agg["size_stats"]
    lines.append(
        f"Bundle size: p50={ss['p50'] / 1024:.1f}KB "
        f"p90={ss['p90'] / 1024:.1f}KB p99={ss['p99'] / 1024:.1f}KB"
    )
    lines.append("")

    # Low-lift
    if agg["low_lift"]:
        lines.append(f"Low-lift runs: {len(agg['low_lift'])}")
    if agg["should_autopilot"]:
        lines.append(f"Should-have-autopiloted: {len(agg['should_autopilot'])}")
    lines.append("")

    # Recommendations
    if agg["recommendations"]:
        lines.append("Recommendations:")
        for rec in agg["recommendations"]:
            lines.append(f"  - {rec}")
    else:
        lines.append("No issues detected.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_report(
    records: List[Dict[str, Any]],
    *,
    format: str = "markdown",
    mode_filter: Optional[str] = None,
    repo_filter: Optional[str] = None,
    action_filter: Optional[str] = None,
) -> str:
    """Generate a corpus analytics report.

    Args:
        records: List of corpus record dicts (loaded from JSONL).
        format: Output format — ``"text"``, ``"markdown"``, or ``"json"``.
        mode_filter: Only include records with this mode.
        repo_filter: Only include records from this repo.
        action_filter: Only include records containing this action.

    Returns:
        Formatted report string.
    """
    agg = _aggregate(
        records,
        mode_filter=mode_filter,
        repo_filter=repo_filter,
        action_filter=action_filter,
    )

    if format == "json":
        return json.dumps(agg, indent=2, default=str)
    elif format == "text":
        return _render_text(agg)
    else:
        return _render_markdown(agg)
