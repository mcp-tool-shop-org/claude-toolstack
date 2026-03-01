"""Tests for candidate narrowing (Phase 4.2)."""

from __future__ import annotations

from cts.semantic.candidates import (
    CandidateSelection,
    select_candidates,
)
from cts.semantic.config import SemanticConfig, load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sources(n: int) -> list:
    """Create ranked_sources with n unique file paths."""
    return [
        {"path": f"src/file_{i:03d}.py", "score": 1.0 - i * 0.05}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# CandidateSelection dataclass
# ---------------------------------------------------------------------------


class TestCandidateSelection:
    def test_to_dict_shape(self):
        cs = CandidateSelection(
            strategy="exclude_top_k",
            allowed_paths=["a.py", "b.py"],
            excluded_top_k=5,
            candidate_files=2,
            excluded_files_sample=["top.py"],
            candidate_rules_hit=["excluded top 5"],
        )
        d = cs.to_dict()
        assert d["strategy"] == "exclude_top_k"
        assert d["excluded_top_k"] == 5
        assert d["candidate_files"] == 2
        assert isinstance(d["excluded_files_sample"], list)
        assert isinstance(d["candidate_rules_hit"], list)

    def test_to_dict_no_allowed_paths(self):
        """allowed_paths is not in to_dict (it's operational, not debug)."""
        cs = CandidateSelection(strategy="none", allowed_paths=[])
        d = cs.to_dict()
        assert "allowed_paths" not in d


# ---------------------------------------------------------------------------
# select_candidates — strategy=none
# ---------------------------------------------------------------------------


class TestStrategyNone:
    def test_returns_empty_paths(self):
        result = select_candidates(
            _make_sources(20), strategy="none"
        )
        assert result.strategy == "none"
        assert result.allowed_paths == []
        assert result.candidate_files == 0

    def test_rules_hit_says_no_narrowing(self):
        result = select_candidates(
            _make_sources(5), strategy="none"
        )
        assert "no narrowing" in result.candidate_rules_hit


# ---------------------------------------------------------------------------
# select_candidates — strategy=exclude_top_k
# ---------------------------------------------------------------------------


class TestExcludeTopK:
    def test_excludes_top_k_files(self):
        sources = _make_sources(20)
        result = select_candidates(sources, exclude_top_k=5)
        top_5 = [f"src/file_{i:03d}.py" for i in range(5)]
        for path in top_5:
            assert path not in result.allowed_paths

    def test_candidates_are_remaining(self):
        sources = _make_sources(15)
        result = select_candidates(sources, exclude_top_k=5)
        assert result.candidate_files == 10
        assert len(result.allowed_paths) == 10

    def test_excluded_files_sample_capped_at_10(self):
        sources = _make_sources(50)
        result = select_candidates(sources, exclude_top_k=20)
        assert len(result.excluded_files_sample) <= 10

    def test_max_files_cap(self):
        sources = _make_sources(300)
        result = select_candidates(
            sources, exclude_top_k=5, max_files=50
        )
        assert len(result.allowed_paths) <= 50
        assert result.candidate_files <= 50

    def test_fewer_sources_than_k(self):
        """When there are fewer sources than K, exclude all."""
        sources = _make_sources(3)
        result = select_candidates(sources, exclude_top_k=10)
        assert result.excluded_top_k == 3
        assert result.candidate_files == 0
        assert result.allowed_paths == []

    def test_empty_sources(self):
        result = select_candidates([], exclude_top_k=10)
        assert result.strategy == "exclude_top_k"
        assert result.candidate_files == 0
        assert result.allowed_paths == []

    def test_duplicate_paths_deduplicated(self):
        """Sources with duplicate paths produce unique candidates."""
        sources = [
            {"path": "a.py", "score": 1.0},
            {"path": "a.py", "score": 0.9},
            {"path": "b.py", "score": 0.8},
            {"path": "c.py", "score": 0.7},
        ]
        result = select_candidates(sources, exclude_top_k=1)
        # a.py excluded (top 1), b.py and c.py are candidates
        assert "a.py" not in result.allowed_paths
        assert "b.py" in result.allowed_paths
        assert "c.py" in result.allowed_paths
        assert result.candidate_files == 2


# ---------------------------------------------------------------------------
# Path filters
# ---------------------------------------------------------------------------


class TestPathFilters:
    def test_avoid_paths(self):
        sources = [
            {"path": "src/core.py", "score": 1.0},
            {"path": "vendor/lib.py", "score": 0.8},
            {"path": "src/util.py", "score": 0.6},
            {"path": "test/test_core.py", "score": 0.5},
        ]
        result = select_candidates(
            sources,
            exclude_top_k=1,
            avoid_paths=["vendor/", "test/"],
        )
        assert "vendor/lib.py" not in result.allowed_paths
        assert "test/test_core.py" not in result.allowed_paths
        assert "src/util.py" in result.allowed_paths

    def test_prefer_paths(self):
        sources = [
            {"path": "top.py", "score": 1.0},  # excluded (top 1)
            {"path": "src/a.py", "score": 0.8},
            {"path": "docs/b.md", "score": 0.7},
            {"path": "src/c.py", "score": 0.6},
        ]
        result = select_candidates(
            sources,
            exclude_top_k=1,
            prefer_paths=["src/"],
        )
        assert "src/a.py" in result.allowed_paths
        assert "src/c.py" in result.allowed_paths
        assert "docs/b.md" not in result.allowed_paths

    def test_prefer_falls_back_when_no_match(self):
        """If prefer_paths matches nothing, keep all candidates."""
        sources = [
            {"path": "top.py", "score": 1.0},
            {"path": "lib/a.py", "score": 0.8},
            {"path": "lib/b.py", "score": 0.6},
        ]
        result = select_candidates(
            sources,
            exclude_top_k=1,
            prefer_paths=["nonexistent/"],
        )
        # No preferred match → keep all remaining
        assert result.candidate_files == 2

    def test_avoid_and_prefer_combined(self):
        sources = [
            {"path": "top.py", "score": 1.0},
            {"path": "src/a.py", "score": 0.9},
            {"path": "vendor/b.py", "score": 0.8},
            {"path": "src/c.py", "score": 0.7},
            {"path": "docs/d.md", "score": 0.6},
        ]
        result = select_candidates(
            sources,
            exclude_top_k=1,
            prefer_paths=["src/"],
            avoid_paths=["vendor/"],
        )
        assert "vendor/b.py" not in result.allowed_paths
        assert "docs/d.md" not in result.allowed_paths
        assert set(result.allowed_paths) == {"src/a.py", "src/c.py"}


# ---------------------------------------------------------------------------
# Debug metadata
# ---------------------------------------------------------------------------


class TestDebugMetadata:
    def test_rules_hit_tracks_exclusion(self):
        result = select_candidates(_make_sources(20), exclude_top_k=5)
        assert any("excluded top 5" in r for r in result.candidate_rules_hit)

    def test_rules_hit_tracks_cap(self):
        result = select_candidates(
            _make_sources(100), exclude_top_k=5, max_files=10
        )
        assert any("capped" in r for r in result.candidate_rules_hit)

    def test_excluded_files_sample_contains_top_paths(self):
        sources = _make_sources(20)
        result = select_candidates(sources, exclude_top_k=5)
        expected_top = [f"src/file_{i:03d}.py" for i in range(5)]
        assert result.excluded_files_sample == expected_top


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


class TestCandidateConfig:
    def test_defaults_exclude_top_k(self):
        cfg = SemanticConfig()
        assert cfg.candidate_strategy == "exclude_top_k"
        assert cfg.candidate_exclude_top_k == 10
        assert cfg.candidate_max_files == 200
        assert cfg.candidate_max_chunks == 20000
        assert cfg.candidate_fallback == "global_tight"

    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("CTS_SEMANTIC_CANDIDATE_STRATEGY", "exclude_top_k")
        monkeypatch.setenv("CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K", "15")
        monkeypatch.setenv("CTS_SEMANTIC_CANDIDATE_MAX_FILES", "100")
        monkeypatch.setenv("CTS_SEMANTIC_CANDIDATE_MAX_CHUNKS", "10000")
        monkeypatch.setenv("CTS_SEMANTIC_CANDIDATE_FALLBACK", "skip")
        cfg = load_config()
        assert cfg.candidate_strategy == "exclude_top_k"
        assert cfg.candidate_exclude_top_k == 15
        assert cfg.candidate_max_files == 100
        assert cfg.candidate_max_chunks == 10000
        assert cfg.candidate_fallback == "skip"

    def test_load_override(self):
        cfg = load_config(
            candidate_strategy="exclude_top_k",
            candidate_exclude_top_k=20,
        )
        assert cfg.candidate_strategy == "exclude_top_k"
        assert cfg.candidate_exclude_top_k == 20
