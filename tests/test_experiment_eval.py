"""Tests for cts.corpus.experiment_eval — experiment evaluation."""

from __future__ import annotations

import json
import unittest

from cts.corpus.experiment_eval import (
    _compare_kpi,
    assign_manual,
    assign_records,
    assign_repo_partition,
    assign_time_window,
    check_constraints,
    evaluate_experiment,
    pick_winner,
    render_experiment_result_json,
    render_experiment_result_markdown,
    render_experiment_result_text,
)
from cts.corpus.evaluate import extract_kpis


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    variant: str = "A",
    repo: str = "org/repo1",
    timestamp: float = 1700000000.0,
    confidence_final: float = 0.7,
    confidence_delta: float = 0.3,
    truncated: bool = False,
    bundle_bytes: int = 100000,
    passes_count: int = 2,
    confidence_pass1: float = 0.4,
) -> dict:
    return {
        "variant": variant,
        "repo": repo,
        "timestamp": timestamp,
        "mode": "default",
        "confidence_pass1": confidence_pass1,
        "confidence_final": confidence_final,
        "confidence_delta": confidence_delta,
        "passes_count": passes_count,
        "truncation_flags": {"truncated": truncated},
        "bundle_bytes_final": bundle_bytes,
        "actions": [],
    }


def _make_variant_records(
    variant: str,
    n: int = 10,
    **overrides,
) -> list:
    return [_make_record(variant=variant, **overrides) for _ in range(n)]


# ---------------------------------------------------------------------------
# Assignment modes
# ---------------------------------------------------------------------------


class TestAssignManual(unittest.TestCase):
    def test_groups_by_variant(self):
        records = _make_variant_records("A", 5) + _make_variant_records("B", 3)
        groups = assign_manual(records)
        self.assertEqual(len(groups["A"]), 5)
        self.assertEqual(len(groups["B"]), 3)

    def test_missing_variant_field(self):
        records = [{"confidence_final": 0.5}]
        groups = assign_manual(records)
        self.assertIn("unassigned", groups)
        self.assertEqual(len(groups["unassigned"]), 1)


class TestAssignRepoPartition(unittest.TestCase):
    def test_partition(self):
        records = _make_variant_records(
            "X", 3, repo="org/alpha"
        ) + _make_variant_records("X", 2, repo="org/beta")
        partition = {
            "A": ["org/alpha"],
            "B": ["org/beta"],
        }
        groups = assign_repo_partition(records, partition)
        self.assertEqual(len(groups["A"]), 3)
        self.assertEqual(len(groups["B"]), 2)

    def test_unknown_repo(self):
        records = [_make_record(repo="org/unknown")]
        partition = {"A": ["org/alpha"]}
        groups = assign_repo_partition(records, partition)
        self.assertIn("unassigned", groups)


class TestAssignTimeWindow(unittest.TestCase):
    def test_two_windows(self):
        records = [
            _make_record(timestamp=100.0),
            _make_record(timestamp=150.0),
            _make_record(timestamp=250.0),
        ]
        windows = {
            "A": (0.0, 200.0),
            "B": (200.0, 400.0),
        }
        groups = assign_time_window(records, windows)
        self.assertEqual(len(groups["A"]), 2)
        self.assertEqual(len(groups["B"]), 1)

    def test_outside_all_windows(self):
        records = [_make_record(timestamp=500.0)]
        windows = {"A": (0.0, 100.0)}
        groups = assign_time_window(records, windows)
        self.assertIn("unassigned", groups)


class TestAssignRecords(unittest.TestCase):
    def test_manual_mode(self):
        records = _make_variant_records("A", 3)
        assignment = {"mode": "manual", "details": {}}
        groups = assign_records(records, assignment)
        self.assertEqual(len(groups["A"]), 3)

    def test_repo_partition_mode(self):
        records = [
            _make_record(repo="org/r1"),
            _make_record(repo="org/r2"),
        ]
        assignment = {
            "mode": "repo_partition",
            "details": {"A": ["org/r1"], "B": ["org/r2"]},
        }
        groups = assign_records(records, assignment)
        self.assertEqual(len(groups["A"]), 1)
        self.assertEqual(len(groups["B"]), 1)

    def test_time_window_mode(self):
        records = [
            _make_record(timestamp=50.0),
            _make_record(timestamp=150.0),
        ]
        assignment = {
            "mode": "time_window",
            "details": {
                "A": [0.0, 100.0],
                "B": [100.0, 200.0],
            },
        }
        groups = assign_records(records, assignment)
        self.assertEqual(len(groups["A"]), 1)
        self.assertEqual(len(groups["B"]), 1)


# ---------------------------------------------------------------------------
# Constraint checking
# ---------------------------------------------------------------------------


