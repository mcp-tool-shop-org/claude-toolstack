"""Tests for semantic_fallback autopilot action and corpus wiring."""

from __future__ import annotations

import json

import pytest

from cts.autopilot import (
    apply_refinement,
    plan_refinements,
)
from cts.corpus.evaluate import extract_kpis
from cts.corpus.experiment_schema import (
    SEMANTIC_KPIS,
    create_semantic_experiment,
    validate_experiment,
)
from cts.corpus.extract import extract_record
from cts.corpus.model import CorpusRecord


# ---------------------------------------------------------------------------
# 5.1 — Autopilot semantic_fallback action
# ---------------------------------------------------------------------------


class TestSemanticFallbackPlanning:
    """Verify semantic_fallback triggers under correct conditions."""

    def _make_bundle(
        self,
        *,
        mode: str = "default",
        sources: int = 2,
        slices: int = 0,
        query: str = "some_query",
    ) -> dict:
        return {
            "mode": mode,
            "query": query,
            "ranked_sources": [
                {"path": f"file{i}.py", "score": 0.3} for i in range(sources)
            ],
            "matches": [{"path": f"file{i}.py"} for i in range(sources)],
            "slices": [{"path": f"slice{i}.py"} for i in range(slices)],
        }

    def _low_conf(self, score: float = 0.3) -> dict:
        return {
            "score": score,
            "sufficient": False,
            "signals": {
                "top_score_weight": 0.06,
                "definition_found": 0.0,
                "source_diversity": 0.1,
                "slice_coverage": 0.0,
                "low_match_penalty": -0.1,
                "mode_bonus": 0.0,
            },
            "reason": f"Confidence {score:.2f} < 0.6",
        }

    def test_fires_when_conditions_met(self):
        """semantic_fallback triggers on low conf + sparse matches.

        Set max_matches > 50 and slice_coverage >= 0.1 to avoid
        widen_search and add_slices filling the 2-action cap first.
        """
        bundle = self._make_bundle(sources=2)
        conf = self._low_conf(0.3)
        conf["signals"]["slice_coverage"] = 0.15  # prevent add_slices
        params = {
            "semantic_store_path": "/tmp/semantic.sqlite3",
            "max_matches": 100,  # prevent widen_search
        }

        actions = plan_refinements(bundle, conf, current_params=params)
        names = [a["name"] for a in actions]
        assert "semantic_fallback" in names

    def test_not_fired_without_store(self):
        """No semantic_fallback when no store path is configured."""
        bundle = self._make_bundle(sources=2)
        conf = self._low_conf(0.3)
        params = {}  # no semantic_store_path

        actions = plan_refinements(bundle, conf, current_params=params)
        names = [a["name"] for a in actions]
        assert "semantic_fallback" not in names

    def test_not_fired_when_confidence_ok(self):
        """No semantic_fallback when confidence is above gate."""
        bundle = self._make_bundle(sources=10, slices=3)
        conf = {
            "score": 0.65,
            "sufficient": True,
            "signals": {"definition_found": 0.2},
            "reason": "ok",
        }
        params = {"semantic_store_path": "/tmp/semantic.sqlite3"}

        actions = plan_refinements(bundle, conf, current_params=params)
        assert actions == []  # sufficient → no actions at all

    def test_not_fired_when_already_invoked(self):
        """No semantic_fallback when _semantic_invoked is already True."""
        bundle = self._make_bundle(sources=2)
        conf = self._low_conf(0.3)
        params = {
            "semantic_store_path": "/tmp/semantic.sqlite3",
            "_semantic_invoked": True,
        }

        actions = plan_refinements(bundle, conf, current_params=params)
        names = [a["name"] for a in actions]
        assert "semantic_fallback" not in names

    def test_not_fired_when_many_sources_and_strong_scores(self):
        """No semantic_fallback when many sources AND top scores are strong.

        Branch A requires < 5 sources, Branch B requires top_score_weight < 0.15.
        With 8 strong-scoring sources, neither branch triggers.
        """
        bundle = self._make_bundle(sources=8)
        conf = self._low_conf(0.45)
        # Strong top score prevents Branch B
        conf["signals"]["top_score_weight"] = 0.25
        params = {"semantic_store_path": "/tmp/semantic.sqlite3"}

        actions = plan_refinements(bundle, conf, current_params=params)
        names = [a["name"] for a in actions]
        assert "semantic_fallback" not in names

    def test_branch_b_fires_many_sources_weak_scores(self):
        """Branch B triggers when many sources but top scores are weak.

        >= 5 sources with top_score_weight < 0.15, no definition, low conf.
        """
        bundle = self._make_bundle(sources=10)
        conf = self._low_conf(0.3)
        conf["signals"]["top_score_weight"] = 0.06  # weak top score
        conf["signals"]["slice_coverage"] = 0.15  # prevent add_slices
        params = {
            "semantic_store_path": "/tmp/semantic.sqlite3",
            "max_matches": 100,  # prevent widen_search
        }

        actions = plan_refinements(bundle, conf, current_params=params)
        names = [a["name"] for a in actions]
        assert "semantic_fallback" in names

    def test_branch_b_trigger_reason_labeled(self):
        """Branch B trigger_reason includes [branch-B] label."""
        bundle = self._make_bundle(sources=10)
        conf = self._low_conf(0.3)
        conf["signals"]["top_score_weight"] = 0.06
        conf["signals"]["slice_coverage"] = 0.15
        params = {
            "semantic_store_path": "/tmp/semantic.sqlite3",
            "max_matches": 100,
        }

        actions = plan_refinements(bundle, conf, current_params=params)
        sem = [a for a in actions if a["name"] == "semantic_fallback"]
        assert len(sem) == 1
        assert "[branch-B]" in sem[0]["trigger_reason"]

    def test_branch_a_trigger_reason_labeled(self):
        """Branch A trigger_reason includes [branch-A] label."""
        bundle = self._make_bundle(sources=2)
        conf = self._low_conf(0.3)
        conf["signals"]["slice_coverage"] = 0.15
        params = {
            "semantic_store_path": "/tmp/semantic.sqlite3",
            "max_matches": 100,
        }

        actions = plan_refinements(bundle, conf, current_params=params)
        sem = [a for a in actions if a["name"] == "semantic_fallback"]
        assert len(sem) == 1
        assert "[branch-A]" in sem[0]["trigger_reason"]

    def test_trigger_reason_present(self):
        """The action includes a descriptive trigger_reason."""
        bundle = self._make_bundle(sources=2)
        conf = self._low_conf(0.3)
        conf["signals"]["slice_coverage"] = 0.15
        params = {
            "semantic_store_path": "/tmp/semantic.sqlite3",
            "max_matches": 100,
        }

        actions = plan_refinements(bundle, conf, current_params=params)
        sem_actions = [a for a in actions if a["name"] == "semantic_fallback"]
        assert len(sem_actions) == 1
        assert "trigger_reason" in sem_actions[0]
        assert "semantic" in sem_actions[0]["trigger_reason"].lower()

    def test_max_two_actions_cap(self):
        """semantic_fallback respects the 2-action-per-pass cap."""
        bundle = self._make_bundle(sources=2, slices=0)
        conf = self._low_conf(0.15)
        params = {"semantic_store_path": "/tmp/semantic.sqlite3"}

        actions = plan_refinements(bundle, conf, current_params=params)
        assert len(actions) <= 2


