"""Trend analytics: time-series analysis from archived experiments.

Extracts per-experiment data points, computes rolling averages,
win rates by strategy, common winning changes, and regression
counts — all without a database.

Dashboard output uses stable headings for automated parsing:
  # Experiment Trend Dashboard
  ## Summary
  ## Win rates
  ## KPI trends
  ## Common winning changes
  ## Regressions / constraint failures
  ## Recent experiments
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from cts.corpus.registry import scan_registry


# ---------------------------------------------------------------------------
# Data point extraction
# ---------------------------------------------------------------------------


def extract_data_points(
    entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract trend data points from registry entries.

    Each point captures: timestamp, verdict, winner, primary KPI
    values per variant, and strategy info if available.
    """
    points: List[Dict[str, Any]] = []
    for e in entries:
        if not e.get("verdict"):
            continue  # No result yet

        point: Dict[str, Any] = {
            "exp_id": e.get("exp_id", ""),
            "created_at": e.get("created_at", 0.0),
            "verdict": e.get("verdict", ""),
            "winner": e.get("winner"),
            "primary_kpi": e.get("primary_kpi", ""),
            "kpi_summary": e.get("kpi_summary", {}),
            "variant_names": e.get("variant_names", []),
            "assignment_mode": e.get("assignment_mode", ""),
        }

        # Try to extract strategy info from variant dir
        exp_dir = e.get("exp_dir", "")
        if exp_dir:
            strategies = _extract_strategies(exp_dir)
            point["strategies"] = strategies

            # Extract winning knobs
            if e.get("winner"):
                winning_recs = _extract_winning_recommendations(exp_dir, e["winner"])
                point["winning_targets"] = winning_recs

        points.append(point)

    return points