class TestConstraints(unittest.TestCase):
    def test_pass(self):
        kpis = {"truncation_rate": 0.1}
        constraints = [{"kpi": "truncation_rate", "operator": "<=", "threshold": 0.2}]
        violations = check_constraints(kpis, constraints)
        self.assertEqual(violations, [])

    def test_violation(self):
        kpis = {"truncation_rate": 0.5}
        constraints = [{"kpi": "truncation_rate", "operator": "<=", "threshold": 0.2}]
        violations = check_constraints(kpis, constraints)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["kpi"], "truncation_rate")

    def test_gte(self):
        kpis = {"confidence_final_mean": 0.3}
        constraints = [
            {
                "kpi": "confidence_final_mean",
                "operator": ">=",
                "threshold": 0.5,
            }
        ]
        violations = check_constraints(kpis, constraints)
        self.assertEqual(len(violations), 1)

    def test_missing_kpi(self):
        kpis = {}
        constraints = [{"kpi": "unknown", "operator": "<=", "threshold": 0.2}]
        violations = check_constraints(kpis, constraints)
        # Missing KPI → assume ok
        self.assertEqual(violations, [])

    def test_multiple_constraints(self):
        kpis = {
            "truncation_rate": 0.3,
            "confidence_final_mean": 0.8,
        }
        constraints = [
            {"kpi": "truncation_rate", "operator": "<=", "threshold": 0.2},
            {
                "kpi": "confidence_final_mean",
                "operator": ">=",
                "threshold": 0.5,
            },
        ]
        violations = check_constraints(kpis, constraints)
        # Only truncation_rate violated
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["kpi"], "truncation_rate")


# ---------------------------------------------------------------------------
# KPI comparison
# ---------------------------------------------------------------------------


class TestCompareKpi(unittest.TestCase):
    def test_higher_better_a_wins(self):
        result = _compare_kpi("confidence_final_mean", 0.8, 0.5)
        self.assertEqual(result, "a_wins")

    def test_higher_better_b_wins(self):
        result = _compare_kpi("confidence_final_mean", 0.5, 0.8)
        self.assertEqual(result, "b_wins")

    def test_lower_better_a_wins(self):
        result = _compare_kpi("truncation_rate", 0.1, 0.3)
        self.assertEqual(result, "a_wins")

    def test_lower_better_b_wins(self):
        result = _compare_kpi("truncation_rate", 0.3, 0.1)
        self.assertEqual(result, "b_wins")

    def test_within_noise_tie(self):
        # Noise for confidence_final_mean is 0.02
        result = _compare_kpi("confidence_final_mean", 0.70, 0.71)
        self.assertEqual(result, "tie")


# ---------------------------------------------------------------------------
# Winner selection
# ---------------------------------------------------------------------------


class TestPickWinner(unittest.TestCase):
    def test_clear_winner(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.5))
        kpis_b = extract_kpis(_make_variant_records("B", 10, confidence_final=0.8))
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b},
            {"primary_kpi": "confidence_final_mean"},
        )
        self.assertEqual(result["winner"], "B")
        self.assertEqual(result["verdict"], "winner")

    def test_winner_by_elimination(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.6))
        kpis_b = extract_kpis(_make_variant_records("B", 10, confidence_final=0.8))
        # B has higher truncation rate → violates constraint
        kpis_b["truncation_rate"] = 0.5
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b},
            {
                "primary_kpi": "confidence_final_mean",
                "constraints": [
                    {
                        "kpi": "truncation_rate",
                        "operator": "<=",
                        "threshold": 0.3,
                    }
                ],
            },
        )
        self.assertEqual(result["winner"], "A")
        self.assertEqual(result["verdict"], "winner_by_elimination")

    def test_tie(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.70))
        kpis_b = extract_kpis(_make_variant_records("B", 10, confidence_final=0.71))
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b},
            {"primary_kpi": "confidence_final_mean"},
        )
        self.assertEqual(result["verdict"], "tie")
        self.assertIsNone(result["winner"])

    def test_tie_broken(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.70))
        kpis_b = extract_kpis(
            _make_variant_records("B", 10, confidence_final=0.71, bundle_bytes=50000)
        )
        # A: bundle_bytes_p90 = 100000, B: 50000 → B wins tie-breaker
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b},
            {
                "primary_kpi": "confidence_final_mean",
                "tie_breakers": ["bundle_bytes_p90"],
            },
        )
        self.assertEqual(result["winner"], "B")
        self.assertEqual(result["verdict"], "winner")

    def test_no_data(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.7))
        kpis_b = extract_kpis([])  # no data
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b},
            {"primary_kpi": "confidence_final_mean"},
        )
        self.assertEqual(result["verdict"], "no_data")

    def test_single_variant(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.7))
        result = pick_winner(
            {"A": kpis_a},
            {"primary_kpi": "confidence_final_mean"},
        )
        self.assertEqual(result["verdict"], "insufficient_variants")

    def test_all_constraints_violated(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.5))
        kpis_b = extract_kpis(_make_variant_records("B", 10, confidence_final=0.8))
        # Both have truncation_rate = 0.0 which is <= 0.01 threshold
        # Set it so both violate
        kpis_a["truncation_rate"] = 0.5
        kpis_b["truncation_rate"] = 0.4
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b},
            {
                "primary_kpi": "confidence_final_mean",
                "constraints": [
                    {
                        "kpi": "truncation_rate",
                        "operator": "<=",
                        "threshold": 0.2,
                    }
                ],
            },
        )
        self.assertIn("all_constraints_violated", result["verdict"])

    def test_three_variants(self):
        kpis_a = extract_kpis(_make_variant_records("A", 10, confidence_final=0.5))
        kpis_b = extract_kpis(_make_variant_records("B", 10, confidence_final=0.7))
        kpis_c = extract_kpis(_make_variant_records("C", 10, confidence_final=0.9))
        result = pick_winner(
            {"A": kpis_a, "B": kpis_b, "C": kpis_c},
            {"primary_kpi": "confidence_final_mean"},
        )
        self.assertEqual(result["winner"], "C")


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------