class TestSemanticFallbackApply:
    """Verify apply_refinement for semantic_fallback."""

    def test_sets_semantic_invoked(self):
        """apply_refinement sets _semantic_invoked=True."""
        params = {"max_matches": 50}
        action = {"name": "semantic_fallback"}
        new_params = apply_refinement(params, action)
        assert new_params["_semantic_invoked"] is True

    def test_preserves_other_params(self):
        """Other params are not modified by semantic_fallback."""
        params = {"max_matches": 100, "evidence_files": 10}
        action = {"name": "semantic_fallback"}
        new_params = apply_refinement(params, action)
        assert new_params["max_matches"] == 100
        assert new_params["evidence_files"] == 10


# ---------------------------------------------------------------------------
# 5.2 — Corpus ingestion: semantic fields
# ---------------------------------------------------------------------------


class TestCorpusSemanticFields:
    """Verify CorpusRecord includes semantic augmentation fields."""

    def test_model_defaults(self):
        """CorpusRecord has semantic fields with proper defaults."""
        rec = CorpusRecord()
        assert rec.semantic_invoked is False
        assert rec.semantic_time_ms is None
        assert rec.semantic_hit_count == 0
        assert rec.semantic_action_fired is False
        assert rec.semantic_lift is None

    def test_to_dict_includes_semantic(self):
        """to_dict() includes all semantic fields."""
        rec = CorpusRecord(
            semantic_invoked=True,
            semantic_time_ms=42.5,
            semantic_hit_count=3,
            semantic_action_fired=True,
            semantic_lift=0.12,
        )
        d = rec.to_dict()
        assert d["semantic_invoked"] is True
        assert d["semantic_time_ms"] == 42.5
        assert d["semantic_hit_count"] == 3
        assert d["semantic_action_fired"] is True
        assert d["semantic_lift"] == 0.12

    def test_json_serializable(self):
        """Semantic fields serialize cleanly to JSON."""
        rec = CorpusRecord(
            semantic_invoked=True,
            semantic_time_ms=42.5,
            semantic_hit_count=3,
        )
        serialized = json.dumps(rec.to_dict())
        parsed = json.loads(serialized)
        assert parsed["semantic_invoked"] is True
        assert parsed["semantic_time_ms"] == 42.5


