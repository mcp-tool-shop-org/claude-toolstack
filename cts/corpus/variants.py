"""Variant generator for A/B tuning experiments.

Given a corpus report's tuning recommendations (from ``generate_tuning``),
produce two (or more) VariantSpec objects that differ in how aggressively
they apply changes.  Each variant gets its own tuning JSON and optional
patch diff.

Strategies:
  - ``conservative`` (0.6× recommended deltas, tighter caps, higher
    autopilot threshold — "play it safe")
  - ``aggressive`` (1.2× recommended deltas, slightly more context,
    lower autopilot threshold — "go for it")
  - ``focused`` (applies only a subset of recommendations, typically
    the highest-evidence ones, at 1.0× — "targeted fix")

The generator produces per-variant tuning envelopes that are
structurally identical to regular tuning output, so downstream
tools (``patch``, ``apply``) work unchanged.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from cts.corpus.experiment_schema import (
    ExperimentEnvelope,
)

# ---------------------------------------------------------------------------
# Strategy multipliers
# ---------------------------------------------------------------------------

_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "delta_multiplier": 0.6,
        "cap_multiplier": 0.8,  # tighter cap
        "threshold_adjustment": +0.05,  # raise threshold → fewer autopilot
        "description": "Conservative variant: smaller deltas, tighter caps",
    },
    "aggressive": {
        "delta_multiplier": 1.2,
        "cap_multiplier": 1.3,  # looser cap
        "threshold_adjustment": -0.05,  # lower threshold → more autopilot
        "description": "Aggressive variant: larger deltas, looser caps",
    },
    "focused": {
        "delta_multiplier": 1.0,
        "cap_multiplier": 1.0,
        "threshold_adjustment": 0.0,
        "min_evidence_count": 5,  # only apply high-evidence recs
        "description": "Focused variant: only high-evidence recommendations",
    },
}


def get_strategy(name: str) -> Dict[str, Any]:
    """Return strategy config by name.

    Raises ``ValueError`` if unknown.
    """
    if name not in _STRATEGIES:
        valid = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown strategy '{name}' (valid: {valid})")
    return dict(_STRATEGIES[name])


def list_strategies() -> List[str]:
    """Return available strategy names."""
    return sorted(_STRATEGIES)


# ---------------------------------------------------------------------------
# Core: scale a single recommendation
# ---------------------------------------------------------------------------


def _scale_recommendation(
    rec: Dict[str, Any],
    strategy: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Apply a strategy's multipliers to a recommendation.

    Returns a new recommendation dict, or ``None`` if the
    recommendation should be excluded under this strategy.
    """
    out = dict(rec)
    change_type = rec.get("change_type", "set")
    to_value = rec.get("to")
    target = rec.get("target", "")

    # Focused strategy: filter by evidence count
    min_evidence = strategy.get("min_evidence_count", 0)
    if min_evidence > 0:
        evidence = rec.get("evidence", {})
        count = evidence.get("count", evidence.get("total", 0))
        if count < min_evidence:
            return None

    # Scale deltas
    if change_type == "delta":
        multiplier = strategy.get("delta_multiplier", 1.0)
        try:
            raw = int(str(to_value))
            scaled = int(raw * multiplier)
            sign = "+" if scaled >= 0 else ""
            out["to"] = f"{sign}{scaled}"
        except (ValueError, TypeError):
            pass  # leave unchanged

    # Scale caps
    elif change_type == "cap":
        multiplier = strategy.get("cap_multiplier", 1.0)
        try:
            raw = int(str(to_value))
            scaled = int(raw * multiplier)
            out["to"] = str(scaled)
        except (ValueError, TypeError):
            pass

    # Adjust thresholds (set changes targeting thresholds)
    elif change_type == "set" and "threshold" in target:
        adjustment = strategy.get("threshold_adjustment", 0.0)
        try:
            raw = float(to_value) if to_value is not None else 0.0
            adjusted = round(raw + adjustment, 4)
            out["to"] = adjusted
        except (ValueError, TypeError):
            pass

    # Toggle: keep as-is (strategy doesn't change on/off decisions)

    return out


# ---------------------------------------------------------------------------
# Public: generate variant tuning envelopes
# ---------------------------------------------------------------------------


def make_variant_tuning(
    base_tuning: Dict[str, Any],
    strategy_name: str,
    *,
    variant_name: str = "",
) -> Dict[str, Any]:
    """Create a variant tuning envelope by applying a strategy.

    Takes the base tuning envelope dict and scales its recommendations
    according to the named strategy.

    Args:
        base_tuning: Parsed tuning envelope dict.
        strategy_name: Strategy name (conservative/aggressive/focused).
        variant_name: Optional label for provenance.

    Returns:
        A new tuning envelope dict with scaled recommendations.
    """
    strategy = get_strategy(strategy_name)
    recs = base_tuning.get("recommendations", [])

    scaled_recs: List[Dict[str, Any]] = []
    for rec in recs:
        result = _scale_recommendation(rec, strategy)
        if result is not None:
            scaled_recs.append(result)

    envelope = dict(base_tuning)
    envelope["recommendations"] = scaled_recs
    envelope["variant_metadata"] = {
        "strategy": strategy_name,
        "variant_name": variant_name,
        "description": strategy.get("description", ""),
        "generated_at": time.time(),
    }
    return envelope


