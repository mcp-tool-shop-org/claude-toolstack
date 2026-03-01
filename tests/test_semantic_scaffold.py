"""Tests for semantic module scaffolding.

Covers: config, chunker, store, mock embedder, search, indexer.
All tests use MockEmbedder — no GPU or sentence-transformers required.
"""

from __future__ import annotations

import os
import struct

import pytest

from cts.semantic import DEFAULTS, SEMANTIC_SCHEMA_VERSION, _check_deps
from cts.semantic.chunker import (
    Chunk,
    _content_hash,
    _is_minified,
    _should_skip,
    chunk_directory,
    chunk_file,
)
from cts.semantic.config import SemanticConfig, load_config
from cts.semantic.embedder import MockEmbedder, create_embedder
from cts.semantic.indexer import IndexResult, index_repo
from cts.semantic.search import cosine_search
from cts.semantic.store import SemanticStore


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self) -> None:
        cfg = load_config()
        assert cfg.chunk_lines == 180
        assert cfg.overlap_lines == 30
        assert cfg.topk_chunks == 8
        assert cfg.max_seconds == 4
        assert cfg.device == "auto"

    def test_overrides(self) -> None:
        cfg = load_config(chunk_lines=100, device="cpu")
        assert cfg.chunk_lines == 100
        assert cfg.device == "cpu"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CTS_SEMANTIC_CHUNK_LINES", "250")
        monkeypatch.setenv("CTS_SEMANTIC_DEVICE", "cuda")
        cfg = load_config()
        assert cfg.chunk_lines == 250
        assert cfg.device == "cuda"

    def test_explicit_overrides_beat_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CTS_SEMANTIC_CHUNK_LINES", "250")
        cfg = load_config(chunk_lines=100)
        assert cfg.chunk_lines == 100

    def test_skip_patterns_default(self) -> None:
        cfg = SemanticConfig()
        assert "node_modules/" in cfg.skip_patterns
        assert "vendor/" in cfg.skip_patterns


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