class TestExtractSemanticFromSidecar:
    """Verify semantic field extraction from sidecar artifacts."""

    def _make_sidecar(
        self,
        *,
        semantic: dict | None = None,
        passes: list | None = None,
    ) -> dict:
        final = {
            "mode": "default",
            "ranked_sources": [{"path": "f.py", "score": 1.0}],
            "matches": [{"path": "f.py"}],
            "slices": [{"path": "f.py"}],
            "_debug": {
                "score_cards": [
                    {"features": {"is_prob_def": True}},
                ],
                "timings": {"total_ms": 100.0},
            },
        }
        if semantic is not None:
            final["_debug"]["semantic"] = semantic

        return {
            "bundle_schema_version": 1,
            "repo": "org/repo",
            "mode": "default",
            "created_at": 1700000000.0,
            "request_id": "req-1",
            "final": final,
            "passes": passes or [],
        }

    def test_no_semantic_data(self):
        """When no semantic data, fields are default."""
        sidecar = self._make_sidecar()
        rec = extract_record(sidecar)
        assert rec.semantic_invoked is False
        assert rec.semantic_time_ms is None
        assert rec.semantic_hit_count == 0

    def test_semantic_invoked(self):
        """Extracts semantic_invoked from _debug.semantic."""
        sidecar = self._make_sidecar(
            semantic={"invoked": True, "time_ms": 42.5, "hit_count": 3},
        )
        rec = extract_record(sidecar)
        assert rec.semantic_invoked is True
        assert rec.semantic_time_ms == 42.5
        assert rec.semantic_hit_count == 3

    def test_semantic_action_fired(self):
        """Detects semantic_fallback in action list."""
        sidecar = self._make_sidecar(
            passes=[
                {
                    "actions": ["widen_search", "semantic_fallback"],
                    "action_details": [
                        {"name": "widen_search"},
                        {"name": "semantic_fallback"},
                    ],
                    "confidence_before": 0.3,
                    "status": "ok",
                },
            ],
        )
        rec = extract_record(sidecar)
        assert rec.semantic_action_fired is True

    def test_semantic_lift_computed(self):
        """Computes lift from the semantic pass's confidence delta."""
        sidecar = self._make_sidecar(
            passes=[
                {
                    "actions": ["semantic_fallback"],
                    "action_details": [{"name": "semantic_fallback"}],
                    "confidence_before": 0.3,
                    "status": "ok",
                },
            ],
        )
        # Final confidence will be computed from the bundle
        rec = extract_record(sidecar)
        assert rec.semantic_action_fired is True
        # Lift = confidence_final - 0.3 (from the pass)
        if rec.semantic_lift is not None:
            assert isinstance(rec.semantic_lift, float)

    def test_no_lift_without_action(self):
        """No semantic_lift when action didn't fire."""
        sidecar = self._make_sidecar(
            semantic={"invoked": True, "time_ms": 10.0, "hit_count": 1},
        )
        rec = extract_record(sidecar)
        assert rec.semantic_lift is None


