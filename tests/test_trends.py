"""Tests for trend analytics and dashboard generation."""

from __future__ import annotations

import json
import os
import time

import pytest

from cts.corpus.trends import (
    compute_kpi_trends,
    compute_regressions,
    compute_win_rates,
    compute_winning_knobs,
    extract_data_points,
    generate_dashboard,
    render_dashboard_json,
    render_dashboard_markdown,
    render_dashboard_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    exp_id: str = "exp-1",
    verdict: str = "winner",
    winner: str | None = "B",
    primary_kpi: str = "confidence_final_mean",
    kpi_summary: dict | None = None,
    created_at: float = 1700000000.0,
    exp_dir: str = "",
    constraints: list | None = None,
    variant_names: list | None = None,
    assignment_mode: str = "manual",
) -> dict:
    return {
        "exp_id": exp_id,
        "verdict": verdict,
        "winner": winner,
        "primary_kpi": primary_kpi,
        "kpi_summary": kpi_summary or {},
        "created_at": created_at,
        "exp_dir": exp_dir,
        "constraints": constraints or [],
        "variant_names": variant_names or ["A", "B"],
        "assignment_mode": assignment_mode,
    }


def _make_tuning_file(
    variants_dir: str, vname: str, strategy: str, targets: list | None = None
) -> None:
    """Create a tuning_<vname>.json with strategy and optional targets."""
    os.makedirs(variants_dir, exist_ok=True)
    recs = [{"target": t} for t in (targets or [])]
    data = {
        "variant_metadata": {"strategy": strategy},
        "recommendations": recs,
    }
    path = os.path.join(variants_dir, f"tuning_{vname}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# extract_data_points
# ---------------------------------------------------------------------------


class TestExtractDataPoints:
    def test_empty_entries(self) -> None:
        assert extract_data_points([]) == []

    def test_skips_entries_without_verdict(self) -> None:
        entries = [_make_entry(verdict=""), _make_entry(verdict="winner")]
        points = extract_data_points(entries)
        assert len(points) == 1
        assert points[0]["exp_id"] == "exp-1"

    def test_basic_extraction(self) -> None:
        entry = _make_entry(
            exp_id="test-exp",
            verdict="winner",
            winner="A",
            primary_kpi="truncation_rate",
            kpi_summary={"A_truncation_rate": 0.01},
            created_at=1700000100.0,
        )
        points = extract_data_points([entry])
        assert len(points) == 1
        p = points[0]
        assert p["exp_id"] == "test-exp"
        assert p["verdict"] == "winner"
        assert p["winner"] == "A"
        assert p["primary_kpi"] == "truncation_rate"
        assert p["kpi_summary"]["A_truncation_rate"] == 0.01
        assert p["created_at"] == 1700000100.0

    def test_extracts_strategies_from_disk(self, tmp_path: str) -> None:
        exp_dir = str(tmp_path / "exp1")
        variants_dir = os.path.join(exp_dir, "variants")
        _make_tuning_file(variants_dir, "A", "conservative")
        _make_tuning_file(variants_dir, "B", "aggressive")

        entry = _make_entry(exp_dir=exp_dir)
        points = extract_data_points([entry])
        assert points[0]["strategies"] == {
            "A": "conservative",
            "B": "aggressive",
        }

    def test_extracts_winning_targets(self, tmp_path: str) -> None:
        exp_dir = str(tmp_path / "exp2")
        variants_dir = os.path.join(exp_dir, "variants")
        _make_tuning_file(
            variants_dir, "B", "aggressive", targets=["max_tokens", "temperature"]
        )

        entry = _make_entry(exp_dir=exp_dir, winner="B")
        points = extract_data_points([entry])
        assert points[0]["winning_targets"] == ["max_tokens", "temperature"]

    def test_no_winning_targets_without_winner(self, tmp_path: str) -> None:
        exp_dir = str(tmp_path / "exp3")
        variants_dir = os.path.join(exp_dir, "variants")
        _make_tuning_file(variants_dir, "A", "conservative", targets=["x"])

        entry = _make_entry(exp_dir=exp_dir, winner=None, verdict="tie")
        points = extract_data_points([entry])
        assert "winning_targets" not in points[0]


# ---------------------------------------------------------------------------
# compute_win_rates
# ---------------------------------------------------------------------------


class TestComputeWinRates:
    def test_empty(self) -> None:
        result = compute_win_rates([])
        assert result["total_experiments"] == 0
        assert result["by_strategy"] == {}

    def test_no_strategy_data(self) -> None:
        points = [
            {"winner": "A", "strategies": {}},
            {"winner": "B"},
        ]
        result = compute_win_rates(points)
        assert result["total_experiments"] == 0

    def test_basic_win_rates(self) -> None:
        points = [
            {
                "winner": "B",
                "strategies": {"A": "conservative", "B": "aggressive"},
            },
            {
                "winner": "B",
                "strategies": {"A": "conservative", "B": "aggressive"},
            },
            {
                "winner": "A",
                "strategies": {"A": "conservative", "B": "aggressive"},
            },
        ]
        result = compute_win_rates(points)
        assert result["total_experiments"] == 3
        by_s = result["by_strategy"]
        assert by_s["aggressive"]["wins"] == 2
        assert by_s["aggressive"]["total"] == 3
        assert by_s["aggressive"]["rate"] == pytest.approx(0.667, abs=0.001)
        assert by_s["conservative"]["wins"] == 1
        assert by_s["conservative"]["total"] == 3
        assert by_s["conservative"]["rate"] == pytest.approx(0.333, abs=0.001)

    def test_mixed_strategies(self) -> None:
        points = [
            {
                "winner": "A",
                "strategies": {"A": "focused", "B": "aggressive"},
            },
            {
                "winner": None,
                "strategies": {"A": "focused", "B": "aggressive"},
            },
        ]
        result = compute_win_rates(points)
        assert result["by_strategy"]["focused"]["wins"] == 1
        assert result["by_strategy"]["focused"]["total"] == 2
        assert result["by_strategy"]["aggressive"]["wins"] == 0


# ---------------------------------------------------------------------------
# compute_kpi_trends
# ---------------------------------------------------------------------------


class TestComputeKpiTrends:
    def test_empty(self) -> None:
        result = compute_kpi_trends([])
        assert result["values"] == []
        assert result["window"] == 5

    def test_no_winners(self) -> None:
        points = [{"winner": None, "kpi_summary": {}, "primary_kpi": "x"}]
        result = compute_kpi_trends(points)
        assert result["values"] == []

    def test_basic_rolling_average(self) -> None:
        points = []
        for i in range(5):
            points.append(
                {
                    "exp_id": f"exp-{i}",
                    "created_at": 1700000000.0 + i * 100,
                    "winner": "A",
                    "primary_kpi": "confidence_final_mean",
                    "kpi_summary": {"A_confidence_final_mean": 0.7 + i * 0.01},
                }
            )
        result = compute_kpi_trends(points, window=3)
        vals = result["values"]
        assert len(vals) == 5
        # First point: rolling avg = itself
        assert vals[0]["rolling_avg"] == pytest.approx(0.7, abs=0.001)
        # Third point: rolling avg of 0.70, 0.71, 0.72
        assert vals[2]["rolling_avg"] == pytest.approx(0.71, abs=0.001)

    def test_custom_window(self) -> None:
        points = [
            {
                "exp_id": f"e{i}",
                "created_at": float(i),
                "winner": "B",
                "primary_kpi": "truncation_rate",
                "kpi_summary": {"B_truncation_rate": 0.05},
            }
            for i in range(3)
        ]
        result = compute_kpi_trends(points, window=10)
        assert result["window"] == 10
        # All values same, rolling avg should match
        for v in result["values"]:
            assert v["rolling_avg"] == pytest.approx(0.05, abs=0.001)

    def test_skips_missing_kpi_value(self) -> None:
        points = [
            {
                "exp_id": "e1",
                "created_at": 1.0,
                "winner": "A",
                "primary_kpi": "confidence_final_mean",
                "kpi_summary": {},  # Missing value
            },
        ]
        result = compute_kpi_trends(points)
        assert result["values"] == []


# ---------------------------------------------------------------------------
# compute_winning_knobs
# ---------------------------------------------------------------------------


class TestComputeWinningKnobs:
    def test_empty(self) -> None:
        assert compute_winning_knobs([]) == []

    def test_counts_targets(self) -> None:
        points = [
            {"winner": "A", "winning_targets": ["max_tokens", "temperature"]},
            {"winner": "B", "winning_targets": ["max_tokens", "top_p"]},
            {"winner": "A", "winning_targets": ["max_tokens"]},
        ]
        result = compute_winning_knobs(points)
        assert result[0]["target"] == "max_tokens"
        assert result[0]["wins"] == 3
        assert len(result) == 3

    def test_top_n_limit(self) -> None:
        points = [
            {"winner": "A", "winning_targets": [f"t{i}" for i in range(20)]},
        ]
        result = compute_winning_knobs(points, top_n=5)
        assert len(result) == 5

    def test_skips_no_winner(self) -> None:
        points = [
            {"winner": None, "winning_targets": ["x"]},
            {"winner": "A", "winning_targets": ["y"]},
        ]
        result = compute_winning_knobs(points)
        assert len(result) == 1
        assert result[0]["target"] == "y"


# ---------------------------------------------------------------------------
# compute_regressions
# ---------------------------------------------------------------------------


class TestComputeRegressions:
    def test_empty(self) -> None:
        result = compute_regressions([])
        assert result["regression_count"] == 0
        assert result["verdict_counts"] == {}
        assert result["constraint_kpis"] == []

    def test_counts_verdicts(self) -> None:
        entries = [
            {"verdict": "winner"},
            {"verdict": "winner"},
            {"verdict": "tie"},
            {"verdict": "no_data"},
        ]
        result = compute_regressions(entries)
        assert result["verdict_counts"]["winner"] == 2
        assert result["verdict_counts"]["tie"] == 1
        assert result["verdict_counts"]["no_data"] == 1

    def test_counts_all_constraints_violated(self) -> None:
        entries = [
            {"verdict": "winner_all_constraints_violated"},
            {"verdict": "tie_all_constraints_violated"},
            {"verdict": "winner"},
        ]
        result = compute_regressions(entries)
        assert result["regression_count"] == 2

    def test_extracts_constraint_kpis(self) -> None:
        entries = [
            {
                "verdict": "winner",
                "constraints": [
                    {"kpi": "truncation_rate", "operator": "<="},
                    {"kpi": "bundle_bytes_p90", "operator": "<="},
                ],
            },
        ]
        result = compute_regressions(entries)
        assert "truncation_rate_<=" in result["constraint_kpis"]
        assert "bundle_bytes_p90_<=" in result["constraint_kpis"]


# ---------------------------------------------------------------------------
# generate_dashboard (integration test with on-disk registry)
# ---------------------------------------------------------------------------


class TestGenerateDashboard:
    def _setup_registry(self, root: str) -> None:
        """Create a minimal on-disk experiment registry."""
        for i in range(3):
            exp_dir = os.path.join(root, f"exp-{i}")
            os.makedirs(exp_dir, exist_ok=True)

            exp_data = {
                "id": f"exp-{i}",
                "created_at": time.time() - (i * 86400),
                "description": f"Experiment {i}",
                "hypothesis": "Testing",
                "variants": [{"name": "A"}, {"name": "B"}],
                "assignment": {"mode": "manual"},
                "decision_rule": {
                    "primary_kpi": "confidence_final_mean",
                    "constraints": [],
                },
            }
            with open(os.path.join(exp_dir, "experiment.json"), "w") as f:
                json.dump(exp_data, f)

            # Add a result for the first two
            if i < 2:
                run_dir = os.path.join(exp_dir, "results", "run_001")
                os.makedirs(run_dir, exist_ok=True)
                result = {
                    "verdict": "winner" if i == 0 else "tie",
                    "winner": "B" if i == 0 else None,
                    "reasoning": "Test",
                    "per_variant": {
                        "A": {"kpis": {"confidence_final_mean": 0.70}},
                        "B": {"kpis": {"confidence_final_mean": 0.75}},
                    },
                }
                with open(os.path.join(run_dir, "result.json"), "w") as f:
                    json.dump(result, f)

    def test_full_dashboard(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        self._setup_registry(root)

        dashboard = generate_dashboard(root)
        assert dashboard["total_experiments"] == 3
        assert dashboard["with_results"] == 2
        assert "win_rates" in dashboard
        assert "kpi_trends" in dashboard
        assert "winning_knobs" in dashboard
        assert "regressions" in dashboard
        assert "recent" in dashboard
        assert len(dashboard["recent"]) <= 10

    def test_window_filter(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        self._setup_registry(root)

        # Only last 0.5 days — should exclude exp-1 and exp-2
        dashboard = generate_dashboard(root, window_days=0.5)
        assert dashboard["total_experiments"] <= 3

    def test_primary_kpi_filter(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        self._setup_registry(root)

        # Filter by non-existent KPI
        dashboard = generate_dashboard(root, primary_kpi="nonexistent")
        assert dashboard["total_experiments"] == 0

    def test_empty_registry(self, tmp_path: str) -> None:
        root = str(tmp_path / "empty")
        dashboard = generate_dashboard(root)
        assert dashboard["total_experiments"] == 0
        assert dashboard["with_results"] == 0


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderers:
    @pytest.fixture()
    def sample_dashboard(self) -> dict:
        return {
            "total_experiments": 5,
            "with_results": 3,
            "win_rates": {
                "total_experiments": 3,
                "by_strategy": {
                    "conservative": {"wins": 1, "total": 3, "rate": 0.333},
                    "aggressive": {"wins": 2, "total": 3, "rate": 0.667},
                },
            },
            "kpi_trends": {
                "values": [
                    {
                        "exp_id": "e1",
                        "created_at": 1.0,
                        "primary_kpi": "confidence_final_mean",
                        "value": 0.72,
                        "rolling_avg": 0.72,
                    },
                ],
                "window": 5,
            },
            "winning_knobs": [
                {"target": "max_tokens", "wins": 3},
                {"target": "temperature", "wins": 1},
            ],
            "regressions": {
                "regression_count": 1,
                "verdict_counts": {"winner": 2, "tie": 1},
                "constraint_kpis": ["truncation_rate_<="],
            },
            "recent": [
                {
                    "exp_id": "exp-1",
                    "verdict": "winner",
                    "winner": "B",
                    "primary_kpi": "confidence_final_mean",
                    "description": "Test experiment",
                },
            ],
        }

    def test_markdown_has_stable_headings(self, sample_dashboard: dict) -> None:
        md = render_dashboard_markdown(sample_dashboard)
        assert "# Experiment Trend Dashboard" in md
        assert "## Summary" in md
        assert "## Win rates" in md
        assert "## KPI trends" in md
        assert "## Common winning changes" in md
        assert "## Regressions / constraint failures" in md
        assert "## Recent experiments" in md

    def test_markdown_contains_data(self, sample_dashboard: dict) -> None:
        md = render_dashboard_markdown(sample_dashboard)
        assert "**Total experiments:** 5" in md
        assert "**With results:** 3" in md
        assert "conservative" in md
        assert "aggressive" in md
        assert "`max_tokens`" in md
        assert "exp-1" in md

    def test_json_roundtrips(self, sample_dashboard: dict) -> None:
        output = render_dashboard_json(sample_dashboard)
        parsed = json.loads(output)
        assert parsed["total_experiments"] == 5
        assert parsed["with_results"] == 3

    def test_text_output(self, sample_dashboard: dict) -> None:
        text = render_dashboard_text(sample_dashboard)
        assert "EXPERIMENT TREND DASHBOARD" in text
        assert "Total experiments:  5" in text
        assert "conservative:" in text
        assert "max_tokens:" in text

    def test_markdown_empty_data(self) -> None:
        dashboard = {
            "total_experiments": 0,
            "with_results": 0,
            "win_rates": {"by_strategy": {}},
            "kpi_trends": {"values": []},
            "winning_knobs": [],
            "regressions": {
                "regression_count": 0,
                "verdict_counts": {},
            },
            "recent": [],
        }
        md = render_dashboard_markdown(dashboard)
        assert "No strategy data available" in md
        assert "No KPI trend data available" in md
        assert "No winning change data available" in md
        assert "No experiments found" in md

    def test_text_empty_data(self) -> None:
        dashboard = {
            "total_experiments": 0,
            "with_results": 0,
            "win_rates": {"by_strategy": {}},
            "kpi_trends": {"values": []},
            "winning_knobs": [],
            "regressions": {"regression_count": 0},
            "recent": [],
        }
        text = render_dashboard_text(dashboard)
        assert "Total experiments:  0" in text
