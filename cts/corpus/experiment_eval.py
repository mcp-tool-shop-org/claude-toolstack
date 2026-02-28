"""Experiment evaluation: assign runs to variants and pick a winner.

Implements the full experiment lifecycle:
  1. **Assignment** — tag each corpus record with a variant name
  2. **Per-variant KPIs** — extract KPIs for each variant's records
  3. **Decision** — apply decision rules (primary KPI, constraints,
     tie-breakers) to determine the winner

Assignment modes:
  - ``manual``: records already tagged (variant field present)
  - ``repo_partition``: map repo → variant via a partition dict
  - ``time_window``: map timestamp → variant via date ranges

Decision algorithm:
  1. Check constraints — a variant with any constraint violation is
     eliminated (unless all variants violate the same constraint).
  2. Compare primary KPI — the variant with the better value wins.
  3. Tie-breakers — if primary KPI is within noise threshold, fall
     through to tie-breaker KPIs in order.
  4. If still tied → verdict ``tie``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from cts.corpus.evaluate import extract_kpis

# ---------------------------------------------------------------------------
# Assignment: tag records with variant names
# ---------------------------------------------------------------------------

# KPI direction maps (higher = better or lower = better)
_HIGHER_BETTER = {"confidence_final_mean", "confidence_delta_mean"}
_LOWER_BETTER = {
    "truncation_rate",
    "autopilot_low_lift_rate",
    "bundle_bytes_p90",
    "should_autopilot_count",
}

# Noise thresholds — differences below these are not significant
_NOISE: Dict[str, float] = {
    "confidence_final_mean": 0.02,
    "confidence_delta_mean": 0.02,
    "truncation_rate": 0.02,
    "autopilot_low_lift_rate": 0.05,
    "bundle_bytes_p90": 5000,
    "should_autopilot_count": 1,
}


def assign_manual(
    records: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group records by their existing ``variant`` field.

    Records without a ``variant`` field are placed in "unassigned".
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        variant = rec.get("variant", "unassigned")
        groups.setdefault(variant, []).append(rec)
    return groups


def assign_repo_partition(
    records: List[Dict[str, Any]],
    partition: Dict[str, List[str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Assign records to variants based on repo membership.

    Args:
        records: Corpus records with a ``repo`` field.
        partition: Mapping ``{variant_name: [repo1, repo2, ...]}``.

    Returns:
        Dict of variant_name → list of records.
    """
    # Build reverse lookup: repo → variant
    repo_to_variant: Dict[str, str] = {}
    for variant, repos in partition.items():
        for repo in repos:
            repo_to_variant[repo] = variant

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        repo = rec.get("repo", "")
        variant = repo_to_variant.get(repo, "unassigned")
        groups.setdefault(variant, []).append(rec)
    return groups