def generate_variants(
    base_tuning: Dict[str, Any],
    experiment: ExperimentEnvelope,
    *,
    strategies: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Generate per-variant tuning envelopes for an experiment.

    Maps each experiment variant to a strategy, then produces
    a scaled tuning envelope for each.

    Args:
        base_tuning: Base tuning envelope dict (from generate_tuning).
        experiment: The experiment envelope.
        strategies: Optional mapping ``{variant_name: strategy_name}``.
            Defaults to A=conservative, B=aggressive.

    Returns:
        List of tuning envelope dicts, one per variant, in variant order.
    """
    if strategies is None:
        # Default mapping for standard A/B
        strategies = _default_strategy_mapping(experiment)

    results: List[Dict[str, Any]] = []
    for variant in experiment.variants:
        strategy_name = strategies.get(variant.name, "conservative")
        tuning = make_variant_tuning(
            base_tuning,
            strategy_name,
            variant_name=variant.name,
        )
        results.append(tuning)
    return results


def _default_strategy_mapping(
    experiment: ExperimentEnvelope,
) -> Dict[str, str]:
    """Create a default variant → strategy mapping.

    For 2 variants: first=conservative, second=aggressive.
    For 3+: first=conservative, last=aggressive, middle=focused.
    """
    names = [v.name for v in experiment.variants]
    if len(names) == 0:
        return {}
    if len(names) == 1:
        return {names[0]: "conservative"}
    if len(names) == 2:
        return {names[0]: "conservative", names[1]: "aggressive"}

    # 3+ variants
    mapping: Dict[str, str] = {
        names[0]: "conservative",
        names[-1]: "aggressive",
    }
    for name in names[1:-1]:
        mapping[name] = "focused"
    return mapping


# ---------------------------------------------------------------------------
# Public: propose (end-to-end convenience)
# ---------------------------------------------------------------------------


def propose_experiment(
    base_tuning: Dict[str, Any],
    experiment: ExperimentEnvelope,
    repos_yaml: Dict[str, Any],
    *,
    out_dir: str = ".",
    strategies: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """End-to-end experiment proposal: generate variant artifacts.

    For each variant, writes:
      - ``tuning_<name>.json`` — scaled tuning envelope
      - ``patch_<name>.diff`` — patch diff against repos.yaml

    Also updates the experiment envelope's variants with refs
    to the generated files.

    Args:
        base_tuning: Base tuning envelope dict.
        experiment: Experiment envelope to populate.
        repos_yaml: Parsed repos.yaml dict.
        out_dir: Directory to write variant artifacts.
        strategies: Optional variant → strategy mapping.

    Returns:
        Summary dict with variant details and file paths.
    """
    from cts.corpus.patch import (
        generate_patch_plan,
        render_plan_diff,
    )

    variant_tunings = generate_variants(base_tuning, experiment, strategies=strategies)

    os.makedirs(out_dir, exist_ok=True)

    summary: Dict[str, Any] = {
        "experiment_id": experiment.id,
        "variants": [],
    }

    for variant, tuning_dict in zip(experiment.variants, variant_tunings):
        vname = variant.name

        # Write tuning JSON
        tuning_path = os.path.join(out_dir, f"tuning_{vname}.json")
        with open(tuning_path, "w", encoding="utf-8") as f:
            json.dump(tuning_dict, f, indent=2, default=str)
            f.write("\n")

        # Generate and write patch diff
        items = generate_patch_plan(tuning_dict, repos_yaml)
        diff_path = os.path.join(out_dir, f"patch_{vname}.diff")
        diff_output = render_plan_diff(repos_yaml, items)
        with open(diff_path, "w", encoding="utf-8") as f:
            f.write(diff_output)

        # Update variant spec with refs
        variant.tuning_ref = f"tuning_{vname}.json"
        variant.patch_ref = f"patch_{vname}.diff"

        active_count = len([i for i in items if not i.skipped])
        skipped_count = len([i for i in items if i.skipped])
        strategy_name = tuning_dict.get("variant_metadata", {}).get(
            "strategy", "unknown"
        )

        summary["variants"].append(
            {
                "name": vname,
                "strategy": strategy_name,
                "tuning_path": tuning_path,
                "diff_path": diff_path,
                "recommendation_count": len(tuning_dict.get("recommendations", [])),
                "active_patches": active_count,
                "skipped_patches": skipped_count,
            }
        )

    # Write updated experiment envelope
    exp_path = os.path.join(out_dir, "experiment.json")
    with open(exp_path, "w", encoding="utf-8") as f:
        json.dump(experiment.to_dict(), f, indent=2, default=str)
        f.write("\n")

    summary["experiment_path"] = exp_path
    return summary
