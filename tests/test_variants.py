"""Tests for cts.corpus.variants — variant generator for A/B experiments."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from cts.corpus.experiment_schema import (
    ExperimentEnvelope,
    create_experiment,
)
from cts.corpus.variants import (
    _default_strategy_mapping,
    _scale_recommendation,
    generate_variants,
    get_strategy,
    list_strategies,
    make_variant_tuning,
    propose_experiment,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_tuning() -> dict:
    """A typical tuning envelope with multiple recommendation types."""
    return {
        "tuning_schema_version": 1,
        "generated_at": 1700000000.0,
        "source_corpus": "corpus.jsonl",
        "filters": {},
        "kpis_before": {
            "truncation_rate": 0.35,
            "confidence_delta_mean": 0.12,
        },
        "recommendations": [
            {
                "id": "reduce-truncation-global",
                "scope": "global",
                "change_type": "delta",
                "target": "bundle.default.max_bytes",
                "from": "current",
                "to": "+131072",
                "rationale": "Truncation rate 35% exceeds 20%",
                "evidence": {"truncated_count": 35, "total": 100, "rate": 0.35},
                "risk": "low",
                "rollback": "Revert max_bytes",
            },
            {
                "id": "cap-bundle-size",
                "scope": "global",
                "change_type": "cap",
                "target": "bundle.default.max_bytes",
                "from": "current",
                "to": "524288",
                "rationale": "Large bundles with low lift",
                "evidence": {"overfetch_count": 5},
                "risk": "low",
                "rollback": "Remove cap",
            },
            {
                "id": "raise-autopilot-threshold",
                "scope": "global",
                "change_type": "set",
                "target": "autopilot.sufficient_threshold",
                "from": 0.6,
                "to": 0.65,
                "rationale": "Too many low-lift autopilot runs",
                "evidence": {
                    "low_lift_count": 15,
                    "autopilot_enabled": 40,
                    "rate": 0.375,
                },
                "risk": "med",
                "rollback": "Revert threshold",
            },
            {
                "id": "review-action-widen",
                "scope": "global",
                "change_type": "toggle",
                "target": "autopilot.actions.widen_search.enabled",
                "from": True,
                "to": False,
                "rationale": "Near-zero lift",
                "evidence": {"mean_lift": 0.003, "median_lift": 0.0, "count": 8},
                "risk": "med",
                "rollback": "Re-enable",
            },
        ],
    }


def _repos_yaml() -> dict:
    """Minimal repos.yaml for patch generation."""
    return {
        "defaults": {
            "bundle": {
                "default": {
                    "max_bytes": 400000,
                    "context_lines": 30,
                    "evidence_files": 5,
                },
            },
            "autopilot": {
                "sufficient_threshold": 0.6,
                "default_passes": 0,
                "actions": {
                    "widen_search": {"enabled": True},
                },
            },
        },
        "repos": {},
    }


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------


class TestStrategyRegistry(unittest.TestCase):
    def test_list_strategies(self):
        names = list_strategies()
        self.assertIn("conservative", names)
        self.assertIn("aggressive", names)
        self.assertIn("focused", names)

    def test_get_valid(self):
        s = get_strategy("conservative")
        self.assertIn("delta_multiplier", s)
        self.assertAlmostEqual(s["delta_multiplier"], 0.6)

    def test_get_invalid(self):
        with self.assertRaises(ValueError):
            get_strategy("nonexistent")

    def test_get_returns_copy(self):
        s1 = get_strategy("aggressive")
        s2 = get_strategy("aggressive")
        s1["delta_multiplier"] = 999
        self.assertNotEqual(s2["delta_multiplier"], 999)


# ---------------------------------------------------------------------------
# Scaling individual recommendations
# ---------------------------------------------------------------------------


class TestScaleRecommendation(unittest.TestCase):
    def test_delta_conservative(self):
        rec = {
            "change_type": "delta",
            "to": "+131072",
            "target": "bundle.default.max_bytes",
            "evidence": {},
        }
        strategy = get_strategy("conservative")
        result = _scale_recommendation(rec, strategy)
        self.assertIsNotNone(result)
        # 131072 * 0.6 = 78643.2 → 78643
        scaled = int(result["to"].lstrip("+"))
        self.assertEqual(scaled, 78643)

    def test_delta_aggressive(self):
        rec = {
            "change_type": "delta",
            "to": "+131072",
            "target": "bundle.default.max_bytes",
            "evidence": {},
        }
        strategy = get_strategy("aggressive")
        result = _scale_recommendation(rec, strategy)
        self.assertIsNotNone(result)
        # 131072 * 1.2 = 157286.4 → 157286
        scaled = int(result["to"].lstrip("+"))
        self.assertEqual(scaled, 157286)

    def test_delta_negative(self):
        rec = {
            "change_type": "delta",
            "to": "-10",
            "target": "bundle.error.context_lines",
            "evidence": {},
        }
        strategy = get_strategy("conservative")
        result = _scale_recommendation(rec, strategy)
        # -10 * 0.6 = -6
        self.assertEqual(result["to"], "-6")

    def test_cap_conservative(self):
        rec = {
            "change_type": "cap",
            "to": "524288",
            "target": "bundle.default.max_bytes",
            "evidence": {},
        }
        strategy = get_strategy("conservative")
        result = _scale_recommendation(rec, strategy)
        # 524288 * 0.8 = 419430.4 → 419430
        self.assertEqual(result["to"], "419430")

    def test_cap_aggressive(self):
        rec = {
            "change_type": "cap",
            "to": "524288",
            "target": "bundle.default.max_bytes",
            "evidence": {},
        }
        strategy = get_strategy("aggressive")
        result = _scale_recommendation(rec, strategy)
        # 524288 * 1.3 = 681574.4 → 681574
        self.assertEqual(result["to"], "681574")

    def test_threshold_set_conservative(self):
        rec = {
            "change_type": "set",
            "to": 0.65,
            "target": "autopilot.sufficient_threshold",
            "evidence": {},
        }
        strategy = get_strategy("conservative")
        result = _scale_recommendation(rec, strategy)
        # 0.65 + 0.05 = 0.70
        self.assertAlmostEqual(result["to"], 0.70, places=3)

    def test_threshold_set_aggressive(self):
        rec = {
            "change_type": "set",
            "to": 0.65,
            "target": "autopilot.sufficient_threshold",
            "evidence": {},
        }
        strategy = get_strategy("aggressive")
        result = _scale_recommendation(rec, strategy)
        # 0.65 - 0.05 = 0.60
        self.assertAlmostEqual(result["to"], 0.60, places=3)

    def test_toggle_unchanged(self):
        rec = {
            "change_type": "toggle",
            "to": False,
            "target": "autopilot.actions.widen_search.enabled",
            "evidence": {},
        }
        strategy = get_strategy("conservative")
        result = _scale_recommendation(rec, strategy)
        self.assertEqual(result["to"], False)

    def test_focused_filters_low_evidence(self):
        rec = {
            "change_type": "delta",
            "to": "+131072",
            "target": "bundle.default.max_bytes",
            "evidence": {"count": 2},
        }
        strategy = get_strategy("focused")
        result = _scale_recommendation(rec, strategy)
        # count=2 < min_evidence_count=5 → filtered out
        self.assertIsNone(result)

    def test_focused_keeps_high_evidence(self):
        rec = {
            "change_type": "delta",
            "to": "+131072",
            "target": "bundle.default.max_bytes",
            "evidence": {"count": 10},
        }
        strategy = get_strategy("focused")
        result = _scale_recommendation(rec, strategy)
        self.assertIsNotNone(result)
        # 1.0x multiplier → same value
        scaled = int(result["to"].lstrip("+"))
        self.assertEqual(scaled, 131072)


# ---------------------------------------------------------------------------
# Default strategy mapping
# ---------------------------------------------------------------------------


class TestDefaultMapping(unittest.TestCase):
    def test_two_variants(self):
        exp = create_experiment(id="t", variant_names=["A", "B"])
        mapping = _default_strategy_mapping(exp)
        self.assertEqual(mapping["A"], "conservative")
        self.assertEqual(mapping["B"], "aggressive")

    def test_three_variants(self):
        exp = create_experiment(
            id="t",
            variant_names=["control", "mid", "bold"],
        )
        mapping = _default_strategy_mapping(exp)
        self.assertEqual(mapping["control"], "conservative")
        self.assertEqual(mapping["mid"], "focused")
        self.assertEqual(mapping["bold"], "aggressive")

    def test_single_variant(self):
        exp = create_experiment(id="t", variant_names=["only"])
        mapping = _default_strategy_mapping(exp)
        self.assertEqual(mapping["only"], "conservative")

    def test_empty_variants(self):
        exp = ExperimentEnvelope(id="t")
        mapping = _default_strategy_mapping(exp)
        self.assertEqual(mapping, {})


# ---------------------------------------------------------------------------
# make_variant_tuning
# ---------------------------------------------------------------------------


class TestMakeVariantTuning(unittest.TestCase):
    def test_conservative_envelope(self):
        base = _base_tuning()
        result = make_variant_tuning(base, "conservative", variant_name="A")
        self.assertIn("variant_metadata", result)
        self.assertEqual(result["variant_metadata"]["strategy"], "conservative")
        self.assertEqual(result["variant_metadata"]["variant_name"], "A")
        # All 4 recs should be present
        self.assertEqual(len(result["recommendations"]), 4)

    def test_aggressive_envelope(self):
        base = _base_tuning()
        result = make_variant_tuning(base, "aggressive", variant_name="B")
        self.assertEqual(result["variant_metadata"]["strategy"], "aggressive")
        self.assertEqual(len(result["recommendations"]), 4)

    def test_focused_filters_some(self):
        base = _base_tuning()
        result = make_variant_tuning(base, "focused", variant_name="C")
        # Only recs with evidence count >= 5 remain
        # reduce-truncation: total=100 → kept
        # cap-bundle: overfetch_count=5 → no "count" key, total=0 → filtered
        # raise-threshold: rate=0.375 → no "count" key → filtered
        # review-action: count=8 → kept
        self.assertTrue(len(result["recommendations"]) < 4)

    def test_preserves_base(self):
        """Ensure base tuning is not mutated."""
        base = _base_tuning()
        original_count = len(base["recommendations"])
        make_variant_tuning(base, "conservative")
        self.assertEqual(len(base["recommendations"]), original_count)
        # Ensure no variant_metadata leaked into base
        self.assertNotIn("variant_metadata", base)


# ---------------------------------------------------------------------------
# generate_variants
# ---------------------------------------------------------------------------


class TestGenerateVariants(unittest.TestCase):
    def test_default_two_variants(self):
        base = _base_tuning()
        exp = create_experiment(id="exp1", variant_names=["A", "B"])
        results = generate_variants(base, exp)
        self.assertEqual(len(results), 2)
        self.assertEqual(
            results[0]["variant_metadata"]["strategy"],
            "conservative",
        )
        self.assertEqual(
            results[1]["variant_metadata"]["strategy"],
            "aggressive",
        )

    def test_custom_strategies(self):
        base = _base_tuning()
        exp = create_experiment(id="exp2", variant_names=["X", "Y"])
        results = generate_variants(
            base,
            exp,
            strategies={"X": "aggressive", "Y": "focused"},
        )
        self.assertEqual(
            results[0]["variant_metadata"]["strategy"],
            "aggressive",
        )
        self.assertEqual(
            results[1]["variant_metadata"]["strategy"],
            "focused",
        )

    def test_three_variants(self):
        base = _base_tuning()
        exp = create_experiment(
            id="exp3",
            variant_names=["safe", "mid", "bold"],
        )
        results = generate_variants(base, exp)
        self.assertEqual(len(results), 3)
        strategies = [r["variant_metadata"]["strategy"] for r in results]
        self.assertEqual(strategies, ["conservative", "focused", "aggressive"])

    def test_variant_names_propagate(self):
        base = _base_tuning()
        exp = create_experiment(id="exp4", variant_names=["A", "B"])
        results = generate_variants(base, exp)
        self.assertEqual(
            results[0]["variant_metadata"]["variant_name"],
            "A",
        )
        self.assertEqual(
            results[1]["variant_metadata"]["variant_name"],
            "B",
        )


# ---------------------------------------------------------------------------
# propose_experiment (end-to-end)
# ---------------------------------------------------------------------------


class TestProposeExperiment(unittest.TestCase):
    def test_writes_artifacts(self):
        base = _base_tuning()
        exp = create_experiment(id="proposal-1", variant_names=["A", "B"])
        repos = _repos_yaml()

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = propose_experiment(base, exp, repos, out_dir=tmpdir)

            # Check summary
            self.assertEqual(summary["experiment_id"], "proposal-1")
            self.assertEqual(len(summary["variants"]), 2)

            # Check files exist
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "tuning_A.json")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "tuning_B.json")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "patch_A.diff")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "patch_B.diff")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "experiment.json")))

    def test_tuning_files_are_valid_json(self):
        base = _base_tuning()
        exp = create_experiment(id="json-test", variant_names=["A", "B"])
        repos = _repos_yaml()

        with tempfile.TemporaryDirectory() as tmpdir:
            propose_experiment(base, exp, repos, out_dir=tmpdir)

            for name in ["tuning_A.json", "tuning_B.json"]:
                path = os.path.join(tmpdir, name)
                with open(path) as f:
                    data = json.load(f)
                self.assertIn("recommendations", data)
                self.assertIn("variant_metadata", data)

    def test_experiment_envelope_updated(self):
        base = _base_tuning()
        exp = create_experiment(id="ref-test", variant_names=["A", "B"])
        repos = _repos_yaml()

        with tempfile.TemporaryDirectory() as tmpdir:
            propose_experiment(base, exp, repos, out_dir=tmpdir)

            exp_path = os.path.join(tmpdir, "experiment.json")
            with open(exp_path) as f:
                data = json.load(f)

            # Variants should have tuning_ref and patch_ref
            self.assertEqual(data["variants"][0]["tuning_ref"], "tuning_A.json")
            self.assertEqual(data["variants"][0]["patch_ref"], "patch_A.diff")
            self.assertEqual(data["variants"][1]["tuning_ref"], "tuning_B.json")
            self.assertEqual(data["variants"][1]["patch_ref"], "patch_B.diff")

    def test_summary_has_patch_counts(self):
        base = _base_tuning()
        exp = create_experiment(id="count-test", variant_names=["A", "B"])
        repos = _repos_yaml()

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = propose_experiment(base, exp, repos, out_dir=tmpdir)

            for v in summary["variants"]:
                self.assertIn("active_patches", v)
                self.assertIn("skipped_patches", v)
                self.assertIn("recommendation_count", v)

    def test_custom_strategies_in_propose(self):
        base = _base_tuning()
        exp = create_experiment(id="custom-strat", variant_names=["X", "Y"])
        repos = _repos_yaml()

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = propose_experiment(
                base,
                exp,
                repos,
                out_dir=tmpdir,
                strategies={"X": "focused", "Y": "conservative"},
            )
            self.assertEqual(summary["variants"][0]["strategy"], "focused")
            self.assertEqual(
                summary["variants"][1]["strategy"],
                "conservative",
            )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases(unittest.TestCase):
    def test_empty_recommendations(self):
        base = {
            "tuning_schema_version": 1,
            "recommendations": [],
        }
        exp = create_experiment(id="empty", variant_names=["A", "B"])
        results = generate_variants(base, exp)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(results[0]["recommendations"]), 0)
        self.assertEqual(len(results[1]["recommendations"]), 0)

    def test_non_numeric_delta_unchanged(self):
        rec = {
            "change_type": "delta",
            "to": "not-a-number",
            "target": "bundle.default.max_bytes",
            "evidence": {},
        }
        strategy = get_strategy("conservative")
        result = _scale_recommendation(rec, strategy)
        # Can't parse → left unchanged
        self.assertEqual(result["to"], "not-a-number")

    def test_invalid_strategy_raises(self):
        base = _base_tuning()
        with self.assertRaises(ValueError):
            make_variant_tuning(base, "nonexistent")


if __name__ == "__main__":
    unittest.main()
