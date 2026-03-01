"""Tests for candidate narrowing pipeline (Phase 4.2, Commits 2-5).

Covers:
  - Store filtered embedding retrieval with batching and cap
  - Narrowed search integration with fallback behavior
  - Corpus extraction of narrowing debug fields
  - Narrowing experiment template
"""

from __future__ import annotations

import json
import struct

from cts.corpus.experiment_schema import (
    create_narrowing_experiment,
    validate_experiment,
)
from cts.corpus.extract import extract_record
from cts.corpus.model import CorpusRecord
from cts.semantic.search import NarrowedSearchResult, narrowed_search
from cts.semantic.store import SemanticStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vec(dim: int, seed: float = 1.0) -> bytes:
    """Create a normalized float32 vector blob."""
    vec = [seed + i * 0.01 for i in range(dim)]
    mag = sum(x * x for x in vec) ** 0.5
    normalized = [x / mag for x in vec]
    return struct.pack(f"{dim}f", *normalized)


def _populate_store(store: SemanticStore, n_files: int, dim: int = 8):
    """Populate a store with test chunks and embeddings."""
    from cts.semantic.chunker import Chunk

    chunks = []
    for i in range(n_files):
        chunk = Chunk(
            chunk_id=f"chunk_{i:04d}",
            repo="test/repo",
            path=f"src/file_{i:03d}.py",
            start_line=1,
            end_line=180,
            content=f"# file {i}\ndef func_{i}(): pass\n",
            content_hash=f"hash_{i:04d}",
        )
        chunks.append(chunk)

    store.upsert_chunks(chunks)
    store.store_embeddings(
        [c.chunk_id for c in chunks],
        [_make_vec(dim, seed=float(i)) for i in range(n_files)],
        model="test-model",
        dim=dim,
    )
    return chunks


# ---------------------------------------------------------------------------
# Commit 2 — Store filtered retrieval
# ---------------------------------------------------------------------------


class TestStoreFilteredRetrieval:
    def test_returns_only_requested_paths(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 10)

        paths = ["src/file_002.py", "src/file_005.py"]
        rows, capped = store.get_embeddings_filtered(paths)
        result_paths = {r[1] for r in rows}
        assert result_paths == set(paths)
        assert not capped
        store.close()

    def test_empty_paths_returns_all(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 5)

        rows, capped = store.get_embeddings_filtered([])
        assert len(rows) == 5
        assert not capped
        store.close()

    def test_max_chunks_cap(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 20)

        rows, capped = store.get_embeddings_filtered([], max_chunks=5)
        assert len(rows) == 5
        assert capped
        store.close()

    def test_batching_large_path_list(self, tmp_path):
        """Large path lists are batched to avoid SQLite param limits."""
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 600)

        # Request all 600 paths — should batch at 500
        all_paths = [f"src/file_{i:03d}.py" for i in range(600)]
        rows, capped = store.get_embeddings_filtered(all_paths)
        assert len(rows) == 600
        assert not capped
        store.close()

    def test_nonexistent_paths_return_empty(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 5)

        rows, capped = store.get_embeddings_filtered(["nonexistent.py"])
        assert len(rows) == 0
        assert not capped
        store.close()

    def test_cap_with_filtered_paths(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 10)

        paths = [f"src/file_{i:03d}.py" for i in range(10)]
        rows, capped = store.get_embeddings_filtered(paths, max_chunks=3)
        assert len(rows) == 3
        assert capped
        store.close()


# ---------------------------------------------------------------------------
# Commit 3 — Narrowed search
# ---------------------------------------------------------------------------


