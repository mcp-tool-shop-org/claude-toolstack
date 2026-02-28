"""Tests for cts.bundle — bundle framework + v2 template."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cts.bundle import (
    MODES,
    _dedupe_top_files,
    _parse_diff_files,
    _populate_ranked_and_matches,
    build_change_bundle,
    build_default_bundle,
    build_error_bundle,
    build_symbol_bundle,
)


class TestEmptyBundle(unittest.TestCase):
    def test_modes_tuple(self):
        self.assertEqual(MODES, ("default", "error", "symbol", "change"))


class TestDedupeTopFiles(unittest.TestCase):
    def test_dedup(self):
        matches = [
            {"path": "a.py", "line": 1},
            {"path": "a.py", "line": 2},
            {"path": "b.py", "line": 3},
        ]
        result = _dedupe_top_files(matches, 5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["path"], "a.py")

    def test_limit(self):
        matches = [{"path": f"file{i}.py", "line": i} for i in range(10)]
        result = _dedupe_top_files(matches, 3)
        self.assertEqual(len(result), 3)

    def test_empty(self):
        self.assertEqual(_dedupe_top_files([], 5), [])


class TestParseDiffFiles(unittest.TestCase):
    def test_basic_diff(self):
        diff = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,5 +10,7 @@ def login():
     pass
diff --git a/src/handler.py b/src/handler.py
--- a/src/handler.py
+++ b/src/handler.py
@@ -1,3 +1,5 @@
     pass
"""
        files = _parse_diff_files(diff)
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["path"], "src/auth.py")
        self.assertEqual(files[0]["line"], 10)
        self.assertEqual(files[1]["path"], "src/handler.py")
        self.assertEqual(files[1]["line"], 1)

    def test_empty_diff(self):
        self.assertEqual(_parse_diff_files(""), [])


class TestPopulateRankedAndMatches(unittest.TestCase):
    def test_basic(self):
        bundle = {"ranked_sources": [], "matches": []}
        ranked = [
            {
                "path": "a.py",
                "line": 10,
                "snippet": "def foo():",
                "_rank_score": 0.5,
            },
        ]
        _populate_ranked_and_matches(bundle, ranked)
        self.assertEqual(len(bundle["ranked_sources"]), 1)
        self.assertEqual(bundle["ranked_sources"][0]["score"], 0.5)
        self.assertEqual(len(bundle["matches"]), 1)

    def test_trace_flag(self):
        bundle = {"ranked_sources": [], "matches": []}
        ranked = [
            {
                "path": "a.py",
                "line": 10,
                "snippet": "x",
                "_rank_score": 2.5,
            },
        ]
        _populate_ranked_and_matches(bundle, ranked, {"a.py"})
        self.assertTrue(bundle["ranked_sources"][0].get("in_trace"))

    def test_long_snippet_trimmed(self):
        bundle = {"ranked_sources": [], "matches": []}
        ranked = [
            {
                "path": "a.py",
                "line": 1,
                "snippet": "x" * 300,
                "_rank_score": 0.0,
            },
        ]
        _populate_ranked_and_matches(bundle, ranked)
        self.assertTrue(bundle["matches"][0]["snippet"].endswith("..."))
        self.assertLessEqual(len(bundle["matches"][0]["snippet"]), 204)


class TestBuildDefaultBundle(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_basic_structure(self, _mock_slices):
        search_data = {
            "query": "def login",
            "matches": [
                {"path": "src/auth.py", "line": 10, "snippet": "def login():"},
            ],
            "count": 1,
            "_request_id": "test-rid",
        }
        bundle = build_default_bundle(search_data, repo="org/repo")
        self.assertEqual(bundle["version"], 2)
        self.assertEqual(bundle["mode"], "default")
        self.assertEqual(bundle["repo"], "org/repo")
        self.assertEqual(bundle["request_id"], "test-rid")
        self.assertEqual(len(bundle["ranked_sources"]), 1)
        self.assertEqual(len(bundle["matches"]), 1)
        self.assertIsInstance(bundle["suggested_commands"], list)


class TestBuildErrorBundle(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_with_stack_trace(self, _mock_slices):
        search_data = {
            "query": "error",
            "matches": [
                {"path": "src/auth.py", "line": 42, "snippet": "raise Error"},
            ],
            "count": 1,
            "_request_id": "test-rid",
        }
        error_text = """Traceback (most recent call last):
  File "src/auth.py", line 42, in login
    raise Error
Error: auth failed
"""
        bundle = build_error_bundle(search_data, repo="org/repo", error_text=error_text)
        self.assertEqual(bundle["mode"], "error")
        self.assertTrue(any("Stack trace" in n for n in bundle["notes"]))
        # The file from the trace should be marked
        trace_sources = [s for s in bundle["ranked_sources"] if s.get("in_trace")]
        self.assertTrue(len(trace_sources) > 0)

    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_without_trace(self, _mock_slices):
        search_data = {
            "query": "error",
            "matches": [],
            "count": 0,
            "_request_id": "test-rid",
        }
        bundle = build_error_bundle(
            search_data, repo="org/repo", error_text="just a message"
        )
        self.assertEqual(bundle["mode"], "error")
        self.assertEqual(len(bundle["notes"]), 0)


class TestBuildSymbolBundle(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_basic(self, _mock_slices):
        symbol_data = {
            "defs": [
                {"name": "MyClass", "kind": "class", "file": "src/model.py"},
            ],
            "count": 1,
            "_request_id": "test-rid",
        }
        bundle = build_symbol_bundle(
            symbol_data, search_data=None, repo="org/repo", symbol="MyClass"
        )
        self.assertEqual(bundle["mode"], "symbol")
        self.assertEqual(len(bundle["symbols"]), 1)
        self.assertEqual(bundle["symbols"][0]["name"], "MyClass")


class TestBuildChangeBundle(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_basic(self, _mock_slices):
        diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,5 @@
+new line
 old line
"""
        bundle = build_change_bundle(diff, repo="org/repo")
        self.assertEqual(bundle["mode"], "change")
        self.assertTrue(len(bundle["diff"]) > 0)
        self.assertTrue(any("1 file(s)" in n for n in bundle["notes"]))


if __name__ == "__main__":
    unittest.main()
