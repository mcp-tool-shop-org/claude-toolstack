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

# Extended KPIs including semantic augmentation (Phase 4)
SEMANTIC_KPIS = DEFAULT_KPIS + [
    "semantic_invoked_rate",
    "semantic_action_rate",
    "semantic_lift_mean",
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


def create_semantic_experiment(
    *,
    id: str = "",
    description: str = "",
    hypothesis: str = "",
    assignment_mode: str = "manual",
) -> ExperimentEnvelope:
    """Create a Phase 4 semantic A/B experiment.

    Template: A = lexical-only baseline, B = lexical + semantic_fallback.

    Uses SEMANTIC_KPIS (includes semantic_lift_mean) and applies
    a constraint to prevent truncation regression.

    Args:
        id: Experiment ID (auto-generated if empty).
        description: What the experiment tests.
        hypothesis: Expected outcome.
        assignment_mode: How to assign runs.

    Returns:
        A fully-specified ExperimentEnvelope for semantic comparison.
    """
    if not id:
        id = f"exp-semantic-{uuid.uuid4().hex[:8]}"

    if not description:
        description = (
            "Compare lexical-only (A) vs lexical + semantic_fallback (B)"
        )

    if not hypothesis:
        hypothesis = (
            "Semantic fallback improves confidence_final_mean "
            "when lexical search produces sparse matches"
        )

    variants = [
        VariantSpec(
            name="A",
            expected_effects=["Baseline: lexical search only"],
        ),
        VariantSpec(
            name="B",
            expected_effects=[
                "semantic_fallback action enabled in autopilot",
                "Expected: higher confidence when matches are sparse",
            ],
        ),
    ]

    return ExperimentEnvelope(
        id=id,
        created_at=time.time(),
        description=description,
        hypothesis=hypothesis,
        kpis=list(SEMANTIC_KPIS),
        variants=variants,
        assignment=AssignmentSpec(mode=assignment_mode),
        decision_rule=DecisionRule(
            primary_kpi="confidence_final_mean",
            constraints=[
                {"kpi": "truncation_rate", "operator": "<=", "threshold": 0.02},
                {"kpi": "bundle_bytes_p90", "operator": "<=", "threshold": 5000},
            ],
            tie_breakers=["semantic_lift_mean", "confidence_delta_mean"],
        ),
    )


def create_narrowing_experiment(
    *,
    id: str = "",
    description: str = "",
    hypothesis: str = "",
    assignment_mode: str = "manual",
) -> ExperimentEnvelope:
    """Create a Phase 4.2 narrowing A/B experiment.

    Template: A = semantic_fallback (baseline), B = semantic_fallback
    + candidate narrowing (exclude_top_k).

    Primary KPI is semantic_lift_mean — we want to verify narrowing
    preserves lift while reducing latency.

    Args:
        id: Experiment ID (auto-generated if empty).
        description: What the experiment tests.
        hypothesis: Expected outcome.
        assignment_mode: How to assign runs.

    Returns:
        A fully-specified ExperimentEnvelope for narrowing comparison.
    """
    if not id:
        id = f"exp-narrowing-{uuid.uuid4().hex[:8]}"

    if not description:
        description = (
            "Compare semantic_fallback without narrowing (A) "
            "vs semantic_fallback with exclude_top_k narrowing (B)"
        )

    if not hypothesis:
        hypothesis = (
            "Candidate narrowing reduces semantic_time_ms p90 "
            "without materially reducing semantic_lift_mean"
        )

    variants = [
        VariantSpec(
            name="A",
            expected_effects=[
                "Baseline: semantic_fallback searches all chunks",
            ],
        ),
        VariantSpec(
            name="B",
            expected_effects=[
                "exclude_top_k=10 narrowing enabled",
                "Expected: lower latency, comparable lift",
            ],
        ),
    ]

    return ExperimentEnvelope(
        id=id,
        created_at=time.time(),
        description=description,
        hypothesis=hypothesis,
        kpis=list(SEMANTIC_KPIS),
        variants=variants,
        assignment=AssignmentSpec(mode=assignment_mode),
        decision_rule=DecisionRule(
            primary_kpi="semantic_lift_mean",
            constraints=[
                {
                    "kpi": "truncation_rate",
                    "operator": "<=",
                    "threshold": 0.02,
                },
            ],
            tie_breakers=["confidence_final_mean", "confidence_delta_mean"],
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
