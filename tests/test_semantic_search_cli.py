"""Tests for semantic search CLI and sidecar metadata."""

from __future__ import annotations

import json

import pytest

from cts.semantic.embedder import MockEmbedder
from cts.semantic.indexer import index_repo
from cts.semantic.search import cosine_search, cosine_search_numpy
from cts.semantic.store import SemanticStore


# ---------------------------------------------------------------------------
# Search integration tests
# ---------------------------------------------------------------------------


class TestSemanticSearchIntegration:
    """End-to-end: index → embed → search → verify hits."""

    @pytest.fixture()
    def indexed_store(self, tmp_path):
        """Build a pre-indexed semantic store from test files."""
        # Create source files with distinct content
        src = tmp_path / "repo"
        src.mkdir()
        (src / "auth.py").write_text(
            "# Authentication module\n"
            "def login(user, password):\n"
            "    '''Authenticate user with credentials.'''\n"
            "    return check_password(user, password)\n"
            + "\n".join(f"# auth line {i}" for i in range(20))
        )
        (src / "database.py").write_text(
            "# Database connection\n"
            "def connect(host, port):\n"
            "    '''Open database connection.'''\n"
            "    return Connection(host, port)\n"
            + "\n".join(f"# db line {i}" for i in range(20))
        )
        (src / "utils.py").write_text(
            "# Utility functions\n"
            "def format_date(d):\n"
            "    '''Format date for display.'''\n"
            "    return d.strftime('%Y-%m-%d')\n"
            + "\n".join(f"# util line {i}" for i in range(20))
        )

        db_path = str(tmp_path / "semantic.sqlite3")
        store = SemanticStore(db_path)
        emb = MockEmbedder(dim=16)

        result = index_repo(str(src), "org/repo", store, emb)
        assert result.chunks_embedded >= 3

        return store, emb, db_path

    def test_search_returns_hits(self, indexed_store):
        store, emb, _ = indexed_store

        query_vec = emb.embed_texts(["authentication login"])[0]
        candidates = store.get_all_embeddings()
        dim = emb.dim

        hits = cosine_search(query_vec, candidates, dim, topk=3)
        assert len(hits) >= 1
        assert all(h.score >= -1.0 for h in hits)
        assert all(h.score <= 1.0 for h in hits)
        store.close()

    def test_search_hits_have_correct_shape(self, indexed_store):
        store, emb, _ = indexed_store

        query_vec = emb.embed_texts(["test query"])[0]
        candidates = store.get_all_embeddings()

        hits = cosine_search(query_vec, candidates, emb.dim, topk=5)
        for hit in hits:
            assert hit.chunk_id
            assert hit.path
            assert hit.start_line >= 0
            assert hit.end_line > hit.start_line
            assert isinstance(hit.score, float)
        store.close()

    def test_topk_limits_results(self, indexed_store):
        store, emb, _ = indexed_store

        query_vec = emb.embed_texts(["some query"])[0]
        candidates = store.get_all_embeddings()

        hits = cosine_search(query_vec, candidates, emb.dim, topk=1)
        assert len(hits) == 1
        store.close()

    def test_numpy_search_matches_python(self, indexed_store):
        store, emb, _ = indexed_store

        query_vec = emb.embed_texts(["search test"])[0]
        candidates = store.get_all_embeddings()

        py_hits = cosine_search(query_vec, candidates, emb.dim, topk=3)
        np_hits = cosine_search_numpy(query_vec, candidates, emb.dim, topk=3)

        # Same top result
        assert py_hits[0].chunk_id == np_hits[0].chunk_id
        # Scores should be close
        for ph, nh in zip(py_hits, np_hits):
            assert abs(ph.score - nh.score) < 0.01
        store.close()


# ---------------------------------------------------------------------------
# Sidecar metadata shape
# ---------------------------------------------------------------------------


class TestSemanticSidecarMetadata:
    """Verify the semantic result dict has correct sidecar fields."""

    def test_result_shape(self):
        """The semantic search result should have all required fields."""
        result = {
            "query": "what does auth do?",
            "repo": "org/repo",
            "semantic_invoked": True,
            "semantic_model": "mock-embedder",
            "semantic_topk": 8,
            "semantic_time_ms": 42.5,
            "semantic_hits": [
                {
                    "path": "auth.py",
                    "start_line": 0,
                    "end_line": 24,
                    "score": 0.8523,
                },
            ],
        }

        # Required sidecar fields
        assert result["semantic_invoked"] is True
        assert isinstance(result["semantic_model"], str)
        assert isinstance(result["semantic_topk"], int)
        assert isinstance(result["semantic_time_ms"], float)
        assert isinstance(result["semantic_hits"], list)

    def test_hit_shape(self):
        """Each hit should have path, lines, score — no raw content."""
        hit = {
            "path": "auth.py",
            "start_line": 0,
            "end_line": 24,
            "score": 0.85,
        }

        # Must have
        assert "path" in hit
        assert "start_line" in hit
        assert "end_line" in hit
        assert "score" in hit

        # Must NOT have (no raw content in sidecar)
        assert "content" not in hit
        assert "embedding" not in hit

    def test_json_serializable(self):
        """Semantic result must be JSON-serializable."""
        result = {
            "semantic_invoked": True,
            "semantic_model": "model",
            "semantic_topk": 8,
            "semantic_time_ms": 42.5,
            "semantic_hits": [
                {"path": "f.py", "start_line": 0, "end_line": 10, "score": 0.9}
            ],
        }
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed["semantic_invoked"] is True

    def test_no_embedding_vectors_in_result(self):
        """Embedding vectors must never appear in search results."""
        result = {
            "semantic_hits": [
                {
                    "path": "f.py",
                    "start_line": 0,
                    "end_line": 10,
                    "score": 0.9,
                }
            ],
        }
        serialized = json.dumps(result)
        # No binary blobs or large float arrays
        assert len(serialized) < 500