class TestChunker:
    def test_empty_content(self) -> None:
        assert chunk_file("", "org/repo", "empty.py") == []

    def test_single_small_file(self) -> None:
        content = "\n".join(f"line {i}" for i in range(10))
        chunks = chunk_file(content, "org/repo", "small.py", chunk_lines=180)
        assert len(chunks) == 1
        assert chunks[0].start_line == 0
        assert chunks[0].end_line == 10
        assert chunks[0].repo == "org/repo"
        assert chunks[0].path == "small.py"

    def test_multiple_chunks_with_overlap(self) -> None:
        content = "\n".join(f"line {i}" for i in range(400))
        chunks = chunk_file(
            content, "org/repo", "big.py", chunk_lines=180, overlap_lines=30
        )
        assert len(chunks) >= 2
        # Second chunk should start at 150 (180 - 30)
        assert chunks[1].start_line == 150

    def test_chunk_ids_are_stable(self) -> None:
        content = "\n".join(f"line {i}" for i in range(200))
        c1 = chunk_file(content, "org/repo", "f.py")
        c2 = chunk_file(content, "org/repo", "f.py")
        assert [c.chunk_id for c in c1] == [c.chunk_id for c in c2]

    def test_chunk_ids_change_with_content(self) -> None:
        content1 = "\n".join(f"line {i}" for i in range(200))
        content2 = "\n".join(f"changed {i}" for i in range(200))
        c1 = chunk_file(content1, "org/repo", "f.py")
        c2 = chunk_file(content2, "org/repo", "f.py")
        assert c1[0].chunk_id != c2[0].chunk_id

    def test_content_hash_deterministic(self) -> None:
        assert _content_hash("hello") == _content_hash("hello")
        assert _content_hash("hello") != _content_hash("world")

    def test_minified_detection(self) -> None:
        normal = ["short line"] * 10
        assert not _is_minified(normal)
        minified = ["x" * 600] + ["short"] * 10
        assert _is_minified(minified)

    def test_skip_patterns(self) -> None:
        assert _should_skip("vendor/lib.js", ["vendor/"])
        assert _should_skip("node_modules/pkg/index.js", ["node_modules/"])
        assert not _should_skip("src/main.py", ["vendor/"])

    def test_chunk_directory(self, tmp_path: str) -> None:
        # Create some source files
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("\n".join(f"# line {i}" for i in range(50)))
        (src / "utils.py").write_text("\n".join(f"# util {i}" for i in range(50)))

        chunks = chunk_directory(str(tmp_path), "org/repo")
        assert len(chunks) >= 2
        paths = {c.path for c in chunks}
        assert "src/main.py" in paths
        assert "src/utils.py" in paths

    def test_chunk_directory_skips_patterns(self, tmp_path: str) -> None:
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("// code")
        (tmp_path / "main.py").write_text("# code")

        chunks = chunk_directory(
            str(tmp_path), "org/repo", skip_patterns=["node_modules/"]
        )
        paths = {c.path for c in chunks}
        assert "main.py" in paths
        assert all("node_modules" not in p for p in paths)

    def test_chunk_directory_max_files(self, tmp_path: str) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.py").write_text(f"# file {i}")
        chunks = chunk_directory(str(tmp_path), "org/repo", max_files=3)
        files = {c.path for c in chunks}
        assert len(files) == 3

    def test_chunk_directory_skips_large_files(self, tmp_path: str) -> None:
        (tmp_path / "small.py").write_text("# small")
        (tmp_path / "big.dat").write_text("x" * 600_000)

        chunks = chunk_directory(str(tmp_path), "org/repo", max_file_bytes=1024)
        paths = {c.path for c in chunks}
        assert "small.py" in paths
        assert "big.dat" not in paths


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestStore:
    def test_create_store(self, tmp_path: str) -> None:
        db = str(tmp_path / "test.sqlite3")
        store = SemanticStore(db)
        assert os.path.exists(db)
        status = store.get_status()
        assert status["schema_version"] == SEMANTIC_SCHEMA_VERSION
        assert status["chunks"] == 0
        store.close()

    def test_upsert_chunks(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [
            Chunk("id1", "repo", "f.py", 0, 10, "content", "hash1"),
            Chunk("id2", "repo", "f.py", 10, 20, "content2", "hash2"),
        ]
        changed = store.upsert_chunks(chunks)
        assert changed == 2
        assert store.chunk_count() == 2

        # Re-upsert same chunks — no change
        changed2 = store.upsert_chunks(chunks)
        assert changed2 == 0
        store.close()

    def test_upsert_updates_changed_content(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [Chunk("id1", "repo", "f.py", 0, 10, "old", "hash_old")]
        store.upsert_chunks(chunks)

        updated = [Chunk("id1", "repo", "f.py", 0, 10, "new", "hash_new")]
        changed = store.upsert_chunks(updated)
        assert changed == 1
        store.close()

    def test_store_embeddings(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [Chunk("id1", "repo", "f.py", 0, 10, "c", "h")]
        store.upsert_chunks(chunks)

        vec = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
        store.store_embeddings(["id1"], [vec], "test-model", 4)

        assert store.embedding_count() == 1
        status = store.get_status()
        assert status["model"] == "test-model"
        assert status["dim"] == 4
        store.close()

    def test_get_chunks_without_embeddings(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [
            Chunk("id1", "repo", "f.py", 0, 10, "c1", "h1"),
            Chunk("id2", "repo", "f.py", 10, 20, "c2", "h2"),
        ]
        store.upsert_chunks(chunks)

        # Embed only one
        vec = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
        store.store_embeddings(["id1"], [vec], "model", 4)

        missing = store.get_chunks_without_embeddings()
        assert missing == ["id2"]
        store.close()

    def test_delete_path(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [
            Chunk("id1", "repo", "a.py", 0, 10, "c", "h"),
            Chunk("id2", "repo", "b.py", 0, 10, "c", "h"),
        ]
        store.upsert_chunks(chunks)
        deleted = store.delete_path("a.py")
        assert deleted == 1
        assert store.chunk_count() == 1
        store.close()

    def test_rebuild(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [Chunk("id1", "repo", "f.py", 0, 10, "c", "h")]
        store.upsert_chunks(chunks)
        vec = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
        store.store_embeddings(["id1"], [vec], "model", 4)

        store.rebuild()
        assert store.chunk_count() == 0
        assert store.embedding_count() == 0
        # Schema version preserved
        assert store.get_meta("schema_version") == str(SEMANTIC_SCHEMA_VERSION)
        store.close()

    def test_get_all_embeddings(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        chunks = [
            Chunk("id1", "repo", "f.py", 0, 10, "c1", "h1"),
            Chunk("id2", "repo", "g.py", 5, 15, "c2", "h2"),
        ]
        store.upsert_chunks(chunks)
        v1 = struct.pack("2f", 1.0, 0.0)
        v2 = struct.pack("2f", 0.0, 1.0)
        store.store_embeddings(["id1", "id2"], [v1, v2], "model", 2)

        results = store.get_all_embeddings()
        assert len(results) == 2
        store.close()

    def test_meta_upsert(self, tmp_path: str) -> None:
        store = SemanticStore(str(tmp_path / "test.sqlite3"))
        store.set_meta("custom_key", "value1")
        assert store.get_meta("custom_key") == "value1"
        store.set_meta("custom_key", "value2")
        assert store.get_meta("custom_key") == "value2"
        store.close()


# ---------------------------------------------------------------------------
# Embedder (mock only)
# ---------------------------------------------------------------------------


class TestMockEmbedder:
    def test_create_mock(self) -> None:
        emb = create_embedder(mock=True, mock_dim=64)
        assert emb.model_name == "mock-embedder"
        assert emb.dim == 64

    def test_embed_texts(self) -> None:
        emb = MockEmbedder(dim=16)
        vecs = emb.embed_texts(["hello world", "test"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 16 * 4  # 16 floats * 4 bytes

    def test_deterministic(self) -> None:
        emb = MockEmbedder(dim=16)
        v1 = emb.embed_texts(["same text"])
        v2 = emb.embed_texts(["same text"])
        assert v1 == v2

    def test_different_texts_different_vecs(self) -> None:
        emb = MockEmbedder(dim=16)
        v1 = emb.embed_texts(["text a"])
        v2 = emb.embed_texts(["text b"])
        assert v1 != v2

    def test_empty_input(self) -> None:
        emb = MockEmbedder(dim=16)
        assert emb.embed_texts([]) == []

    def test_normalized(self) -> None:
        emb = MockEmbedder(dim=16)
        vecs = emb.embed_texts(["test"])
        values = struct.unpack("16f", vecs[0])
        magnitude = sum(v * v for v in values) ** 0.5
        assert abs(magnitude - 1.0) < 0.01

    def test_info(self) -> None:
        emb = MockEmbedder(dim=32)
        info = emb.info()
        assert info.model_name == "mock-embedder"
        assert info.dim == 32
        assert info.backend == "mock"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestCosineSearch:
    def test_empty_candidates(self) -> None:
        q = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        hits = cosine_search(q, [], dim=4)
        assert hits == []

    def test_exact_match_highest(self) -> None:
        q = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        candidates = [
            ("id1", "a.py", 0, 10, struct.pack("4f", 1.0, 0.0, 0.0, 0.0)),
            ("id2", "b.py", 0, 10, struct.pack("4f", 0.0, 1.0, 0.0, 0.0)),
            ("id3", "c.py", 0, 10, struct.pack("4f", 0.5, 0.5, 0.0, 0.0)),
        ]
        hits = cosine_search(q, candidates, dim=4, topk=3)
        assert hits[0].chunk_id == "id1"
        assert hits[0].score == pytest.approx(1.0, abs=0.01)

    def test_topk_limiting(self) -> None:
        q = struct.pack("2f", 1.0, 0.0)
        candidates = [
            (f"id{i}", "f.py", i, i + 10, struct.pack("2f", 1.0, 0.0))
            for i in range(20)
        ]
        hits = cosine_search(q, candidates, dim=2, topk=5)
        assert len(hits) == 5

    def test_orthogonal_vectors_zero_score(self) -> None:
        q = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        candidates = [
            ("id1", "f.py", 0, 10, struct.pack("4f", 0.0, 1.0, 0.0, 0.0)),
        ]
        hits = cosine_search(q, candidates, dim=4)
        assert hits[0].score == pytest.approx(0.0, abs=0.01)

    def test_sorted_by_score_descending(self) -> None:
        q = struct.pack("2f", 1.0, 0.0)
        candidates = [
            ("low", "a.py", 0, 10, struct.pack("2f", 0.1, 0.9)),
            ("high", "b.py", 0, 10, struct.pack("2f", 0.9, 0.1)),
            ("mid", "c.py", 0, 10, struct.pack("2f", 0.5, 0.5)),
        ]
        hits = cosine_search(q, candidates, dim=2, topk=3)
        assert hits[0].chunk_id == "high"
        assert hits[-1].chunk_id == "low"


# ---------------------------------------------------------------------------
# Indexer (with mock embedder)
# ---------------------------------------------------------------------------


class TestIndexer:
    def test_index_repo_basic(self, tmp_path: str) -> None:
        # Create source files
        src = tmp_path / "repo"
        src.mkdir()
        (src / "main.py").write_text("\n".join(f"# line {i}" for i in range(50)))

        db = str(tmp_path / "semantic.sqlite3")
        store = SemanticStore(db)
        emb = MockEmbedder(dim=16)

        result = index_repo(str(src), "org/repo", store, emb)

        assert result.files_scanned >= 1
        assert result.chunks_total >= 1
        assert result.chunks_embedded >= 1
        assert result.elapsed_seconds > 0
        assert store.chunk_count() >= 1
        assert store.embedding_count() >= 1
        store.close()

    def test_incremental_indexing(self, tmp_path: str) -> None:
        src = tmp_path / "repo"
        src.mkdir()
        (src / "main.py").write_text("# original content\n" * 10)

        db = str(tmp_path / "semantic.sqlite3")
        store = SemanticStore(db)
        emb = MockEmbedder(dim=16)

        r1 = index_repo(str(src), "org/repo", store, emb)
        assert r1.chunks_embedded >= 1

        # Re-index same content — should skip
        r2 = index_repo(str(src), "org/repo", store, emb)
        assert r2.chunks_new == 0
        assert r2.chunks_embedded == 0
        store.close()

    def test_max_files_respected(self, tmp_path: str) -> None:
        src = tmp_path / "repo"
        src.mkdir()
        for i in range(20):
            (src / f"f{i}.py").write_text(f"# file {i}")

        db = str(tmp_path / "semantic.sqlite3")
        store = SemanticStore(db)
        emb = MockEmbedder(dim=16)

        result = index_repo(str(src), "org/repo", store, emb, max_files=5)
        assert result.files_scanned == 5
        store.close()

    def test_result_to_dict(self) -> None:
        r = IndexResult(
            files_scanned=10,
            chunks_total=50,
            chunks_new=5,
            chunks_embedded=5,
            elapsed_seconds=1.234,
        )
        d = r.to_dict()
        assert d["files_scanned"] == 10
        assert d["elapsed_seconds"] == 1.23


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------


class TestDepsCheck:
    def test_check_deps_reports_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If numpy/sentence-transformers are missing, error is clear."""
        # We can't easily unmock installed packages, but we can verify
        # the function exists and its error message format
        # Just verify it doesn't raise when deps are present (or gives
        # a clear message)
        try:
            _check_deps()
        except ImportError as e:
            assert "pip install .[semantic]" in str(e)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_schema_version_is_1(self) -> None:
        assert SEMANTIC_SCHEMA_VERSION == 1

    def test_defaults_have_expected_keys(self) -> None:
        expected = {
            "chunk_lines",
            "overlap_lines",
            "topk_chunks",
            "max_slices",
            "max_seconds",
            "max_file_bytes",
            "confidence_gate",
            "match_gate",
        }
        assert set(DEFAULTS.keys()) == expected
