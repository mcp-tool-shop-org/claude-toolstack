"""Tests for cts.corpus.evaluate — before vs after evaluation."""

from __future__ import annotations

import json
import unittest

from cts.corpus.evaluate import (
    compare_kpis,
    evaluate,
    extract_kpis,
    render_evaluation_json,
    render_evaluation_markdown,
    render_evaluation_text,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    confidence_pass1: float = 0.4,
    confidence_final: float = 0.7,
    confidence_delta: float = 0.3,
    passes_count: int = 2,
    truncated: bool = False,
    bundle_bytes: int = 100000,
    mode: str = "default",
) -> dict:
    return {
        "mode": mode,
        "confidence_pass1": confidence_pass1,
        "confidence_final": confidence_final,
        "confidence_delta": confidence_delta,
        "passes_count": passes_count,
        "truncation_flags": {"truncated": truncated},
        "bundle_bytes_final": bundle_bytes,
        "actions": [],
    }


def _make_corpus(
    n: int = 10,
    **overrides,
) -> list:
    return [_make_record(**overrides) for _ in range(n)]


# ---------------------------------------------------------------------------
# KPI extraction
# ---------------------------------------------------------------------------


class TestExtractKpis(unittest.TestCase):
    def test_empty_records(self):
        kpis = extract_kpis([])
        self.assertEqual(kpis["total"], 0)

    def test_basic_extraction(self):
        records = _make_corpus(10)
        kpis = extract_kpis(records)
        self.assertEqual(kpis["total"], 10)
        self.assertAlmostEqual(kpis["confidence_final_mean"], 0.7, places=2)
        self.assertAlmostEqual(kpis["confidence_delta_mean"], 0.3, places=2)
        self.assertEqual(kpis["truncation_rate"], 0.0)
        self.assertEqual(kpis["should_autopilot_count"], 0)

    def test_truncation_rate(self):
        records = _make_corpus(10, truncated=True)
        kpis = extract_kpis(records)
        self.assertAlmostEqual(kpis["truncation_rate"], 1.0, places=2)

    def test_mixed_truncation(self):
        records = _make_corpus(8) + _make_corpus(2, truncated=True)
        kpis = extract_kpis(records)
        self.assertAlmostEqual(kpis["truncation_rate"], 0.2, places=2)

    def test_low_lift_rate(self):
        records = _make_corpus(5, confidence_delta=0.01)
        kpis = extract_kpis(records)
        self.assertAlmostEqual(kpis["autopilot_low_lift_rate"], 1.0, places=2)

    def test_should_autopilot(self):
        records = _make_corpus(5, passes_count=0, confidence_pass1=0.3)
        kpis = extract_kpis(records)
        self.assertEqual(kpis["should_autopilot_count"], 5)

    def test_bundle_p90(self):
        records = _make_corpus(10, bundle_bytes=50000)
        kpis = extract_kpis(records)
        self.assertEqual(kpis["bundle_bytes_p90"], 50000)


# ---------------------------------------------------------------------------
# KPI comparison
# ---------------------------------------------------------------------------


