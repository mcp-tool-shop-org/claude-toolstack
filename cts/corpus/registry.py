"""Registry indexer: scan archived experiments for fast querying.

Builds an in-memory index from the on-disk experiment registry,
enabling list, search, and show operations without a database.

Each experiment entry in the index contains:
  - exp_id, created_at, description, hypothesis
  - variant names
  - assignment mode
  - decision rule (primary KPI + constraints)
  - latest result verdict/winner
  - KPI summary (primary + key deltas)
  - hashes from meta.json
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Index entry model
# ---------------------------------------------------------------------------


def _load_json_safe(path: str) -> Optional[Dict[str, Any]]:
    """Load JSON from a file, returning None on failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None


def _get_latest_result(
    results_dir: str,
) -> Optional[Dict[str, Any]]:
    """Load the most recent result from the results/ directory.

    Returns the result dict or None if no results exist.
    """
    if not os.path.isdir(results_dir):
        return None

    runs = []
    for entry in os.listdir(results_dir):
        run_dir = os.path.join(results_dir, entry)
        result_path = os.path.join(run_dir, "result.json")
        if os.path.isfile(result_path):
            # Use directory mtime as proxy for run time
            mtime = os.path.getmtime(run_dir)
            runs.append((mtime, result_path, entry))

    if not runs:
        return None

    runs.sort(reverse=True)
    result = _load_json_safe(runs[0][1])
    if result is not None:
        result["_run_id"] = runs[0][2]
    return result


def _build_entry(exp_dir: str) -> Optional[Dict[str, Any]]:
    """Build an index entry from an experiment directory.

    Returns None if the directory is not a valid experiment.
    """
    exp_path = os.path.join(exp_dir, "experiment.json")
    meta_path = os.path.join(exp_dir, "meta.json")

    exp_data = _load_json_safe(exp_path)
    if exp_data is None:
        return None

    meta = _load_json_safe(meta_path) or {}

    # Get latest result
    results_dir = os.path.join(exp_dir, "results")
    latest_result = _get_latest_result(results_dir)

    # Count runs
    run_count = 0
    if os.path.isdir(results_dir):
        run_count = len(
            [
                d
                for d in os.listdir(results_dir)
                if os.path.isdir(os.path.join(results_dir, d))
            ]
        )

    # Extract variant names
    variants = exp_data.get("variants", [])
    variant_names = [v.get("name", f"V{i}") for i, v in enumerate(variants)]

    # Decision rule
    dr = exp_data.get("decision_rule", {})
    primary_kpi = dr.get("primary_kpi", "")
    constraints = dr.get("constraints", [])

    # Assignment
    assignment = exp_data.get("assignment", {})
    assignment_mode = assignment.get("mode", "manual")

    entry: Dict[str, Any] = {
        "exp_id": exp_data.get("id", ""),
        "created_at": exp_data.get("created_at", 0.0),
        "description": exp_data.get("description", ""),
        "hypothesis": exp_data.get("hypothesis", ""),
        "variant_names": variant_names,
        "assignment_mode": assignment_mode,
        "primary_kpi": primary_kpi,
        "constraints": constraints,
        "run_count": run_count,
        "exp_dir": exp_dir,
        "hashes": meta.get("hashes", {}),
    }

    # Latest result summary
    if latest_result:
        entry["verdict"] = latest_result.get("verdict", "")
        entry["winner"] = latest_result.get("winner")
        entry["reasoning"] = latest_result.get("reasoning", "")
        entry["latest_run_id"] = latest_result.get("_run_id", "")

        # Extract KPI deltas from per_variant
        pv = latest_result.get("per_variant", {})
        kpi_summary: Dict[str, Any] = {}
        for vname, vdata in pv.items():
            kpis = vdata.get("kpis", {})
            if primary_kpi and primary_kpi in kpis:
                kpi_summary[f"{vname}_{primary_kpi}"] = kpis[primary_kpi]
        entry["kpi_summary"] = kpi_summary
    else:
        entry["verdict"] = ""
        entry["winner"] = None
        entry["reasoning"] = ""
        entry["latest_run_id"] = ""
        entry["kpi_summary"] = {}

    return entry


