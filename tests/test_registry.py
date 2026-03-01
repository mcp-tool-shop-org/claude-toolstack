"""Tests for cts.corpus.registry — experiment registry indexer."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from cts.corpus.archive import archive_experiment
from cts.corpus.registry import (
    filter_entries,
    find_experiment_dir,
    render_list_json,
    render_list_markdown,
    render_list_text,
    render_show_text,
    scan_registry,
    show_experiment,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _archive_full(
    tmpdir: str,
    exp_id: str,
    *,
    winner: str = "B",
    verdict: str = "winner",
    description: str = "",
    hypothesis: str = "",
    primary_kpi: str = "confidence_final_mean",
    created_at: float = 0.0,
) -> str:
    """Create and archive a complete experiment. Returns exp_dir."""
    src = os.path.join(tmpdir, f"src_{exp_id}")
    os.makedirs(src, exist_ok=True)

    exp_path = os.path.join(src, "experiment.json")
    _write_json(
        exp_path,
        {
            "experiment_schema_version": 1,
            "id": exp_id,
            "created_at": created_at or time.time(),
            "description": description,
            "hypothesis": hypothesis,
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {
                "primary_kpi": primary_kpi,
                "constraints": [],
            },
        },
    )

    result_path = os.path.join(src, "result.json")
    _write_json(
        result_path,
        {
            "experiment_id": exp_id,
            "winner": winner,
            "verdict": verdict,
            "reasoning": f"{winner} wins",
            "per_variant": {
                "A": {
                    "total": 10,
                    "kpis": {"confidence_final_mean": 0.5},
                },
                "B": {
                    "total": 10,
                    "kpis": {"confidence_final_mean": 0.8},
                },
            },
        },
    )

    registry = os.path.join(tmpdir, "experiments")
    summary = archive_experiment(
        experiment_path=exp_path,
        result_path=result_path,
        registry_root=registry,
    )
    return summary["exp_dir"]


# ---------------------------------------------------------------------------
# scan_registry
# ---------------------------------------------------------------------------


class TestScanRegistry(unittest.TestCase):
    def test_empty_registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = scan_registry(os.path.join(tmpdir, "experiments"))
            self.assertEqual(entries, [])

    def test_nonexistent_root(self):
        entries = scan_registry("/nonexistent/path")
        self.assertEqual(entries, [])

    def test_single_experiment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _archive_full(tmpdir, "exp-001", description="Test experiment")
            entries = scan_registry(os.path.join(tmpdir, "experiments"))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["exp_id"], "exp-001")
            self.assertEqual(entries[0]["description"], "Test experiment")

    def test_multiple_experiments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _archive_full(tmpdir, "exp-a", created_at=1000.0)
            _archive_full(tmpdir, "exp-b", created_at=2000.0)
            _archive_full(tmpdir, "exp-c", created_at=3000.0)

            entries = scan_registry(os.path.join(tmpdir, "experiments"))
            self.assertEqual(len(entries), 3)
            # Sorted by created_at descending
            self.assertEqual(entries[0]["exp_id"], "exp-c")
            self.assertEqual(entries[2]["exp_id"], "exp-a")

    def test_result_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _archive_full(tmpdir, "exp-res", winner="B", verdict="winner")
            entries = scan_registry(os.path.join(tmpdir, "experiments"))
            e = entries[0]
            self.assertEqual(e["winner"], "B")
            self.assertEqual(e["verdict"], "winner")
            self.assertEqual(e["run_count"], 1)

    def test_variant_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _archive_full(tmpdir, "exp-vars")
            entries = scan_registry(os.path.join(tmpdir, "experiments"))
            self.assertEqual(entries[0]["variant_names"], ["A", "B"])


# ---------------------------------------------------------------------------
# filter_entries
# ---------------------------------------------------------------------------


class TestFilterEntries(unittest.TestCase):
    def _entries(self):
        return [
            {
                "exp_id": "exp-1",
                "winner": "A",
                "verdict": "winner",
                "created_at": time.time() - 86400 * 5,
                "primary_kpi": "confidence_final_mean",
                "description": "truncation test",
                "hypothesis": "reducing truncation helps",
            },
            {
                "exp_id": "exp-2",
                "winner": "B",
                "verdict": "tie",
                "created_at": time.time() - 86400 * 60,
                "primary_kpi": "bundle_bytes_p90",
                "description": "bundle optimization",
                "hypothesis": "smaller bundles",
            },
            {
                "exp_id": "exp-3",
                "winner": None,
                "verdict": "no_data",
                "created_at": time.time() - 86400 * 2,
                "primary_kpi": "confidence_final_mean",
                "description": "autopilot tuning",
                "hypothesis": "",
            },
        ]

    def test_filter_by_winner(self):
        result = filter_entries(self._entries(), winner="A")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["exp_id"], "exp-1")

    def test_filter_by_verdict(self):
        result = filter_entries(self._entries(), verdict="tie")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["exp_id"], "exp-2")

    def test_filter_by_since_days(self):
        result = filter_entries(self._entries(), since_days=30)
        # exp-1 (5 days ago) and exp-3 (2 days ago) match
        ids = [e["exp_id"] for e in result]
        self.assertIn("exp-1", ids)
        self.assertIn("exp-3", ids)
        self.assertNotIn("exp-2", ids)

    def test_filter_by_primary_kpi(self):
        result = filter_entries(self._entries(), primary_kpi="bundle_bytes_p90")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["exp_id"], "exp-2")

    def test_filter_contains(self):
        result = filter_entries(self._entries(), contains="truncation")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["exp_id"], "exp-1")

    def test_filter_combined(self):
        result = filter_entries(
            self._entries(),
            winner="A",
            primary_kpi="confidence_final_mean",
        )
        self.assertEqual(len(result), 1)

    def test_filter_no_match(self):
        result = filter_entries(self._entries(), winner="Z")
        self.assertEqual(len(result), 0)


# ---------------------------------------------------------------------------
# show_experiment / find_experiment_dir
# ---------------------------------------------------------------------------


class TestShowExperiment(unittest.TestCase):
    def test_show_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_dir = _archive_full(
                tmpdir,
                "exp-show",
                description="Show test",
                winner="B",
            )
            detail = show_experiment(exp_dir)
            self.assertIsNotNone(detail)
            self.assertEqual(detail["experiment"]["id"], "exp-show")
            self.assertEqual(len(detail["results"]), 1)
            self.assertEqual(detail["results"][0]["winner"], "B")

    def test_show_nonexistent(self):
        detail = show_experiment("/nonexistent")
        self.assertIsNone(detail)

    def test_find_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _archive_full(tmpdir, "exp-find")
            registry = os.path.join(tmpdir, "experiments")
            found = find_experiment_dir("exp-find", root=registry)
            self.assertIsNotNone(found)
            self.assertTrue(found.endswith("exp-find"))

    def test_find_dir_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            found = find_experiment_dir(
                "nonexistent", root=os.path.join(tmpdir, "experiments")
            )
            self.assertIsNone(found)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderers(unittest.TestCase):
    def _entries(self):
        return [
            {
                "exp_id": "exp-1",
                "winner": "B",
                "verdict": "winner",
                "run_count": 2,
                "primary_kpi": "confidence_final_mean",
                "description": "Test experiment",
            }
        ]

    def test_render_text(self):
        output = render_list_text(self._entries())
        self.assertIn("exp-1", output)
        self.assertIn("winner", output)
        self.assertIn("Total: 1", output)

    def test_render_text_empty(self):
        output = render_list_text([])
        self.assertIn("No experiments", output)

    def test_render_json(self):
        output = render_list_json(self._entries())
        data = json.loads(output)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["experiments"][0]["exp_id"], "exp-1")

    def test_render_markdown(self):
        output = render_list_markdown(self._entries())
        self.assertIn("# Experiment Registry", output)
        self.assertIn("| exp-1 |", output)

    def test_render_markdown_empty(self):
        output = render_list_markdown([])
        self.assertIn("No experiments", output)


class TestRenderShow(unittest.TestCase):
    def test_render_show_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_dir = _archive_full(
                tmpdir,
                "exp-render",
                description="Render test",
                hypothesis="Should improve",
            )
            detail = show_experiment(exp_dir)
            output = render_show_text(detail)
            self.assertIn("EXPERIMENT: exp-render", output)
            self.assertIn("Render test", output)
            self.assertIn("Should improve", output)


if __name__ == "__main__":
    unittest.main()
