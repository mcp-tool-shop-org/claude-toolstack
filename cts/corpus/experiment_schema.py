"""Experiment schema for A/B tuning experiments.

Defines a versioned envelope for controlled tuning experiments.
An experiment compares two (or more) candidate tuning variants
against a baseline corpus using defined KPIs and decision rules.

Consumers check ``experiment_schema_version`` to decide if they
can parse the payload.

Assignment modes:
  - ``time_window``: A runs for date range 1, B for date range 2
  - ``repo_partition``: A runs on set of repos, B on another set
  - ``manual``: operator assigns explicitly

Decision rules:
  - ``primary_kpi``: the KPI that decides the winner
  - ``constraints``: conditions that must hold (e.g. truncation
    rate must not worsen beyond a threshold)
  - ``tie_breakers``: fallback KPIs if primary is tied
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

EXPERIMENT_SCHEMA_VERSION = 1

# Default KPIs tracked in experiments (same as evaluate.py)
DEFAULT_KPIS = [
    "confidence_final_mean",
    "confidence_delta_mean",
    "truncation_rate",
    "autopilot_low_lift_rate",
    "bundle_bytes_p90",
    "should_autopilot_count",
]


@dataclass
class VariantSpec:
    """Specification for one experiment variant (A, B, ...)."""

    name: str  # "A", "B", etc.
    tuning_ref: str = ""  # path to tuning JSON
    patch_ref: str = ""  # path to patch diff
    apply_plan: Dict[str, Any] = field(default_factory=dict)
    expected_effects: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "tuning_ref": self.tuning_ref,
            "patch_ref": self.patch_ref,
        }
        if self.apply_plan:
            d["apply_plan"] = self.apply_plan
        if self.expected_effects:
            d["expected_effects"] = self.expected_effects
        return d


@dataclass
class DecisionRule:
    """How to pick the winner."""

    primary_kpi: str = "confidence_final_mean"
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    tie_breakers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_kpi": self.primary_kpi,
            "constraints": self.constraints,
            "tie_breakers": self.tie_breakers,
        }


@dataclass
class AssignmentSpec:
    """How runs are assigned to variants."""

    mode: str = "manual"  # time_window | repo_partition | manual
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "details": self.details,
        }


@dataclass
class ExperimentEnvelope:
    """Top-level envelope for an A/B experiment."""

    experiment_schema_version: int = EXPERIMENT_SCHEMA_VERSION
    id: str = ""
    created_at: float = 0.0
    description: str = ""
    hypothesis: str = ""
    kpis: List[str] = field(default_factory=lambda: list(DEFAULT_KPIS))
    baseline: Dict[str, Any] = field(default_factory=dict)
    variants: List[VariantSpec] = field(default_factory=list)
    assignment: AssignmentSpec = field(default_factory=AssignmentSpec)
    decision_rule: DecisionRule = field(default_factory=DecisionRule)
    audit: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_schema_version": self.experiment_schema_version,
            "id": self.id,
            "created_at": self.created_at,
            "description": self.description,
            "hypothesis": self.hypothesis,
            "kpis": self.kpis,
            "baseline": self.baseline,
            "variants": [v.to_dict() for v in self.variants],
            "assignment": self.assignment.to_dict(),
            "decision_rule": self.decision_rule.to_dict(),
            "audit": self.audit,
        }


def parse_constraint(spec: str) -> Dict[str, Any]:
    """Parse a constraint string like ``truncation_rate<=+0.02``.

    Supported operators: ``<=``, ``>=``, ``<``, ``>``.

    Returns dict with kpi, operator, threshold.
    """
    for op in ("<=", ">=", "<", ">"):
        if op in spec:
            parts = spec.split(op, 1)
            kpi = parts[0].strip()
            try:
                threshold = float(parts[1].strip())
            except ValueError:
                threshold = 0.0
            return {
                "kpi": kpi,
                "operator": op,
                "threshold": threshold,
            }
    return {"kpi": spec, "operator": "<=", "threshold": 0.0}


def create_experiment(
    *,
    id: str = "",
    description: str = "",
    hypothesis: str = "",
    variant_names: Optional[List[str]] = None,
    primary_kpi: str = "confidence_final_mean",
    constraints: Optional[List[str]] = None,
    assignment_mode: str = "manual",
) -> ExperimentEnvelope:
    """Create a new experiment envelope with sensible defaults.

    Args:
        id: Experiment ID (auto-generated if empty).
        description: What the experiment tests.
        hypothesis: Expected outcome.
        variant_names: List of variant names (default: ["A", "B"]).
        primary_kpi: KPI that decides the winner.
        constraints: Constraint strings (e.g. "truncation_rate<=+0.02").
        assignment_mode: How to assign runs (manual/time_window/repo_partition).

    Returns:
        A fully-specified ExperimentEnvelope.
    """
    if not id:
        id = f"exp-{uuid.uuid4().hex[:8]}"

    names = variant_names or ["A", "B"]
    variants = [VariantSpec(name=n) for n in names]

    parsed_constraints = []
    if constraints:
        for c in constraints:
            parsed_constraints.append(parse_constraint(c))

    return ExperimentEnvelope(
        id=id,
        created_at=time.time(),
        description=description,
        hypothesis=hypothesis,
        variants=variants,
        assignment=AssignmentSpec(mode=assignment_mode),
        decision_rule=DecisionRule(
            primary_kpi=primary_kpi,
            constraints=parsed_constraints,
        ),
    )


def validate_experiment(data: Dict[str, Any]) -> List[str]:
    """Validate an experiment envelope dict.

    Returns a list of error strings (empty = valid).
    """
    errors: List[str] = []

    if data.get("experiment_schema_version") != EXPERIMENT_SCHEMA_VERSION:
        errors.append(
            f"Unsupported schema version: "
            f"{data.get('experiment_schema_version')} "
            f"(expected {EXPERIMENT_SCHEMA_VERSION})"
        )

    if not data.get("id"):
        errors.append("Missing experiment id")

    variants = data.get("variants", [])
    if len(variants) < 2:
        errors.append(f"Need at least 2 variants, got {len(variants)}")

    names = [v.get("name", "") for v in variants]
    if len(names) != len(set(names)):
        errors.append("Duplicate variant names")

    dr = data.get("decision_rule", {})
    if not dr.get("primary_kpi"):
        errors.append("Missing decision_rule.primary_kpi")

    return errors