def assign_time_window(
    records: List[Dict[str, Any]],
    windows: Dict[str, Tuple[float, float]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Assign records to variants based on timestamp ranges.

    Args:
        records: Corpus records with a ``timestamp`` field.
        windows: Mapping ``{variant_name: (start_ts, end_ts)}``.
            Timestamps are epoch seconds.  Intervals are half-open:
            ``start <= ts < end``.

    Returns:
        Dict of variant_name → list of records.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        ts = rec.get("timestamp", 0.0)
        assigned = "unassigned"
        for variant, (start, end) in windows.items():
            if start <= ts < end:
                assigned = variant
                break
        groups.setdefault(assigned, []).append(rec)
    return groups


def assign_records(
    records: List[Dict[str, Any]],
    assignment: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Route to the correct assignment function based on mode.

    Args:
        records: Corpus records.
        assignment: Assignment spec dict with ``mode`` and ``details``.

    Returns:
        Dict of variant_name → list of records.
    """
    mode = assignment.get("mode", "manual")
    details = assignment.get("details", {})

    if mode == "repo_partition":
        return assign_repo_partition(records, details)
    elif mode == "time_window":
        # Convert detail values to tuples
        windows: Dict[str, Tuple[float, float]] = {}
        for name, window in details.items():
            if isinstance(window, (list, tuple)) and len(window) == 2:
                windows[name] = (float(window[0]), float(window[1]))
        return assign_time_window(records, windows)
    else:
        return assign_manual(records)


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def _check_constraint(
    kpis: Dict[str, Any],
    constraint: Dict[str, Any],
) -> bool:
    """Check if a single constraint is satisfied.

    Returns True if constraint passes, False if violated.
    """
    kpi_name = constraint.get("kpi", "")
    operator = constraint.get("operator", "<=")
    threshold = constraint.get("threshold", 0.0)

    value = kpis.get(kpi_name)
    if value is None:
        return True  # Can't check — assume ok

    try:
        val = float(value)
        thr = float(threshold)
    except (ValueError, TypeError):
        return True

    if operator == "<=":
        return val <= thr
    elif operator == ">=":
        return val >= thr
    elif operator == "<":
        return val < thr
    elif operator == ">":
        return val > thr
    return True


def check_constraints(
    kpis: Dict[str, Any],
    constraints: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Check all constraints against KPIs.

    Returns list of violation dicts (empty if all pass).
    """
    violations: List[Dict[str, Any]] = []
    for c in constraints:
        if not _check_constraint(kpis, c):
            violations.append(
                {
                    "kpi": c.get("kpi", ""),
                    "operator": c.get("operator", "<="),
                    "threshold": c.get("threshold", 0.0),
                    "actual": kpis.get(c.get("kpi", "")),
                }
            )
    return violations


def _compare_kpi(
    kpi_name: str,
    value_a: float,
    value_b: float,
) -> str:
    """Compare two KPI values accounting for direction and noise.

    Returns "a_wins", "b_wins", or "tie".
    """
    noise = _NOISE.get(kpi_name, 0.01)

    if abs(value_a - value_b) <= noise:
        return "tie"

    if kpi_name in _HIGHER_BETTER:
        return "a_wins" if value_a > value_b else "b_wins"
    elif kpi_name in _LOWER_BETTER:
        return "a_wins" if value_a < value_b else "b_wins"

    # Unknown direction — treat as higher=better
    return "a_wins" if value_a > value_b else "b_wins"


def pick_winner(
    variant_kpis: Dict[str, Dict[str, Any]],
    decision_rule: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply decision rules to determine the experiment winner.

    Args:
        variant_kpis: Mapping ``{variant_name: kpi_dict}``.
        decision_rule: Decision rule with primary_kpi, constraints,
            tie_breakers.

    Returns:
        Result dict with winner, verdict, constraint info, and reasoning.
    """
    primary_kpi = decision_rule.get("primary_kpi", "confidence_final_mean")
    constraints = decision_rule.get("constraints", [])
    tie_breakers = decision_rule.get("tie_breakers", [])

    variant_names = sorted(variant_kpis.keys())

    # Need at least 2 variants to compare
    if len(variant_names) < 2:
        return {
            "winner": variant_names[0] if variant_names else None,
            "verdict": "insufficient_variants",
            "reasoning": f"Only {len(variant_names)} variant(s) — need at least 2",
            "per_variant": {},
        }

    # Check for data in each variant
    per_variant: Dict[str, Any] = {}
    for name in variant_names:
        kpis = variant_kpis[name]
        total = kpis.get("total", 0)
        violations = check_constraints(kpis, constraints)
        per_variant[name] = {
            "total": total,
            "kpis": kpis,
            "constraint_violations": violations,
            "eliminated": len(violations) > 0,
        }

    # Check if any variant has no data
    no_data = [n for n in variant_names if per_variant[n]["total"] == 0]
    if no_data:
        return {
            "winner": None,
            "verdict": "no_data",
            "reasoning": f"Variant(s) with no data: {', '.join(no_data)}",
            "per_variant": per_variant,
        }

    # Eliminate variants that violate constraints
    # (unless ALL variants violate the same constraint)
    surviving = [n for n in variant_names if not per_variant[n]["eliminated"]]

    if len(surviving) == 0:
        # All variants violate constraints — compare anyway but flag it
        surviving = list(variant_names)
        all_violated = True
    else:
        all_violated = False

    if len(surviving) == 1:
        return {
            "winner": surviving[0],
            "verdict": "winner_by_elimination",
            "reasoning": (
                f"{surviving[0]} is the only variant passing all constraints"
            ),
            "per_variant": per_variant,
        }

    # Compare primary KPI across surviving variants
    # For >2 variants, do pairwise from first surviving
    best = surviving[0]
    reasoning_parts: List[str] = []
    resolved_by_tiebreaker = False

    for challenger in surviving[1:]:
        best_val = per_variant[best]["kpis"].get(primary_kpi, 0)
        chal_val = per_variant[challenger]["kpis"].get(primary_kpi, 0)

        try:
            best_val = float(best_val)
            chal_val = float(chal_val)
        except (ValueError, TypeError):
            continue

        result = _compare_kpi(primary_kpi, best_val, chal_val)

        if result == "b_wins":
            best = challenger
            reasoning_parts.append(
                f"{challenger} beats {best} on {primary_kpi} "
                f"({chal_val:.4f} vs {best_val:.4f})"
            )
        elif result == "tie":
            # Try tie-breakers
            tie_broken = False
            for tb_kpi in tie_breakers:
                tb_best = per_variant[best]["kpis"].get(tb_kpi, 0)
                tb_chal = per_variant[challenger]["kpis"].get(tb_kpi, 0)
                try:
                    tb_best = float(tb_best)
                    tb_chal = float(tb_chal)
                except (ValueError, TypeError):
                    continue

                tb_result = _compare_kpi(tb_kpi, tb_best, tb_chal)
                if tb_result == "b_wins":
                    best = challenger
                    reasoning_parts.append(
                        f"Tied on {primary_kpi}, {challenger} wins tie-breaker {tb_kpi}"
                    )
                    tie_broken = True
                    resolved_by_tiebreaker = True
                    break
                elif tb_result == "a_wins":
                    reasoning_parts.append(
                        f"Tied on {primary_kpi}, {best} wins tie-breaker {tb_kpi}"
                    )
                    tie_broken = True
                    resolved_by_tiebreaker = True
                    break

            if not tie_broken:
                reasoning_parts.append(
                    f"Tied on {primary_kpi} between {best} and {challenger}"
                )
        else:
            # best stays
            reasoning_parts.append(
                f"{best} beats {challenger} on {primary_kpi} "
                f"({best_val:.4f} vs {chal_val:.4f})"
            )

    # Determine final verdict
    if resolved_by_tiebreaker:
        # Tie-breaker resolved it — trust the result
        verdict = "winner"
        winner = best
    else:
        # Check if the "best" is actually clearly better or tied
        all_kpi_vals = []
        for name in surviving:
            val = per_variant[name]["kpis"].get(primary_kpi, 0)
            try:
                all_kpi_vals.append((name, float(val)))
            except (ValueError, TypeError):
                all_kpi_vals.append((name, 0.0))

        noise = _NOISE.get(primary_kpi, 0.01)
        best_val_f = dict(all_kpi_vals).get(best, 0.0)
        all_within_noise = all(abs(v - best_val_f) <= noise for _, v in all_kpi_vals)

        if all_within_noise and len(surviving) > 1:
            verdict = "tie"
            winner = None
        else:
            verdict = "winner"
            winner = best

    if all_violated:
        verdict = f"{verdict}_all_constraints_violated"

    return {
        "winner": winner,
        "verdict": verdict,
        "reasoning": (
            "; ".join(reasoning_parts) if reasoning_parts else "No comparison needed"
        ),
        "per_variant": per_variant,
    }


# ---------------------------------------------------------------------------
# Full experiment evaluation
# ---------------------------------------------------------------------------


def evaluate_experiment(
    records: List[Dict[str, Any]],
    experiment: Dict[str, Any],
) -> Dict[str, Any]:
    """End-to-end experiment evaluation.

    1. Assign records to variants.
    2. Extract KPIs per variant.
    3. Apply decision rules to pick a winner.

    Args:
        records: Corpus records (all variants mixed).
        experiment: Experiment envelope dict.

    Returns:
        Result dict with per-variant KPIs, winner, and reasoning.
    """
    assignment = experiment.get("assignment", {"mode": "manual"})
    decision_rule = experiment.get("decision_rule", {})
    variant_names = [
        v.get("name", f"V{i}") for i, v in enumerate(experiment.get("variants", []))
    ]

    # Assign records to variants
    groups = assign_records(records, assignment)

    # Extract KPIs per variant
    variant_kpis: Dict[str, Dict[str, Any]] = {}
    for name in variant_names:
        recs = groups.get(name, [])
        variant_kpis[name] = extract_kpis(recs)

    # Also capture unassigned if any
    unassigned = groups.get("unassigned", [])

    # Pick winner
    result = pick_winner(variant_kpis, decision_rule)

    return {
        "experiment_id": experiment.get("id", ""),
        "variant_count": len(variant_names),
        "total_records": len(records),
        "unassigned_records": len(unassigned),
        "per_variant": result["per_variant"],
        "winner": result["winner"],
        "verdict": result["verdict"],
        "reasoning": result["reasoning"],
        "decision_rule": decision_rule,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_experiment_result_text(result: Dict[str, Any]) -> str:
    """Render experiment evaluation as plain text."""
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("EXPERIMENT EVALUATION")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Experiment:  {result.get('experiment_id', '?')}")
    lines.append(f"Records:     {result.get('total_records', 0)}")
    lines.append(f"Unassigned:  {result.get('unassigned_records', 0)}")
    lines.append(f"Verdict:     {result.get('verdict', '?')}")
    lines.append(f"Winner:      {result.get('winner') or 'none'}")
    lines.append(f"Reasoning:   {result.get('reasoning', '')}")
    lines.append("")

    per_variant = result.get("per_variant", {})
    for name in sorted(per_variant):
        pv = per_variant[name]
        kpis = pv.get("kpis", {})
        lines.append(f"--- Variant: {name} ---")
        lines.append(f"  Records: {pv.get('total', 0)}")
        lines.append(f"  Eliminated: {pv.get('eliminated', False)}")
        violations = pv.get("constraint_violations", [])
        if violations:
            for v in violations:
                lines.append(
                    f"  VIOLATION: {v['kpi']} {v['operator']} "
                    f"{v['threshold']} (actual: {v['actual']})"
                )
        for k, v in sorted(kpis.items()):
            if k != "total":
                lines.append(f"  {k}: {v}")
        lines.append("")

    return "\n".join(lines)


def render_experiment_result_json(result: Dict[str, Any]) -> str:
    """Render experiment evaluation as JSON."""
    import json

    return json.dumps(result, indent=2, default=str)


def render_experiment_result_markdown(result: Dict[str, Any]) -> str:
    """Render experiment evaluation as Markdown."""
    lines: List[str] = []
    lines.append("# Experiment Evaluation")
    lines.append("")

    winner = result.get("winner") or "none"
    verdict = result.get("verdict", "?")
    lines.append(f"**Experiment:** {result.get('experiment_id', '?')}")
    lines.append(f"**Records:** {result.get('total_records', 0)}")
    lines.append(f"**Verdict:** {verdict}")
    lines.append(f"**Winner:** {winner}")
    lines.append(f"**Reasoning:** {result.get('reasoning', '')}")
    lines.append("")

    # KPI comparison table
    per_variant = result.get("per_variant", {})
    variant_names = sorted(per_variant.keys())
    if variant_names:
        # Collect all KPI names
        all_kpis = set()
        for pv in per_variant.values():
            all_kpis.update(pv.get("kpis", {}).keys())
        all_kpis.discard("total")
        kpi_names = sorted(all_kpis)

        # Header
        header = "| KPI |"
        sep = "|-----|"
        for name in variant_names:
            header += f" {name} |"
            sep += "------|"
        lines.append(header)
        lines.append(sep)

        # Records row
        row = "| Records |"
        for name in variant_names:
            row += f" {per_variant[name].get('total', 0)} |"
        lines.append(row)

        # KPI rows
        for kpi in kpi_names:
            row = f"| {kpi} |"
            for name in variant_names:
                val = per_variant[name].get("kpis", {}).get(kpi, "—")
                if isinstance(val, float):
                    row += f" {val:.4f} |"
                else:
                    row += f" {val} |"
            lines.append(row)

        lines.append("")

    # Constraint violations
    for name in variant_names:
        violations = per_variant[name].get("constraint_violations", [])
        if violations:
            lines.append(f"### ⚠ {name} — Constraint Violations")
            for v in violations:
                lines.append(
                    f"- `{v['kpi']}` {v['operator']} {v['threshold']} "
                    f"(actual: {v['actual']})"
                )
            lines.append("")

    return "\n".join(lines)