def _extract_strategies(exp_dir: str) -> Dict[str, str]:
    """Extract strategy names from variant tuning files."""
    strategies: Dict[str, str] = {}
    variants_dir = os.path.join(exp_dir, "variants")
    if not os.path.isdir(variants_dir):
        return strategies

    for fname in os.listdir(variants_dir):
        if fname.startswith("tuning_") and fname.endswith(".json"):
            vname = fname[7:-5]  # tuning_A.json → A
            path = os.path.join(variants_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                meta = data.get("variant_metadata", {})
                strategies[vname] = meta.get("strategy", "unknown")
            except (json.JSONDecodeError, OSError):
                pass
    return strategies


def _extract_winning_recommendations(
    exp_dir: str,
    winner: str,
) -> List[str]:
    """Extract target keys from the winning variant's tuning."""
    tuning_path = os.path.join(exp_dir, "variants", f"tuning_{winner}.json")
    if not os.path.exists(tuning_path):
        return []

    try:
        with open(tuning_path, encoding="utf-8") as f:
            data = json.load(f)
        recs = data.get("recommendations", [])
        return [r.get("target", "") for r in recs if r.get("target")]
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Aggregate analytics
# ---------------------------------------------------------------------------


def compute_win_rates(
    points: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute win rates grouped by strategy.

    Returns dict with strategy → {wins, total, rate}.
    """
    strategy_stats: Dict[str, Dict[str, int]] = {}
    total_experiments = 0

    for p in points:
        winner = p.get("winner")
        strategies = p.get("strategies", {})

        if not strategies:
            continue

        total_experiments += 1
        for vname, strategy in strategies.items():
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {"wins": 0, "total": 0}
            strategy_stats[strategy]["total"] += 1
            if vname == winner:
                strategy_stats[strategy]["wins"] += 1

    result: Dict[str, Any] = {}
    for strategy, stats in sorted(strategy_stats.items()):
        total = stats["total"]
        wins = stats["wins"]
        result[strategy] = {
            "wins": wins,
            "total": total,
            "rate": round(wins / total, 3) if total > 0 else 0.0,
        }

    return {
        "total_experiments": total_experiments,
        "by_strategy": result,
    }


def compute_kpi_trends(
    points: List[Dict[str, Any]],
    *,
    window: int = 5,
) -> Dict[str, Any]:
    """Compute rolling average KPI trends.

    Tracks the primary KPI value for the winning variant over time.
    """
    # Sort by created_at ascending for rolling window
    sorted_points = sorted(points, key=lambda p: p.get("created_at", 0))

    kpi_values: List[Dict[str, Any]] = []
    for p in sorted_points:
        winner = p.get("winner")
        if not winner:
            continue

        kpi_summary = p.get("kpi_summary", {})
        primary = p.get("primary_kpi", "")
        winner_key = f"{winner}_{primary}"
        value = kpi_summary.get(winner_key)

        if value is not None:
            kpi_values.append(
                {
                    "exp_id": p["exp_id"],
                    "created_at": p["created_at"],
                    "primary_kpi": primary,
                    "value": value,
                }
            )

    # Compute rolling averages
    for i, kv in enumerate(kpi_values):
        start = max(0, i - window + 1)
        window_vals = [kpi_values[j]["value"] for j in range(start, i + 1)]
        kv["rolling_avg"] = round(sum(window_vals) / len(window_vals), 4)

    return {
        "values": kpi_values,
        "window": window,
    }


def compute_winning_knobs(
    points: List[Dict[str, Any]],
    *,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Find the most common target keys in winning variants.

    Returns top_n targets sorted by frequency.
    """
    target_counts: Dict[str, int] = {}

    for p in points:
        if not p.get("winner"):
            continue

        targets = p.get("winning_targets", [])
        for t in targets:
            target_counts[t] = target_counts.get(t, 0) + 1

    sorted_targets = sorted(target_counts.items(), key=lambda x: x[1], reverse=True)

    return [{"target": t, "wins": c} for t, c in sorted_targets[:top_n]]


def compute_regressions(
    entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Count constraint violations and regression verdicts."""
    regression_count = 0
    constraint_failures: Dict[str, int] = {}
    verdict_counts: Dict[str, int] = {}

    for e in entries:
        verdict = e.get("verdict", "")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        if "all_constraints_violated" in verdict:
            regression_count += 1

        # Count constraint types from decision rule
        constraints = e.get("constraints", [])
        for c in constraints:
            kpi = c.get("kpi", "")
            if kpi:
                key = f"{kpi}_{c.get('operator', '')}"
                constraint_failures.setdefault(key, 0)

    return {
        "regression_count": regression_count,
        "verdict_counts": verdict_counts,
        "constraint_kpis": list(constraint_failures.keys()),
    }


# ---------------------------------------------------------------------------
# Dashboard generation
# ---------------------------------------------------------------------------


def generate_dashboard(
    root: str = "experiments",
    *,
    window_days: Optional[float] = None,
    primary_kpi: Optional[str] = None,
    group_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a full trend dashboard from the registry.

    Args:
        root: Registry root directory.
        window_days: Only include experiments from last N days.
        primary_kpi: Filter by primary KPI.
        group_by: Grouping strategy (future use).

    Returns:
        Dashboard data dict suitable for rendering.
    """
    import time

    entries = scan_registry(root)

    # Filter by window
    if window_days is not None:
        cutoff = time.time() - (window_days * 86400)
        entries = [e for e in entries if e.get("created_at", 0) >= cutoff]

    # Filter by primary KPI
    if primary_kpi:
        entries = [e for e in entries if e.get("primary_kpi") == primary_kpi]

    points = extract_data_points(entries)

    return {
        "total_experiments": len(entries),
        "with_results": len([e for e in entries if e.get("verdict")]),
        "win_rates": compute_win_rates(points),
        "kpi_trends": compute_kpi_trends(points),
        "winning_knobs": compute_winning_knobs(points),
        "regressions": compute_regressions(entries),
        "recent": _summarize_recent(entries[:10]),
    }


def _summarize_recent(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trim registry entries to lightweight summaries for the dashboard."""
    keep_keys = {
        "exp_id",
        "created_at",
        "description",
        "verdict",
        "winner",
        "primary_kpi",
        "assignment_mode",
        "variant_names",
    }
    return [{k: e[k] for k in keep_keys if k in e} for e in entries]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_dashboard_markdown(dashboard: Dict[str, Any]) -> str:
    """Render dashboard as Markdown with stable headings."""
    lines: List[str] = []

    lines.append("# Experiment Trend Dashboard")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    total = dashboard.get("total_experiments", 0)
    with_results = dashboard.get("with_results", 0)
    lines.append(f"- **Total experiments:** {total}")
    lines.append(f"- **With results:** {with_results}")
    lines.append("")

    # Win rates
    lines.append("## Win rates")
    lines.append("")
    wr = dashboard.get("win_rates", {})
    by_strategy = wr.get("by_strategy", {})
    if by_strategy:
        lines.append("| Strategy | Wins | Total | Rate |")
        lines.append("|----------|------|-------|------|")
        for strategy, stats in sorted(by_strategy.items()):
            wins = stats.get("wins", 0)
            total_s = stats.get("total", 0)
            rate = stats.get("rate", 0.0)
            lines.append(f"| {strategy} | {wins} | {total_s} | {rate:.1%} |")
    else:
        lines.append("No strategy data available.")
    lines.append("")

    # KPI trends
    lines.append("## KPI trends")
    lines.append("")
    kpi_data = dashboard.get("kpi_trends", {})
    values = kpi_data.get("values", [])
    if values:
        lines.append("| Experiment | KPI | Value | Rolling Avg |")
        lines.append("|-----------|-----|-------|-------------|")
        for v in values[-10:]:  # Last 10
            lines.append(
                f"| {v['exp_id']} | {v['primary_kpi']} "
                f"| {v['value']:.4f} | {v['rolling_avg']:.4f} |"
            )
    else:
        lines.append("No KPI trend data available.")
    lines.append("")

    # Common winning changes
    lines.append("## Common winning changes")
    lines.append("")
    knobs = dashboard.get("winning_knobs", [])
    if knobs:
        lines.append("| Target | Win count |")
        lines.append("|--------|----------|")
        for k in knobs:
            lines.append(f"| `{k['target']}` | {k['wins']} |")
    else:
        lines.append("No winning change data available.")
    lines.append("")

    # Regressions
    lines.append("## Regressions / constraint failures")
    lines.append("")
    reg = dashboard.get("regressions", {})
    reg_count = reg.get("regression_count", 0)
    lines.append(f"- **All-constraints-violated:** {reg_count}")
    verdict_counts = reg.get("verdict_counts", {})
    if verdict_counts:
        lines.append("- Verdict distribution:")
        for v, c in sorted(verdict_counts.items()):
            lines.append(f"  - {v}: {c}")
    lines.append("")

    # Recent experiments
    lines.append("## Recent experiments")
    lines.append("")
    recent = dashboard.get("recent", [])
    if recent:
        lines.append("| ID | Verdict | Winner | Primary KPI | Description |")
        lines.append("|---|---|---|---|---|")
        for e in recent[:10]:
            exp_id = e.get("exp_id", "?")
            verdict = e.get("verdict", "—")
            winner = e.get("winner") or "—"
            pkpi = e.get("primary_kpi", "")
            desc = e.get("description", "")
            lines.append(f"| {exp_id} | {verdict} | {winner} | {pkpi} | {desc} |")
    else:
        lines.append("No experiments found.")
    lines.append("")

    return "\n".join(lines)


def render_dashboard_json(dashboard: Dict[str, Any]) -> str:
    """Render dashboard as JSON."""
    return json.dumps(dashboard, indent=2, default=str)


def render_dashboard_text(dashboard: Dict[str, Any]) -> str:
    """Render dashboard as plain text."""
    lines: List[str] = []

    lines.append("=" * 60)
    lines.append("EXPERIMENT TREND DASHBOARD")
    lines.append("=" * 60)
    lines.append("")

    total = dashboard.get("total_experiments", 0)
    with_results = dashboard.get("with_results", 0)
    lines.append(f"Total experiments:  {total}")
    lines.append(f"With results:       {with_results}")
    lines.append("")

    # Win rates
    wr = dashboard.get("win_rates", {})
    by_strategy = wr.get("by_strategy", {})
    if by_strategy:
        lines.append("Win rates by strategy:")
        for strategy, stats in sorted(by_strategy.items()):
            rate = stats.get("rate", 0.0)
            wins = stats.get("wins", 0)
            total_s = stats.get("total", 0)
            lines.append(f"  {strategy}: {rate:.1%} ({wins}/{total_s})")
    lines.append("")

    # Winning knobs
    knobs = dashboard.get("winning_knobs", [])
    if knobs:
        lines.append("Top winning changes:")
        for k in knobs[:5]:
            lines.append(f"  {k['target']}: {k['wins']} win(s)")
    lines.append("")

    # Regressions
    reg = dashboard.get("regressions", {})
    reg_count = reg.get("regression_count", 0)
    lines.append(f"Regressions (all constraints violated): {reg_count}")
    lines.append("")

    # Recent
    recent = dashboard.get("recent", [])
    if recent:
        lines.append("Recent experiments:")
        for e in recent[:5]:
            exp_id = e.get("exp_id", "?")
            verdict = e.get("verdict", "?")
            winner = e.get("winner") or "none"
            lines.append(f"  {exp_id}: {verdict} (winner: {winner})")

    lines.append("")
    return "\n".join(lines)