class TestNarrowedSearch:
    def test_basic_narrowed_search(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 20, dim=8)

        query = _make_vec(8, seed=5.0)
        result = narrowed_search(
            query,
            store,
            dim=8,
            allowed_paths=["src/file_005.py", "src/file_010.py"],
            topk=2,
        )
        assert isinstance(result, NarrowedSearchResult)
        assert len(result.hits) <= 2
        assert result.debug["candidate_chunks_considered"] == 2
        assert not result.debug["fallback_used"]
        store.close()

    def test_no_paths_searches_all(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 10, dim=8)

        query = _make_vec(8, seed=3.0)
        result = narrowed_search(
            query,
            store,
            dim=8,
            topk=3,
        )
        assert len(result.hits) <= 3
        assert result.debug["candidate_chunks_considered"] == 10
        store.close()

    def test_fallback_global_tight(self, tmp_path):
        """Empty candidate pool triggers global_tight fallback."""
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 10, dim=8)

        query = _make_vec(8, seed=1.0)
        result = narrowed_search(
            query,
            store,
            dim=8,
            allowed_paths=["nonexistent.py"],
            fallback="global_tight",
            fallback_topk=3,
        )
        assert result.debug["fallback_used"] is True
        assert result.debug["fallback_strategy"] == "global_tight"
        assert len(result.hits) <= 3
        store.close()

    def test_fallback_skip(self, tmp_path):
        """Empty candidate pool with fallback=skip returns no hits."""
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 5, dim=8)

        query = _make_vec(8, seed=1.0)
        result = narrowed_search(
            query,
            store,
            dim=8,
            allowed_paths=["nonexistent.py"],
            fallback="skip",
        )
        assert result.debug["fallback_used"] is True
        assert result.debug["fallback_strategy"] == "skip"
        assert result.hits == []
        store.close()

    def test_debug_metadata_complete(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 10, dim=8)

        query = _make_vec(8, seed=1.0)
        cand_debug = {"strategy": "exclude_top_k", "excluded_top_k": 5}
        result = narrowed_search(
            query,
            store,
            dim=8,
            allowed_paths=["src/file_005.py"],
            candidate_debug=cand_debug,
        )
        d = result.debug
        assert "candidate_selection" in d
        assert d["candidate_selection"]["strategy"] == "exclude_top_k"
        assert "candidate_chunks_considered" in d
        assert "candidate_chunks_capped" in d
        assert "search_time_ms" in d
        store.close()

    def test_max_chunks_caps_search(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 20, dim=8)

        query = _make_vec(8, seed=1.0)
        result = narrowed_search(
            query,
            store,
            dim=8,
            max_chunks=5,
            topk=3,
        )
        assert result.debug["candidate_chunks_considered"] == 5
        assert result.debug["candidate_chunks_capped"]
        store.close()

    def test_to_dict_serializable(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        _populate_store(store, 5, dim=8)

        query = _make_vec(8, seed=1.0)
        result = narrowed_search(query, store, dim=8, topk=2)
        d = result.to_dict()
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        assert isinstance(parsed["hits"], list)
        assert isinstance(parsed["debug"], dict)
        store.close()


# ---------------------------------------------------------------------------
# Commit 4 — Corpus extraction of narrowing fields
# ---------------------------------------------------------------------------


class TestCorpusNarrowingFields:
    def test_model_defaults(self):
        rec = CorpusRecord()
        assert rec.semantic_candidate_strategy == ""
        assert rec.semantic_candidate_files == 0
        assert rec.semantic_candidate_chunks == 0
        assert rec.semantic_candidate_fallback_used is False

    def test_to_dict_includes_narrowing(self):
        rec = CorpusRecord(
            semantic_candidate_strategy="exclude_top_k",
            semantic_candidate_files=42,
            semantic_candidate_chunks=7200,
            semantic_candidate_fallback_used=True,
        )
        d = rec.to_dict()
        assert d["semantic_candidate_strategy"] == "exclude_top_k"
        assert d["semantic_candidate_files"] == 42
        assert d["semantic_candidate_chunks"] == 7200
        assert d["semantic_candidate_fallback_used"] is True

    def _make_sidecar(self, *, cand_data: dict | None = None) -> dict:
        final = {
            "mode": "default",
            "ranked_sources": [{"path": "f.py", "score": 1.0}],
            "matches": [{"path": "f.py"}],
            "slices": [{"path": "f.py"}],
            "_debug": {
                "score_cards": [{"features": {"is_prob_def": True}}],
                "timings": {"total_ms": 100.0},
                "semantic": {"invoked": True, "time_ms": 50.0, "hit_count": 3},
            },
        }
        if cand_data is not None:
            final["_debug"]["semantic_candidates"] = cand_data

        return {
            "bundle_schema_version": 1,
            "repo": "org/repo",
            "mode": "default",
            "created_at": 1700000000.0,
            "request_id": "req-1",
            "final": final,
            "passes": [],
        }

    def test_extracts_from_debug(self):
        sidecar = self._make_sidecar(
            cand_data={
                "strategy": "exclude_top_k",
                "candidate_files": 42,
                "candidate_chunks_considered": 7200,
                "fallback_used": False,
            }
        )
        rec = extract_record(sidecar)
        assert rec.semantic_candidate_strategy == "exclude_top_k"
        assert rec.semantic_candidate_files == 42
        assert rec.semantic_candidate_chunks == 7200
        assert rec.semantic_candidate_fallback_used is False

    def test_no_narrowing_data(self):
        sidecar = self._make_sidecar()
        rec = extract_record(sidecar)
        assert rec.semantic_candidate_strategy == ""
        assert rec.semantic_candidate_files == 0

    def test_fallback_detected(self):
        sidecar = self._make_sidecar(
            cand_data={
                "strategy": "exclude_top_k",
                "candidate_files": 0,
                "candidate_chunks_considered": 0,
                "fallback_used": True,
            }
        )
        rec = extract_record(sidecar)
        assert rec.semantic_candidate_fallback_used is True


# ---------------------------------------------------------------------------
# Commit 5 — Narrowing experiment template
# ---------------------------------------------------------------------------


class TestNarrowingExperimentTemplate:
    def test_creates_valid_experiment(self):
        exp = create_narrowing_experiment()
        data = exp.to_dict()
        errors = validate_experiment(data)
        assert errors == [], f"Validation errors: {errors}"

    def test_two_variants(self):
        exp = create_narrowing_experiment()
        assert len(exp.variants) == 2
        assert exp.variants[0].name == "A"
        assert exp.variants[1].name == "B"

    def test_primary_kpi_is_lift(self):
        exp = create_narrowing_experiment()
        assert exp.decision_rule.primary_kpi == "semantic_lift_mean"

    def test_auto_generated_id(self):
        exp = create_narrowing_experiment()
        assert exp.id.startswith("exp-narrowing-")

    def test_custom_id(self):
        exp = create_narrowing_experiment(id="my-narrow-001")
        assert exp.id == "my-narrow-001"

    def test_includes_semantic_kpis(self):
        exp = create_narrowing_experiment()
        assert "semantic_lift_mean" in exp.kpis
        assert "semantic_invoked_rate" in exp.kpis

    def test_tie_breakers(self):
        exp = create_narrowing_experiment()
        assert "confidence_final_mean" in exp.decision_rule.tie_breakers

    def test_constraints_present(self):
        exp = create_narrowing_experiment()
        assert len(exp.decision_rule.constraints) >= 1
        kpi_names = [c["kpi"] for c in exp.decision_rule.constraints]
        assert "truncation_rate" in kpi_names

    def test_json_serializable(self):
        exp = create_narrowing_experiment()
        serialized = json.dumps(exp.to_dict())
        parsed = json.loads(serialized)
        assert parsed["id"].startswith("exp-narrowing-")
        assert len(parsed["variants"]) == 2