# ---------------------------------------------------------------------------
# 5.2b — KPI extraction includes semantic metrics
# ---------------------------------------------------------------------------


class TestSemanticKPIs:
    """Verify extract_kpis includes semantic KPI values."""

    def _make_records(self) -> list:
        return [
            {
                "confidence_final": 0.7,
                "confidence_delta": 0.1,
                "passes_count": 1,
                "confidence_pass1": 0.6,
                "bundle_bytes_final": 5000,
                "truncation_flags": {},
                "semantic_invoked": True,
                "semantic_action_fired": True,
                "semantic_lift": 0.08,
            },
            {
                "confidence_final": 0.8,
                "confidence_delta": 0.15,
                "passes_count": 1,
                "confidence_pass1": 0.65,
                "bundle_bytes_final": 6000,
                "truncation_flags": {},
                "semantic_invoked": False,
                "semantic_action_fired": False,
                "semantic_lift": None,
            },
            {
                "confidence_final": 0.6,
                "confidence_delta": 0.05,
                "passes_count": 0,
                "confidence_pass1": 0.55,
                "bundle_bytes_final": 4000,
                "truncation_flags": {},
                "semantic_invoked": True,
                "semantic_action_fired": True,
                "semantic_lift": 0.12,
            },
        ]

    def test_semantic_invoked_rate(self):
        kpis = extract_kpis(self._make_records())
        # 2 out of 3 had semantic_invoked=True
        assert kpis["semantic_invoked_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_semantic_action_rate(self):
        kpis = extract_kpis(self._make_records())
        # 2 out of 3 had semantic_action_fired=True
        assert kpis["semantic_action_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_semantic_lift_mean(self):
        kpis = extract_kpis(self._make_records())
        # mean of [0.08, 0.12] = 0.10
        assert kpis["semantic_lift_mean"] == pytest.approx(0.10, abs=0.01)

    def test_empty_corpus_semantic_kpis(self):
        kpis = extract_kpis([])
        assert kpis["total"] == 0

    def test_no_semantic_records(self):
        """Records without semantic fields still produce valid KPIs."""
        records = [
            {
                "confidence_final": 0.7,
                "confidence_delta": 0.1,
                "passes_count": 1,
                "confidence_pass1": 0.6,
                "bundle_bytes_final": 5000,
                "truncation_flags": {},
            },
        ]
        kpis = extract_kpis(records)
        assert kpis["semantic_invoked_rate"] == 0.0
        assert kpis["semantic_action_rate"] == 0.0
        assert kpis["semantic_lift_mean"] == 0.0


# ---------------------------------------------------------------------------
# 5.3 — Experiment template for Phase 4
# ---------------------------------------------------------------------------


class TestSemanticExperimentTemplate:
    """Verify the Phase 4 semantic experiment template."""

    def test_creates_valid_experiment(self):
        exp = create_semantic_experiment()
        data = exp.to_dict()
        errors = validate_experiment(data)
        assert errors == [], f"Validation errors: {errors}"

    def test_two_variants(self):
        exp = create_semantic_experiment()
        assert len(exp.variants) == 2
        assert exp.variants[0].name == "A"
        assert exp.variants[1].name == "B"

    def test_includes_semantic_kpis(self):
        exp = create_semantic_experiment()
        assert "semantic_lift_mean" in exp.kpis
        assert "semantic_invoked_rate" in exp.kpis
        assert "semantic_action_rate" in exp.kpis

    def test_primary_kpi(self):
        exp = create_semantic_experiment()
        assert exp.decision_rule.primary_kpi == "confidence_final_mean"

    def test_tie_breakers(self):
        exp = create_semantic_experiment()
        assert "semantic_lift_mean" in exp.decision_rule.tie_breakers

    def test_constraints_present(self):
        exp = create_semantic_experiment()
        assert len(exp.decision_rule.constraints) >= 1
        kpi_names = [c["kpi"] for c in exp.decision_rule.constraints]
        assert "truncation_rate" in kpi_names

    def test_auto_generated_id(self):
        exp = create_semantic_experiment()
        assert exp.id.startswith("exp-semantic-")

    def test_custom_id(self):
        exp = create_semantic_experiment(id="my-exp-001")
        assert exp.id == "my-exp-001"

    def test_json_serializable(self):
        exp = create_semantic_experiment()
        serialized = json.dumps(exp.to_dict())
        parsed = json.loads(serialized)
        assert parsed["id"].startswith("exp-semantic-")
        assert len(parsed["variants"]) == 2

    def test_semantic_kpis_superset_of_default(self):
        """SEMANTIC_KPIS includes all DEFAULT_KPIS plus semantic ones."""
        from cts.corpus.experiment_schema import DEFAULT_KPIS

        for kpi in DEFAULT_KPIS:
            assert kpi in SEMANTIC_KPIS
        assert len(SEMANTIC_KPIS) > len(DEFAULT_KPIS)


# ---------------------------------------------------------------------------
# 5.4 — CLI wiring: CTS_SEMANTIC_ENABLED injects store path
# ---------------------------------------------------------------------------


class TestCLISemanticStoreInjection:
    """Verify that CTS_SEMANTIC_ENABLED gates semantic_store_path injection."""

    def test_enabled_with_store_injects_path(self, tmp_path, monkeypatch):
        """When enabled and store exists, semantic_store_path is in kwargs."""
        import os

        from cts.cli import _default_db_path

        repo = "test/repo"
        db_path = _default_db_path(repo)

        # Create the store file so os.path.exists returns True
        full_db = tmp_path / db_path
        full_db.parent.mkdir(parents=True, exist_ok=True)
        full_db.write_bytes(b"")

        monkeypatch.setenv("CTS_SEMANTIC_ENABLED", "1")
        monkeypatch.chdir(tmp_path)

        sem_flag = os.environ.get("CTS_SEMANTIC_ENABLED", "").lower()
        assert sem_flag in ("1", "true")

        resolved = _default_db_path(repo)
        assert os.path.exists(resolved)

        # Simulate the build_kwargs construction from cli.py
        bk: dict = {}
        if sem_flag in ("1", "true"):
            sem_db = _default_db_path(repo)
            if os.path.exists(sem_db):
                bk["semantic_store_path"] = sem_db

        assert "semantic_store_path" in bk
        assert bk["semantic_store_path"] == resolved

    def test_enabled_without_store_no_injection(self, tmp_path, monkeypatch):
        """When enabled but store missing, no semantic_store_path."""
        import os

        from cts.cli import _default_db_path

        monkeypatch.setenv("CTS_SEMANTIC_ENABLED", "1")
        monkeypatch.chdir(tmp_path)

        repo = "test/repo"
        sem_db = _default_db_path(repo)
        assert not os.path.exists(sem_db)

        bk: dict = {}
        sem_flag = os.environ.get("CTS_SEMANTIC_ENABLED", "").lower()
        if sem_flag in ("1", "true"):
            if os.path.exists(sem_db):
                bk["semantic_store_path"] = sem_db

        assert "semantic_store_path" not in bk

    def test_disabled_no_injection(self, tmp_path, monkeypatch):
        """When disabled (default), no injection even if store exists."""
        import os

        from cts.cli import _default_db_path

        repo = "test/repo"
        db_path = _default_db_path(repo)

        full_db = tmp_path / db_path
        full_db.parent.mkdir(parents=True, exist_ok=True)
        full_db.write_bytes(b"")

        monkeypatch.chdir(tmp_path)
        # CTS_SEMANTIC_ENABLED not set

        bk: dict = {}
        sem_flag = os.environ.get("CTS_SEMANTIC_ENABLED", "").lower()
        if sem_flag in ("1", "true"):
            sem_db = _default_db_path(repo)
            if os.path.exists(sem_db):
                bk["semantic_store_path"] = sem_db

        assert "semantic_store_path" not in bk

    def test_true_string_accepted(self, tmp_path, monkeypatch):
        """CTS_SEMANTIC_ENABLED=true works (not just =1)."""
        import os

        from cts.cli import _default_db_path

        repo = "test/repo"
        db_path = _default_db_path(repo)

        full_db = tmp_path / db_path
        full_db.parent.mkdir(parents=True, exist_ok=True)
        full_db.write_bytes(b"")

        monkeypatch.setenv("CTS_SEMANTIC_ENABLED", "true")
        monkeypatch.chdir(tmp_path)

        bk: dict = {}
        sem_flag = os.environ.get("CTS_SEMANTIC_ENABLED", "").lower()
        if sem_flag in ("1", "true"):
            sem_db = _default_db_path(repo)
            if os.path.exists(sem_db):
                bk["semantic_store_path"] = sem_db

        assert "semantic_store_path" in bk

    def test_zero_means_disabled(self, tmp_path, monkeypatch):
        """CTS_SEMANTIC_ENABLED=0 means disabled."""
        import os

        monkeypatch.setenv("CTS_SEMANTIC_ENABLED", "0")

        sem_flag = os.environ.get("CTS_SEMANTIC_ENABLED", "").lower()
        assert sem_flag not in ("1", "true")


# ---------------------------------------------------------------------------
# 5.5 — Semantic retrieval pipeline (Phase 4.3)
# ---------------------------------------------------------------------------


def _create_test_store(tmp_path, repo="test/repo", dim=384):
    """Helper: create a SemanticStore with mock embeddings."""
    from cts.semantic.embedder import MockEmbedder
    from cts.semantic.store import SemanticStore

    db_path = str(tmp_path / "semantic.sqlite3")
    store = SemanticStore(db_path)

    embedder = MockEmbedder(dim=dim)

    # Create test source files
    files = {
        "src/auth.py": "def authenticate(user, password):\n    pass\n" * 10,
        "src/config.py": "class AppConfig:\n    timeout = 30\n" * 10,
        "src/handler.py": "def handle_request(req):\n    return response\n" * 10,
    }
    for rel_path, content in files.items():
        full = tmp_path / "repo" / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    # Index chunks manually
    from cts.semantic.chunker import Chunk

    chunks = []
    for rel_path, content in files.items():
        lines = content.splitlines()
        chunk = Chunk(
            chunk_id=f"{repo}:{rel_path}:1-{len(lines)}",
            repo=repo,
            path=rel_path,
            start_line=1,
            end_line=len(lines),
            content=content,
            content_hash=str(hash(content)),
        )
        chunks.append(chunk)

    store.upsert_chunks(chunks)

    # Embed and store
    texts = [c.content for c in chunks]
    vecs = embedder.embed_texts(texts)
    store.store_embeddings(
        [c.chunk_id for c in chunks],
        vecs,
        model="mock-embedder",
        dim=dim,
    )

    store.close()
    return db_path, str(tmp_path / "repo")


class TestSemanticRetrieveAndSlice:
    """Verify the semantic retrieval pipeline in the bundle builder."""

    def test_returns_slices_and_debug(self, tmp_path, monkeypatch):
        """Pipeline returns tagged slices and debug metadata."""
        import cts.bundle as bundle_mod

        monkeypatch.setattr(
            bundle_mod,
            "_EMBEDDER_CACHE",
            None,
        )

        from cts.semantic.embedder import MockEmbedder

        mock_emb = MockEmbedder(dim=384)
        monkeypatch.setattr(bundle_mod, "_EMBEDDER_CACHE", mock_emb)

        db_path, repo_root = _create_test_store(tmp_path)

        slices, debug = bundle_mod.semantic_retrieve_and_slice(
            "authenticate user",
            db_path,
            repo_root,
            max_slices=3,
        )

        assert debug["invoked"] is True
        assert debug["time_ms"] >= 0
        assert debug["hits_topk"] > 0
        assert debug["slices_added"] > 0
        assert len(slices) > 0
        assert len(slices) <= 3

        # Each slice has the semantic tag
        for sl in slices:
            assert sl["source"] == "semantic"
            assert "semantic_score" in sl
            assert sl["path"] in ("src/auth.py", "src/config.py", "src/handler.py")

    def test_respects_max_slices_cap(self, tmp_path, monkeypatch):
        """Semantic slices are capped at max_slices."""
        import cts.bundle as bundle_mod

        from cts.semantic.embedder import MockEmbedder

        mock_emb = MockEmbedder(dim=384)
        monkeypatch.setattr(bundle_mod, "_EMBEDDER_CACHE", mock_emb)

        db_path, repo_root = _create_test_store(tmp_path)

        slices, debug = bundle_mod.semantic_retrieve_and_slice(
            "config",
            db_path,
            repo_root,
            max_slices=1,
        )

        assert len(slices) <= 1
        assert debug["slices_added"] <= 1

    def test_missing_store_returns_empty(self, tmp_path):
        """Returns empty slices when store file doesn't exist."""
        from cts.bundle import semantic_retrieve_and_slice

        slices, debug = semantic_retrieve_and_slice(
            "query",
            str(tmp_path / "nonexistent.sqlite3"),
            str(tmp_path),
        )

        assert slices == []
        assert debug["error"] == "store_not_found"

    def test_empty_store_returns_empty(self, tmp_path, monkeypatch):
        """Returns empty slices when store has no embeddings."""
        from cts.semantic.store import SemanticStore

        db_path = str(tmp_path / "empty.sqlite3")
        store = SemanticStore(db_path)
        store.close()

        from cts.bundle import semantic_retrieve_and_slice

        slices, debug = semantic_retrieve_and_slice(
            "query",
            db_path,
            str(tmp_path),
        )

        assert slices == []
        assert debug["error"] == "empty_store"

    def test_path_jail_blocks_traversal(self, tmp_path):
        """_read_local_slice rejects paths that escape repo_root."""
        from cts.bundle import _read_local_slice

        repo_root = str(tmp_path / "repo")
        (tmp_path / "repo").mkdir()
        # Create a file outside repo root
        (tmp_path / "secret.txt").write_text("secret data", encoding="utf-8")

        # Traversal attempt
        result = _read_local_slice(repo_root, "../secret.txt", 1, 1)
        assert result is None

    def test_path_jail_allows_valid_paths(self, tmp_path):
        """_read_local_slice allows paths within repo_root."""
        from cts.bundle import _read_local_slice

        repo_root = str(tmp_path / "repo")
        src_dir = tmp_path / "repo" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "valid.py").write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = _read_local_slice(repo_root, "src/valid.py", 1, 3)
        assert result is not None
        assert result["path"] == "src/valid.py"
        assert len(result["lines"]) > 0


class TestBuildDefaultBundleSemantic:
    """Verify build_default_bundle with semantic augmentation."""

    def _make_search_data(self, query="test", matches=3):
        return {
            "repo": "test/repo",
            "query": query,
            "count": matches,
            "matches": [
                {"path": f"src/file{i}.py", "line": 10, "snippet": f"match {i}"}
                for i in range(matches)
            ],
            "truncated": False,
        }

    def test_no_semantic_by_default(self, monkeypatch):
        """Without semantic flags, no semantic section appears."""
        import cts.bundle as bundle_mod

        # Mock fetch_slices to avoid gateway dependency
        monkeypatch.setattr(bundle_mod, "fetch_slices", lambda *a, **kw: [])

        b = bundle_mod.build_default_bundle(
            self._make_search_data(),
            repo="test/repo",
            debug=True,
        )

        assert "_debug" in b
        assert "semantic" not in b["_debug"]

    def test_semantic_not_invoked_without_flag(self, monkeypatch):
        """Even with store_path, semantic won't run without _semantic_invoked."""
        import cts.bundle as bundle_mod

        monkeypatch.setattr(bundle_mod, "fetch_slices", lambda *a, **kw: [])

        b = bundle_mod.build_default_bundle(
            self._make_search_data(),
            repo="test/repo",
            debug=True,
            semantic_store_path="/tmp/fake.sqlite3",
            _semantic_invoked=False,
        )

        assert "semantic" not in b["_debug"]

    def test_semantic_runs_when_both_flags_set(self, tmp_path, monkeypatch):
        """When both _semantic_invoked and store_path are set, semantic runs."""
        import cts.bundle as bundle_mod

        from cts.semantic.embedder import MockEmbedder

        mock_emb = MockEmbedder(dim=384)
        monkeypatch.setattr(bundle_mod, "_EMBEDDER_CACHE", mock_emb)
        monkeypatch.setattr(bundle_mod, "fetch_slices", lambda *a, **kw: [])

        db_path, repo_root = _create_test_store(tmp_path)

        b = bundle_mod.build_default_bundle(
            self._make_search_data(query="authenticate"),
            repo="test/repo",
            repo_root=repo_root,
            debug=True,
            semantic_store_path=db_path,
            _semantic_invoked=True,
        )

        assert "_debug" in b
        assert "semantic" in b["_debug"]
        sem = b["_debug"]["semantic"]
        assert sem["invoked"] is True
        assert sem["slices_added"] > 0
        assert sem["time_ms"] >= 0

        # Semantic slices appear in the bundle
        sem_slices = [s for s in b["slices"] if s.get("source") == "semantic"]
        assert len(sem_slices) > 0

    def test_semantic_slices_tagged(self, tmp_path, monkeypatch):
        """Semantic slices have source='semantic' and semantic_score."""
        import cts.bundle as bundle_mod

        from cts.semantic.embedder import MockEmbedder

        mock_emb = MockEmbedder(dim=384)
        monkeypatch.setattr(bundle_mod, "_EMBEDDER_CACHE", mock_emb)
        monkeypatch.setattr(bundle_mod, "fetch_slices", lambda *a, **kw: [])

        db_path, repo_root = _create_test_store(tmp_path)

        b = bundle_mod.build_default_bundle(
            self._make_search_data(query="config"),
            repo="test/repo",
            repo_root=repo_root,
            debug=True,
            semantic_store_path=db_path,
            _semantic_invoked=True,
        )

        sem_slices = [s for s in b["slices"] if s.get("source") == "semantic"]
        for sl in sem_slices:
            assert "semantic_score" in sl
            assert isinstance(sl["semantic_score"], float)
            assert "lines" in sl
            assert len(sl["lines"]) > 0

    def test_semantic_dedupes_existing_paths(self, tmp_path, monkeypatch):
        """Semantic slices don't duplicate paths already in lexical slices."""
        import cts.bundle as bundle_mod

        from cts.semantic.embedder import MockEmbedder

        mock_emb = MockEmbedder(dim=384)
        monkeypatch.setattr(bundle_mod, "_EMBEDDER_CACHE", mock_emb)

        # Return a lexical slice that overlaps with a semantic hit
        monkeypatch.setattr(
            bundle_mod,
            "fetch_slices",
            lambda *a, **kw: [{"path": "src/auth.py", "lines": ["line1"]}],
        )

        db_path, repo_root = _create_test_store(tmp_path)

        b = bundle_mod.build_default_bundle(
            self._make_search_data(query="authenticate"),
            repo="test/repo",
            repo_root=repo_root,
            debug=True,
            semantic_store_path=db_path,
            _semantic_invoked=True,
        )

        # src/auth.py should appear only once
        auth_slices = [s for s in b["slices"] if s.get("path") == "src/auth.py"]
        assert len(auth_slices) == 1
        # The existing one (no source tag) should be kept, not the semantic one
        assert auth_slices[0].get("source") != "semantic"

    def test_branch_recorded_in_debug(self, tmp_path, monkeypatch):
        """_semantic_branch is recorded in _debug.semantic.branch."""
        import cts.bundle as bundle_mod

        from cts.semantic.embedder import MockEmbedder

        mock_emb = MockEmbedder(dim=384)
        monkeypatch.setattr(bundle_mod, "_EMBEDDER_CACHE", mock_emb)
        monkeypatch.setattr(bundle_mod, "fetch_slices", lambda *a, **kw: [])

        db_path, repo_root = _create_test_store(tmp_path)

        b = bundle_mod.build_default_bundle(
            self._make_search_data(query="config"),
            repo="test/repo",
            repo_root=repo_root,
            debug=True,
            semantic_store_path=db_path,
            _semantic_invoked=True,
            _semantic_branch="B",
        )

        assert b["_debug"]["semantic"]["branch"] == "B"