# ---------------------------------------------------------------------------
# Scan and index
# ---------------------------------------------------------------------------


def scan_registry(
    root: str = "experiments",
) -> List[Dict[str, Any]]:
    """Scan the experiment registry and build an index.

    Args:
        root: Root directory of the registry.

    Returns:
        List of index entries, sorted by created_at descending.
    """
    entries: List[Dict[str, Any]] = []

    if not os.path.isdir(root):
        return entries

    for name in os.listdir(root):
        exp_dir = os.path.join(root, name)
        if not os.path.isdir(exp_dir):
            continue

        entry = _build_entry(exp_dir)
        if entry is not None:
            entries.append(entry)

    # Sort by created_at descending (most recent first)
    entries.sort(key=lambda e: e.get("created_at", 0.0), reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def filter_entries(
    entries: List[Dict[str, Any]],
    *,
    winner: Optional[str] = None,
    verdict: Optional[str] = None,
    since_days: Optional[float] = None,
    primary_kpi: Optional[str] = None,
    contains: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter index entries by various criteria.

    All filters are AND-combined (all must match).
    """
    result = list(entries)

    if winner is not None:
        result = [e for e in result if e.get("winner") == winner]

    if verdict is not None:
        result = [e for e in result if e.get("verdict") == verdict]

    if since_days is not None:
        cutoff = time.time() - (since_days * 86400)
        result = [e for e in result if e.get("created_at", 0) >= cutoff]

    if primary_kpi is not None:
        result = [e for e in result if e.get("primary_kpi") == primary_kpi]

    if contains is not None:
        needle = contains.lower()
        result = [
            e
            for e in result
            if needle in e.get("description", "").lower()
            or needle in e.get("hypothesis", "").lower()
            or needle in e.get("exp_id", "").lower()
        ]

    return result


# ---------------------------------------------------------------------------
# Show: detailed experiment view
# ---------------------------------------------------------------------------


def show_experiment(
    exp_dir: str,
) -> Optional[Dict[str, Any]]:
    """Load full details for a single experiment.

    Returns a rich dict with experiment + all results, or None.
    """
    exp_path = os.path.join(exp_dir, "experiment.json")
    exp_data = _load_json_safe(exp_path)
    if exp_data is None:
        return None

    meta = _load_json_safe(os.path.join(exp_dir, "meta.json")) or {}

    # Load all results
    results_dir = os.path.join(exp_dir, "results")
    results: List[Dict[str, Any]] = []
    if os.path.isdir(results_dir):
        for run_name in sorted(os.listdir(results_dir)):
            run_dir = os.path.join(results_dir, run_name)
            result_path = os.path.join(run_dir, "result.json")
            result = _load_json_safe(result_path)
            if result is not None:
                result["_run_id"] = run_name
                result["_run_dir"] = run_dir
                results.append(result)

    # List variant artifacts
    variants_dir = os.path.join(exp_dir, "variants")
    variant_artifacts: List[str] = []
    if os.path.isdir(variants_dir):
        variant_artifacts = sorted(os.listdir(variants_dir))

    return {
        "experiment": exp_data,
        "meta": meta,
        "results": results,
        "variant_artifacts": variant_artifacts,
        "exp_dir": exp_dir,
    }


def find_experiment_dir(
    exp_id: str,
    root: str = "experiments",
) -> Optional[str]:
    """Find an experiment directory by ID.

    Returns the directory path or None if not found.
    """
    candidate = os.path.join(root, exp_id)
    if os.path.isdir(candidate):
        exp_path = os.path.join(candidate, "experiment.json")
        if os.path.exists(exp_path):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_list_text(entries: List[Dict[str, Any]]) -> str:
    """Render experiment list as a text table."""
    if not entries:
        return "No experiments found.\n"

    lines: List[str] = []
    # Header
    lines.append(
        f"{'ID':<25} {'Verdict':<15} {'Winner':<8} "
        f"{'Runs':>5} {'Primary KPI':<25} {'Description'}"
    )
    lines.append("-" * 100)

    for e in entries:
        exp_id = e.get("exp_id", "?")[:24]
        verdict = e.get("verdict", "—")[:14]
        winner = (e.get("winner") or "—")[:7]
        runs = e.get("run_count", 0)
        pkpi = e.get("primary_kpi", "")[:24]
        desc = e.get("description", "")[:40]
        lines.append(
            f"{exp_id:<25} {verdict:<15} {winner:<8} {runs:>5} {pkpi:<25} {desc}"
        )

    lines.append("")
    lines.append(f"Total: {len(entries)} experiment(s)")
    return "\n".join(lines)


def render_list_json(entries: List[Dict[str, Any]]) -> str:
    """Render experiment list as JSON."""
    return json.dumps(
        {"experiments": entries, "total": len(entries)},
        indent=2,
        default=str,
    )


def render_list_markdown(entries: List[Dict[str, Any]]) -> str:
    """Render experiment list as Markdown table."""
    if not entries:
        return "No experiments found.\n"

    lines: List[str] = []
    lines.append("# Experiment Registry")
    lines.append("")
    lines.append("| ID | Verdict | Winner | Runs | Primary KPI | Description |")
    lines.append("|---|---|---|---|---|---|")

    for e in entries:
        exp_id = e.get("exp_id", "?")
        verdict = e.get("verdict", "—")
        winner = e.get("winner") or "—"
        runs = e.get("run_count", 0)
        pkpi = e.get("primary_kpi", "")
        desc = e.get("description", "")
        lines.append(f"| {exp_id} | {verdict} | {winner} | {runs} | {pkpi} | {desc} |")

    lines.append("")
    lines.append(f"**Total:** {len(entries)} experiment(s)")
    return "\n".join(lines)


def render_show_text(detail: Dict[str, Any]) -> str:
    """Render experiment detail as text."""
    exp = detail.get("experiment", {})
    results = detail.get("results", [])
    artifacts = detail.get("variant_artifacts", [])

    lines: List[str] = []
    lines.append("=" * 60)
    lines.append(f"EXPERIMENT: {exp.get('id', '?')}")
    lines.append("=" * 60)
    lines.append(f"Description:  {exp.get('description', '')}")
    lines.append(f"Hypothesis:   {exp.get('hypothesis', '')}")
    lines.append(f"Created:      {exp.get('created_at', 0)}")

    # Variants
    variants = exp.get("variants", [])
    vnames = [v.get("name", "?") for v in variants]
    lines.append(f"Variants:     {', '.join(vnames)}")

    # Assignment
    assignment = exp.get("assignment", {})
    lines.append(f"Assignment:   {assignment.get('mode', 'manual')}")

    # Decision rule
    dr = exp.get("decision_rule", {})
    lines.append(f"Primary KPI:  {dr.get('primary_kpi', '')}")
    constraints = dr.get("constraints", [])
    if constraints:
        for c in constraints:
            lines.append(
                f"  Constraint: {c.get('kpi', '')} "
                f"{c.get('operator', '')} {c.get('threshold', '')}"
            )

    lines.append("")

    # Variant artifacts
    if artifacts:
        lines.append("Variant artifacts:")
        for a in artifacts:
            lines.append(f"  - {a}")
        lines.append("")

    # Results
    if results:
        lines.append(f"Results ({len(results)} run(s)):")
        for r in results:
            run_id = r.get("_run_id", "?")
            verdict = r.get("verdict", "?")
            winner = r.get("winner") or "none"
            lines.append(f"  [{run_id}] verdict={verdict} winner={winner}")
            reasoning = r.get("reasoning", "")
            if reasoning:
                lines.append(f"    {reasoning}")
    else:
        lines.append("No results archived yet.")

    lines.append("")
    lines.append(f"Directory: {detail.get('exp_dir', '')}")

    return "\n".join(lines)
