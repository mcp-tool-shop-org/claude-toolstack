"""Tests for baseline capture command."""

from __future__ import annotations

import json
import time

import pytest

from cts.corpus.baseline import (
    BASELINE_VERSION,
    capture_baseline,
    render_baseline_json,
    render_baseline_markdown,
    render_baseline_text,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_corpus_file(path: str, records: list) -> str:
    """Write records as JSONL and return the path."""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def _sample_records(n: int = 10, mode: str = "error") -> list:
    """Generate n synthetic corpus records."""
    records = []
    for i in range(n):
        records.append(
            {
                "schema_version": 1,
                "repo": "org/repo-a" if i % 2 == 0 else "org/repo-b",
                "mode": mode,
                "created_at": time.time() - (n - i) * 100,
                "request_id": f"req-{i}",
                "passes_count": 1 if i % 3 == 0 else 0,
                "confidence_pass1": 0.5 + i * 0.02,
                "confidence_final": 0.6 + i * 0.02,
                "confidence_delta": 0.1,
                "actions": [],
                "bundle_bytes_final": 20000 + i * 1000,
                "section_bytes": {},
                "truncation_flags": {"truncated": i % 5 == 0},
                "timings_ms": {},
            }
        )
    return records


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCaptureBaseline:
    def test_basic_capture(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(10),
        )
        result = capture_baseline(corpus, label="test-baseline")

        assert result["baseline_version"] == BASELINE_VERSION
        assert result["label"] == "test-baseline"
        assert result["corpus_records"] == 10
        assert result["corpus_hash"]  # non-empty
        assert result["created_at"] > 0
        assert "kpis" in result
        assert "distributions" in result
        assert "tool_versions" in result

    def test_kpis_populated(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(10),
        )
        result = capture_baseline(corpus)

        kpis = result["kpis"]
        assert kpis["total"] == 10
        assert "confidence_final_mean" in kpis
        assert "truncation_rate" in kpis
        assert "bundle_bytes_p90" in kpis
        assert kpis["confidence_final_mean"] > 0

    def test_distributions_populated(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(10),
        )
        result = capture_baseline(corpus)

        dists = result["distributions"]
        assert "confidence_final" in dists
        assert "confidence_delta" in dists
        assert "bundle_bytes" in dists

        cf = dists["confidence_final"]
        assert cf["count"] == 10
        assert "p50" in cf
        assert "p90" in cf
        assert "mean" in cf

    def test_tool_versions_captured(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(5),
        )
        result = capture_baseline(corpus)

        tv = result["tool_versions"]
        assert "cts" in tv
        assert "bundle_schema" in tv
        assert "experiment_schema" in tv
        assert "tuning_schema" in tv

    def test_corpus_hash_is_deterministic(self, tmp_path: str) -> None:
        records = _sample_records(5)
        corpus = _make_corpus_file(str(tmp_path / "corpus.jsonl"), records)
        r1 = capture_baseline(corpus)
        r2 = capture_baseline(corpus)
        assert r1["corpus_hash"] == r2["corpus_hash"]

    def test_corpus_hash_changes_with_content(self, tmp_path: str) -> None:
        corpus1 = _make_corpus_file(str(tmp_path / "c1.jsonl"), _sample_records(5))
        corpus2 = _make_corpus_file(str(tmp_path / "c2.jsonl"), _sample_records(3))
        r1 = capture_baseline(corpus1)
        r2 = capture_baseline(corpus2)
        assert r1["corpus_hash"] != r2["corpus_hash"]

    def test_mode_filter(self, tmp_path: str) -> None:
        records = _sample_records(5, mode="error") + _sample_records(5, mode="symbol")
        corpus = _make_corpus_file(str(tmp_path / "corpus.jsonl"), records)
        result = capture_baseline(corpus, mode_filter="symbol")
        assert result["corpus_records"] == 5
        assert result["filters"]["mode"] == "symbol"

    def test_repo_filter(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(10),
        )
        result = capture_baseline(corpus, repo_filter="org/repo-a")
        assert result["corpus_records"] == 5  # every other record
        assert result["filters"]["repo"] == "org/repo-a"

    def test_since_days_filter(self, tmp_path: str) -> None:
        records = _sample_records(10)
        # Make some records very old
        for i in range(5):
            records[i]["created_at"] = time.time() - 86400 * 30
        corpus = _make_corpus_file(str(tmp_path / "corpus.jsonl"), records)
        result = capture_baseline(corpus, since_days=1)
        assert result["corpus_records"] == 5
        assert result["filters"]["since_days"] == 1

    def test_empty_corpus(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(str(tmp_path / "empty.jsonl"), [])
        result = capture_baseline(corpus)
        assert result["corpus_records"] == 0
        assert result["kpis"]["total"] == 0
        assert result["distributions"]["confidence_final"] == {}

    def test_no_filters_means_empty_filters_dict(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(3),
        )
        result = capture_baseline(corpus)
        assert result["filters"] == {}

    def test_json_serializable(self, tmp_path: str) -> None:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(5),
        )
        result = capture_baseline(corpus)
        # Should not raise
        serialized = json.dumps(result, default=str)
        parsed = json.loads(serialized)
        assert parsed["baseline_version"] == BASELINE_VERSION


# ---------------------------------------------------------------------------
# Renderer tests
# ---------------------------------------------------------------------------


class TestRenderers:
    @pytest.fixture()
    def sample_baseline(self, tmp_path: str) -> dict:
        corpus = _make_corpus_file(
            str(tmp_path / "corpus.jsonl"),
            _sample_records(10),
        )
        return capture_baseline(corpus, label="test-render")

    def test_text_output(self, sample_baseline: dict) -> None:
        text = render_baseline_text(sample_baseline)
        assert "BASELINE SNAPSHOT" in text
        assert "test-render" in text
        assert "confidence_final_mean" in text

    def test_json_roundtrip(self, sample_baseline: dict) -> None:
        output = render_baseline_json(sample_baseline)
        parsed = json.loads(output)
        assert parsed["label"] == "test-render"
        assert parsed["baseline_version"] == BASELINE_VERSION

    def test_markdown_structure(self, sample_baseline: dict) -> None:
        md = render_baseline_markdown(sample_baseline)
        assert "# Baseline Snapshot" in md
        assert "## KPIs" in md
        assert "## Distributions" in md
        assert "## Tool versions" in md
        assert "test-render" in md

    def test_markdown_contains_kpi_table(self, sample_baseline: dict) -> None:
        md = render_baseline_markdown(sample_baseline)
        assert "| KPI | Value |" in md
        assert "confidence_final_mean" in md

    def test_text_empty_baseline(self) -> None:
        baseline = {
            "label": "empty",
            "corpus_records": 0,
            "corpus_hash": "abc123",
            "kpis": {"total": 0},
            "distributions": {},
        }
        text = render_baseline_text(baseline)
        assert "empty" in text
        assert "Records:  0" in text
