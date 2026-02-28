"""Tuning recommendation schema for corpus analytics.

Defines a stable, versioned envelope for machine-readable tuning
recommendations derived from corpus reports.  Consumers check
``tuning_schema_version`` to decide if they can parse the payload.

Recommendations are deterministic: same corpus + same heuristics →
same output.  They contain only numeric summaries, never code or
file content.

Change types:
  - ``set``:   replace a config value with a new one
  - ``delta``: add/subtract from current value
  - ``cap``:   enforce a minimum or maximum
  - ``toggle``: enable/disable a boolean knob
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TUNING_SCHEMA_VERSION = 1


@dataclass
class TuningRecommendation:
    """A single tuning recommendation."""

    id: str  # stable slug, e.g. "raise-change-context"
    scope: str  # global | mode:error | mode:symbol | repo:org/repo
    change_type: str  # set | delta | cap | toggle
    target: str  # dotted path, e.g. "bundle.change.context_lines"
    from_value: Any = None
    to_value: Any = None
    rationale: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    risk: str = "low"  # low | med | high
    rollback: str = ""  # how to revert

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "change_type": self.change_type,
            "target": self.target,
            "from": self.from_value,
            "to": self.to_value,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "risk": self.risk,
            "rollback": self.rollback,
        }


@dataclass
class TuningEnvelope:
    """Top-level envelope wrapping a set of recommendations."""

    tuning_schema_version: int = TUNING_SCHEMA_VERSION
    generated_at: float = 0.0
    source_corpus: str = ""
    filters: Dict[str, Any] = field(default_factory=dict)
    kpis_before: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[TuningRecommendation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tuning_schema_version": self.tuning_schema_version,
            "generated_at": self.generated_at,
            "source_corpus": self.source_corpus,
            "filters": self.filters,
            "kpis_before": self.kpis_before,
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


def generate_tuning(
    agg: Dict[str, Any],
    *,
    source_corpus: str = "",
    filters: Optional[Dict[str, Any]] = None,
) -> TuningEnvelope:
    """Generate tuning recommendations from aggregated corpus stats.

    Converts the human-readable heuristics from the report engine
    into structured, machine-readable recommendations with stable
    IDs, scopes, change types, and evidence.

    Args:
        agg: Aggregated stats dict from ``_aggregate()``.
        source_corpus: Path to the source corpus JSONL (for provenance).
        filters: Any filters applied during aggregation.

    Returns:
        :class:`TuningEnvelope` with recommendations.
    """
    total = agg.get("total", 0)
    if total == 0:
        return TuningEnvelope(
            generated_at=time.time(),
            source_corpus=source_corpus,
            filters=filters or {},
        )

    recs: List[TuningRecommendation] = []

    # --- KPIs ---
    truncated_count = agg.get("truncated_count", 0)
    truncation_rate = truncated_count / total if total else 0.0
    ds = agg.get("delta_stats", {})
    size_stats = agg.get("size_stats", {})
    autopilot_enabled = agg.get("autopilot_enabled", 0)
    low_lift = agg.get("low_lift", [])
    should_autopilot = agg.get("should_autopilot", [])
    mode_confidence = agg.get("mode_confidence", {})
    action_lift = agg.get("action_lift", {})
    truncation_by_mode = agg.get("truncation_by_mode", {})

    kpis_before: Dict[str, Any] = {
        "total_artifacts": total,
        "truncation_rate": round(truncation_rate, 4),
        "confidence_delta_mean": round(ds.get("mean", 0.0), 4),
        "autopilot_low_lift_rate": (
            round(len(low_lift) / autopilot_enabled, 4) if autopilot_enabled else 0.0
        ),
        "should_have_autopiloted": len(should_autopilot),
        "bundle_bytes_p90": size_stats.get("p90", 0),
    }

    # --- Heuristic 1: High truncation rate ---
    if truncation_rate > 0.2:
        recs.append(
            TuningRecommendation(
                id="reduce-truncation-global",
                scope="global",
                change_type="delta",
                target="bundle.default.max_bytes",
                from_value="current",
                to_value="+131072",
                rationale=(
                    f"Truncation rate {truncation_rate:.1%} exceeds 20% threshold"
                ),
                evidence={
                    "truncated_count": truncated_count,
                    "total": total,
                    "rate": round(truncation_rate, 4),
                },
                risk="low",
                rollback="Revert max_bytes to previous value in repos.yaml",
            )
        )

    # Per-mode truncation
    for mode, trunc_count in truncation_by_mode.items():
        mode_total = agg.get("mode_counts", {}).get(mode, 1)
        mode_rate = trunc_count / mode_total if mode_total else 0.0
        if mode_rate > 0.3:
            recs.append(
                TuningRecommendation(
                    id=f"reduce-truncation-{mode}",
                    scope=f"mode:{mode}",
                    change_type="delta",
                    target=f"bundle.{mode}.context_lines",
                    from_value="current",
                    to_value="-10",
                    rationale=(
                        f"Mode '{mode}' truncation rate {mode_rate:.1%} exceeds 30%"
                    ),
                    evidence={
                        "truncated": trunc_count,
                        "mode_total": mode_total,
                        "rate": round(mode_rate, 4),
                    },
                    risk="low",
                    rollback=(f"Revert bundle.{mode}.context_lines in repos.yaml"),
                )
            )

    # --- Heuristic 2: Low confidence per mode ---
    for mode, mc in mode_confidence.items():
        if mc["final_mean"] < 0.5:
            recs.append(
                TuningRecommendation(
                    id=f"boost-confidence-{mode}",
                    scope=f"mode:{mode}",
                    change_type="delta",
                    target=f"bundle.{mode}.evidence_files",
                    from_value="current",
                    to_value="+3",
                    rationale=(
                        f"Mode '{mode}' mean final confidence "
                        f"{mc['final_mean']:.3f} is below 0.5"
                    ),
                    evidence={
                        "final_mean": mc["final_mean"],
                        "final_median": mc["final_median"],
                        "pass1_mean": mc["pass1_mean"],
                    },
                    risk="low",
                    rollback=(f"Revert bundle.{mode}.evidence_files in repos.yaml"),
                )
            )

    # --- Heuristic 3: Near-zero action lift ---
    for aname, lift in action_lift.items():
        if lift["mean"] < 0.01 and lift["count"] >= 3:
            recs.append(
                TuningRecommendation(
                    id=f"review-action-{aname}",
                    scope="global",
                    change_type="toggle",
                    target=f"autopilot.actions.{aname}.enabled",
                    from_value=True,
                    to_value=False,
                    rationale=(
                        f"Action '{aname}' has near-zero lift "
                        f"(mean={lift['mean']:.4f}, n={lift['count']})"
                    ),
                    evidence={
                        "mean_lift": lift["mean"],
                        "median_lift": lift["median"],
                        "count": lift["count"],
                    },
                    risk="med",
                    rollback=(f"Re-enable autopilot.actions.{aname}.enabled"),
                )
            )

    # --- Heuristic 4: Autopilot waste ---
    if autopilot_enabled > 0:
        low_lift_rate = len(low_lift) / autopilot_enabled
        if low_lift_rate > 0.3:
            recs.append(
                TuningRecommendation(
                    id="raise-autopilot-threshold",
                    scope="global",
                    change_type="set",
                    target="autopilot.sufficient_threshold",
                    from_value=0.6,
                    to_value=0.65,
                    rationale=(
                        f"{len(low_lift)}/{autopilot_enabled} "
                        f"({low_lift_rate:.1%}) autopilot runs "
                        "had <0.05 confidence lift"
                    ),
                    evidence={
                        "low_lift_count": len(low_lift),
                        "autopilot_enabled": autopilot_enabled,
                        "rate": round(low_lift_rate, 4),
                    },
                    risk="med",
                    rollback=(
                        "Revert autopilot.sufficient_threshold to 0.6 in repos.yaml"
                    ),
                )
            )

    # --- Heuristic 5: Should-have-autopiloted ---
    if should_autopilot and len(should_autopilot) > total * 0.1:
        recs.append(
            TuningRecommendation(
                id="lower-autopilot-trigger",
                scope="global",
                change_type="set",
                target="autopilot.default_passes",
                from_value=0,
                to_value=2,
                rationale=(
                    f"{len(should_autopilot)} artifacts had no autopilot "
                    f"but confidence < 0.6"
                ),
                evidence={
                    "count": len(should_autopilot),
                    "total": total,
                    "rate": round(len(should_autopilot) / total, 4),
                },
                risk="low",
                rollback=("Revert autopilot.default_passes to 0 in repos.yaml"),
            )
        )

    # --- Heuristic 6: Large bundles with low lift ---
    overfetch = agg.get("overfetch", [])
    if overfetch and len(overfetch) >= 3:
        recs.append(
            TuningRecommendation(
                id="cap-bundle-size",
                scope="global",
                change_type="cap",
                target="bundle.default.max_bytes",
                from_value="current",
                to_value="524288",
                rationale=(
                    f"{len(overfetch)} runs had large bundles "
                    f"(>50KB) but confidence lift <0.05"
                ),
                evidence={
                    "overfetch_count": len(overfetch),
                },
                risk="low",
                rollback="Remove max_bytes cap in repos.yaml",
            )
        )

    return TuningEnvelope(
        generated_at=time.time(),
        source_corpus=source_corpus,
        filters=filters or {},
        kpis_before=kpis_before,
        recommendations=recs,
    )
