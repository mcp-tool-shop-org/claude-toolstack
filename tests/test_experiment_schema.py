"""Tests for cts.corpus.experiment_schema — experiment envelope."""

from __future__ import annotations

import json
import unittest

from cts.corpus.experiment_schema import (
    EXPERIMENT_SCHEMA_VERSION,
    AssignmentSpec,
    DecisionRule,
    ExperimentEnvelope,
    VariantSpec,
    create_experiment,
    parse_constraint,
    validate_experiment,
)


class TestVariantSpec(unittest.TestCase):
    def test_to_dict_minimal(self):
        v = VariantSpec(name="A")
        d = v.to_dict()
        self.assertEqual(d["name"], "A")
        self.assertEqual(d["tuning_ref"], "")
        self.assertNotIn("apply_plan", d)
        self.assertNotIn("expected_effects", d)

    def test_to_dict_full(self):
        v = VariantSpec(
            name="B",
            tuning_ref="tuning_B.json",
            patch_ref="patch_B.diff",
            apply_plan={"scope": "global", "duration_days": 3},
            expected_effects=["higher confidence"],
        )
        d = v.to_dict()
        self.assertEqual(d["tuning_ref"], "tuning_B.json")
        self.assertEqual(d["apply_plan"]["scope"], "global")
        self.assertEqual(d["expected_effects"], ["higher confidence"])


class TestDecisionRule(unittest.TestCase):
    def test_defaults(self):
        dr = DecisionRule()
        d = dr.to_dict()
        self.assertEqual(d["primary_kpi"], "confidence_final_mean")
        self.assertEqual(d["constraints"], [])
        self.assertEqual(d["tie_breakers"], [])

    def test_with_constraints(self):
        dr = DecisionRule(
            primary_kpi="truncation_rate",
            constraints=[
                {"kpi": "confidence_final_mean", "operator": ">=", "threshold": 0},
            ],
            tie_breakers=["bundle_bytes_p90"],
        )
        d = dr.to_dict()
        self.assertEqual(d["primary_kpi"], "truncation_rate")
        self.assertEqual(len(d["constraints"]), 1)


class TestAssignmentSpec(unittest.TestCase):
    def test_defaults(self):
        a = AssignmentSpec()
        d = a.to_dict()
        self.assertEqual(d["mode"], "manual")

    def test_repo_partition(self):
        a = AssignmentSpec(
            mode="repo_partition",
            details={"A": ["org/repo1"], "B": ["org/repo2"]},
        )
        d = a.to_dict()
        self.assertEqual(d["mode"], "repo_partition")


class TestExperimentEnvelope(unittest.TestCase):
    def test_to_dict(self):
        e = ExperimentEnvelope(
            id="exp-test",
            created_at=1700000000.0,
            description="test",
            hypothesis="should improve",
            variants=[VariantSpec(name="A"), VariantSpec(name="B")],
        )
        d = e.to_dict()
        self.assertEqual(d["experiment_schema_version"], EXPERIMENT_SCHEMA_VERSION)
        self.assertEqual(d["id"], "exp-test")
        self.assertEqual(len(d["variants"]), 2)
        self.assertEqual(len(d["kpis"]), 6)

    def test_default_kpis(self):
        e = ExperimentEnvelope()
        self.assertIn("confidence_final_mean", e.kpis)
        self.assertIn("truncation_rate", e.kpis)


class TestParseConstraint(unittest.TestCase):
    def test_lte(self):
        c = parse_constraint("truncation_rate<=+0.02")
        self.assertEqual(c["kpi"], "truncation_rate")
        self.assertEqual(c["operator"], "<=")
        self.assertAlmostEqual(c["threshold"], 0.02)

    def test_gte(self):
        c = parse_constraint("confidence_final_mean>=0")
        self.assertEqual(c["kpi"], "confidence_final_mean")
        self.assertEqual(c["operator"], ">=")

    def test_lt(self):
        c = parse_constraint("bundle_bytes_p90<500000")
        self.assertEqual(c["operator"], "<")

    def test_gt(self):
        c = parse_constraint("confidence_delta_mean>0.01")
        self.assertEqual(c["operator"], ">")
        self.assertAlmostEqual(c["threshold"], 0.01)

    def test_no_operator(self):
        c = parse_constraint("some_kpi")
        self.assertEqual(c["kpi"], "some_kpi")
        self.assertEqual(c["operator"], "<=")


class TestCreateExperiment(unittest.TestCase):
    def test_defaults(self):
        e = create_experiment()
        self.assertTrue(e.id.startswith("exp-"))
        self.assertEqual(len(e.variants), 2)
        self.assertEqual(e.variants[0].name, "A")
        self.assertEqual(e.variants[1].name, "B")
        self.assertEqual(
            e.decision_rule.primary_kpi,
            "confidence_final_mean",
        )

    def test_custom_variants(self):
        e = create_experiment(
            id="my-exp",
            variant_names=["control", "aggressive", "conservative"],
        )
        self.assertEqual(e.id, "my-exp")
        self.assertEqual(len(e.variants), 3)
        names = [v.name for v in e.variants]
        self.assertEqual(names, ["control", "aggressive", "conservative"])

    def test_with_constraints(self):
        e = create_experiment(
            constraints=["truncation_rate<=+0.02", "bundle_bytes_p90<600000"],
        )
        self.assertEqual(len(e.decision_rule.constraints), 2)
        self.assertEqual(
            e.decision_rule.constraints[0]["kpi"],
            "truncation_rate",
        )

    def test_with_primary_kpi(self):
        e = create_experiment(primary_kpi="truncation_rate")
        self.assertEqual(e.decision_rule.primary_kpi, "truncation_rate")

    def test_serialization_roundtrip(self):
        e = create_experiment(
            id="round-trip",
            description="test",
            hypothesis="things improve",
            constraints=["truncation_rate<=0.05"],
        )
        d = e.to_dict()
        payload = json.dumps(d, indent=2)
        loaded = json.loads(payload)
        self.assertEqual(loaded["id"], "round-trip")
        self.assertEqual(len(loaded["variants"]), 2)
        self.assertEqual(
            loaded["decision_rule"]["primary_kpi"],
            "confidence_final_mean",
        )


class TestValidateExperiment(unittest.TestCase):
    def test_valid(self):
        e = create_experiment(id="valid-exp")
        errors = validate_experiment(e.to_dict())
        self.assertEqual(errors, [])

    def test_missing_id(self):
        e = create_experiment()
        d = e.to_dict()
        d["id"] = ""
        errors = validate_experiment(d)
        self.assertTrue(any("id" in e for e in errors))

    def test_wrong_version(self):
        e = create_experiment(id="v-test")
        d = e.to_dict()
        d["experiment_schema_version"] = 999
        errors = validate_experiment(d)
        self.assertTrue(any("version" in e.lower() for e in errors))

    def test_too_few_variants(self):
        e = create_experiment(id="few")
        d = e.to_dict()
        d["variants"] = [{"name": "A"}]
        errors = validate_experiment(d)
        self.assertTrue(any("2 variants" in e for e in errors))

    def test_duplicate_variant_names(self):
        e = create_experiment(id="dup", variant_names=["A", "A"])
        errors = validate_experiment(e.to_dict())
        self.assertTrue(any("Duplicate" in e for e in errors))

    def test_missing_primary_kpi(self):
        e = create_experiment(id="no-kpi")
        d = e.to_dict()
        d["decision_rule"]["primary_kpi"] = ""
        errors = validate_experiment(d)
        self.assertTrue(any("primary_kpi" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