class TestCompareKpis(unittest.TestCase):
    def test_improved(self):
        before = {
            "total": 10,
            "confidence_final_mean": 0.5,
            "confidence_delta_mean": 0.1,
            "truncation_rate": 0.3,
            "autopilot_low_lift_rate": 0.4,
            "bundle_bytes_p90": 200000,
            "should_autopilot_count": 5,
        }
        after = {
            "total": 10,
            "confidence_final_mean": 0.7,
            "confidence_delta_mean": 0.2,
            "truncation_rate": 0.1,
            "autopilot_low_lift_rate": 0.1,
            "bundle_bytes_p90": 150000,
            "should_autopilot_count": 1,
        }
        result = compare_kpis(before, after)
        self.assertEqual(result["verdict"], "improved")
        self.assertTrue(result["improved_count"] > result["regressed_count"])

    def test_regressed(self):
        before = {
            "total": 10,
            "confidence_final_mean": 0.7,
            "confidence_delta_mean": 0.2,
            "truncation_rate": 0.1,
            "autopilot_low_lift_rate": 0.1,
            "bundle_bytes_p90": 100000,
            "should_autopilot_count": 1,
        }
        after = {
            "total": 10,
            "confidence_final_mean": 0.4,
            "confidence_delta_mean": 0.05,
            "truncation_rate": 0.5,
            "autopilot_low_lift_rate": 0.6,
            "bundle_bytes_p90": 300000,
            "should_autopilot_count": 8,
        }
        result = compare_kpis(before, after)
        self.assertEqual(result["verdict"], "regressed")

    def test_no_data(self):
        result = compare_kpis({"total": 0}, {"total": 10})
        self.assertEqual(result["verdict"], "no_data")

    def test_unchanged(self):
        before = {
            "total": 10,
            "confidence_final_mean": 0.6,
            "confidence_delta_mean": 0.15,
            "truncation_rate": 0.2,
            "autopilot_low_lift_rate": 0.3,
            "bundle_bytes_p90": 150000,
            "should_autopilot_count": 3,
        }
        # Same values — should be unchanged
        result = compare_kpis(before, dict(before))
        self.assertEqual(result["verdict"], "unchanged")

    def test_mixed(self):
        before = {
            "total": 10,
            "confidence_final_mean": 0.5,
            "confidence_delta_mean": 0.2,
            "truncation_rate": 0.1,
            "autopilot_low_lift_rate": 0.2,
            "bundle_bytes_p90": 100000,
            "should_autopilot_count": 2,
        }
        after = {
            "total": 10,
            "confidence_final_mean": 0.7,  # improved
            "confidence_delta_mean": 0.05,  # regressed
            "truncation_rate": 0.05,  # improved
            "autopilot_low_lift_rate": 0.5,  # regressed
            "bundle_bytes_p90": 80000,  # improved
            "should_autopilot_count": 5,  # regressed
        }
        result = compare_kpis(before, after)
        self.assertIn(result["verdict"], ("mixed", "improved", "regressed"))

    def test_noise_threshold(self):
        before = {
            "total": 10,
            "confidence_final_mean": 0.6,
            "confidence_delta_mean": 0.15,
            "truncation_rate": 0.2,
            "autopilot_low_lift_rate": 0.3,
            "bundle_bytes_p90": 150000,
            "should_autopilot_count": 3,
        }
        # Tiny changes below noise threshold
        after = {
            "total": 10,
            "confidence_final_mean": 0.601,
            "confidence_delta_mean": 0.151,
            "truncation_rate": 0.201,
            "autopilot_low_lift_rate": 0.301,
            "bundle_bytes_p90": 150100,
            "should_autopilot_count": 3,
        }
        result = compare_kpis(before, after)
        self.assertEqual(result["verdict"], "unchanged")

    def test_per_kpi_details(self):
        before = {
            "total": 10,
            "confidence_final_mean": 0.5,
            "truncation_rate": 0.3,
        }
        after = {
            "total": 10,
            "confidence_final_mean": 0.7,
            "truncation_rate": 0.1,
        }
        result = compare_kpis(before, after)
        kpis = result["kpis"]
        self.assertIn("confidence_final_mean", kpis)
        cfm = kpis["confidence_final_mean"]
        self.assertEqual(cfm["before"], 0.5)
        self.assertEqual(cfm["after"], 0.7)
        self.assertAlmostEqual(cfm["delta"], 0.2, places=3)
        self.assertEqual(cfm["direction"], "improved")


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------


class TestEvaluate(unittest.TestCase):
    def test_full_evaluation(self):
        before = _make_corpus(10, confidence_final=0.5)
        after = _make_corpus(10, confidence_final=0.75)

        result = evaluate(before, after)
        self.assertIn("before", result)
        self.assertIn("after", result)
        self.assertIn("comparison", result)
        self.assertEqual(result["before"]["total"], 10)
        self.assertEqual(result["after"]["total"], 10)

    def test_empty_before(self):
        result = evaluate([], _make_corpus(5))
        self.assertEqual(result["comparison"]["verdict"], "no_data")

    def test_empty_after(self):
        result = evaluate(_make_corpus(5), [])
        self.assertEqual(result["comparison"]["verdict"], "no_data")


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderText(unittest.TestCase):
    def test_render(self):
        before = _make_corpus(10, confidence_final=0.5)
        after = _make_corpus(10, confidence_final=0.8)
        result = evaluate(before, after)
        output = render_evaluation_text(result)
        self.assertIn("TUNING EVALUATION REPORT", output)
        self.assertIn("Verdict:", output)
        self.assertIn("Before:", output)


class TestRenderJson(unittest.TestCase):
    def test_valid_json(self):
        before = _make_corpus(5)
        after = _make_corpus(5)
        result = evaluate(before, after)
        output = render_evaluation_json(result)
        parsed = json.loads(output)
        self.assertIn("comparison", parsed)


class TestRenderMarkdown(unittest.TestCase):
    def test_markdown_format(self):
        before = _make_corpus(5, confidence_final=0.4)
        after = _make_corpus(5, confidence_final=0.8)
        result = evaluate(before, after)
        output = render_evaluation_markdown(result)
        self.assertIn("# Tuning Evaluation Report", output)
        self.assertIn("**Verdict:**", output)
        self.assertIn("| KPI |", output)


if __name__ == "__main__":
    unittest.main()