class TestEvaluateExperiment(unittest.TestCase):
    def test_full_eval(self):
        records = _make_variant_records(
            "A", 10, confidence_final=0.5
        ) + _make_variant_records("B", 10, confidence_final=0.8)
        experiment = {
            "id": "exp-test",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual", "details": {}},
            "decision_rule": {
                "primary_kpi": "confidence_final_mean",
                "constraints": [],
                "tie_breakers": [],
            },
        }
        result = evaluate_experiment(records, experiment)
        self.assertEqual(result["experiment_id"], "exp-test")
        self.assertEqual(result["winner"], "B")
        self.assertEqual(result["total_records"], 20)

    def test_repo_partition_eval(self):
        records = [
            _make_record(repo="org/r1", confidence_final=0.5),
            _make_record(repo="org/r1", confidence_final=0.5),
            _make_record(repo="org/r2", confidence_final=0.9),
            _make_record(repo="org/r2", confidence_final=0.9),
        ]
        experiment = {
            "id": "exp-repo",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {
                "mode": "repo_partition",
                "details": {
                    "A": ["org/r1"],
                    "B": ["org/r2"],
                },
            },
            "decision_rule": {
                "primary_kpi": "confidence_final_mean",
                "constraints": [],
                "tie_breakers": [],
            },
        }
        result = evaluate_experiment(records, experiment)
        self.assertEqual(result["winner"], "B")

    def test_with_constraints(self):
        records = _make_variant_records(
            "A", 10, confidence_final=0.6
        ) + _make_variant_records("B", 10, confidence_final=0.8, truncated=True)
        experiment = {
            "id": "exp-constraint",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {
                "primary_kpi": "confidence_final_mean",
                "constraints": [
                    {
                        "kpi": "truncation_rate",
                        "operator": "<=",
                        "threshold": 0.3,
                    }
                ],
                "tie_breakers": [],
            },
        }
        result = evaluate_experiment(records, experiment)
        # B has 100% truncation → eliminated → A wins
        self.assertEqual(result["winner"], "A")

    def test_empty_records(self):
        experiment = {
            "id": "exp-empty",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {"primary_kpi": "confidence_final_mean"},
        }
        result = evaluate_experiment([], experiment)
        self.assertEqual(result["verdict"], "no_data")


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderText(unittest.TestCase):
    def test_basic_render(self):
        records = _make_variant_records(
            "A", 10, confidence_final=0.5
        ) + _make_variant_records("B", 10, confidence_final=0.8)
        experiment = {
            "id": "exp-render",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {"primary_kpi": "confidence_final_mean"},
        }
        result = evaluate_experiment(records, experiment)
        output = render_experiment_result_text(result)
        self.assertIn("EXPERIMENT EVALUATION", output)
        self.assertIn("exp-render", output)
        self.assertIn("Verdict:", output)


class TestRenderJson(unittest.TestCase):
    def test_valid_json(self):
        records = _make_variant_records("A", 5) + _make_variant_records("B", 5)
        experiment = {
            "id": "exp-json",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {"primary_kpi": "confidence_final_mean"},
        }
        result = evaluate_experiment(records, experiment)
        output = render_experiment_result_json(result)
        parsed = json.loads(output)
        self.assertIn("winner", parsed)


class TestRenderMarkdown(unittest.TestCase):
    def test_markdown_format(self):
        records = _make_variant_records(
            "A", 5, confidence_final=0.4
        ) + _make_variant_records("B", 5, confidence_final=0.8)
        experiment = {
            "id": "exp-md",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {"primary_kpi": "confidence_final_mean"},
        }
        result = evaluate_experiment(records, experiment)
        output = render_experiment_result_markdown(result)
        self.assertIn("# Experiment Evaluation", output)
        self.assertIn("**Winner:**", output)
        self.assertIn("| KPI |", output)


if __name__ == "__main__":
    unittest.main()
