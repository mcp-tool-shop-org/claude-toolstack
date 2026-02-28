"""Tests for cts.bundle — bundle framework + v2 template."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cts.bundle import (
    MODES,
    _Timer,
    _compute_debug,
    _dedupe_top_files,
    _parse_diff_files,
    _populate_ranked_and_matches,
    _section_size,
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


class TestTimer(unittest.TestCase):
    def test_lap_records_steps(self):
        t = _Timer()
        t.lap("step_a")
        t.lap("step_b")
        d = t.to_dict()
        self.assertIn("step_a", d)
        self.assertIn("step_b", d)
        self.assertIn("total", d)

    def test_total_ge_sum_of_steps(self):
        t = _Timer()
        t.lap("a")
        t.lap("b")
        d = t.to_dict()
        step_sum = d["a"] + d["b"]
        self.assertGreaterEqual(d["total"], step_sum)

    def test_values_are_floats(self):
        t = _Timer()
        t.lap("x")
        d = t.to_dict()
        self.assertIsInstance(d["x"], float)
        self.assertIsInstance(d["total"], float)


class TestSectionSize(unittest.TestCase):
    def test_list_items(self):
        data = [{"path": "a.py"}, {"path": "b.py"}]
        s = _section_size(data)
        self.assertEqual(s["items"], 2)
        self.assertGreater(s["bytes"], 0)
        self.assertGreater(s["lines"], 0)

    def test_empty(self):
        s = _section_size([])
        self.assertEqual(s["items"], 0)

    def test_none(self):
        s = _section_size(None)
        self.assertEqual(s["bytes"], 0)
        self.assertEqual(s["items"], 0)


class TestComputeDebug(unittest.TestCase):
    def test_basic_structure(self):
        bundle = {
            "ranked_sources": [{"path": "a.py", "line": 1, "score": 0.5}],
            "matches": [{"path": "a.py", "line": 1, "snippet": "x"}],
            "slices": [],
            "symbols": [],
            "diff": "",
        }
        t = _Timer()
        t.lap("ranking")
        debug = _compute_debug(bundle, t)
        self.assertIn("timings_ms", debug)
        self.assertIn("sections", debug)
        self.assertIn("bundle_bytes", debug)
        self.assertIn("bundle_lines", debug)

    def test_score_cards_trimmed(self):
        bundle = {
            "ranked_sources": [],
            "matches": [],
            "slices": [],
            "symbols": [],
            "diff": "",
        }
        t = _Timer()
        cards = [{"path": f"f{i}.py", "score_total": i} for i in range(20)]
        debug = _compute_debug(bundle, t, score_cards=cards, explain_top=5)
        self.assertEqual(len(debug["score_cards"]), 5)

    def test_limits_passed_through(self):
        bundle = {
            "ranked_sources": [],
            "matches": [],
            "slices": [],
            "symbols": [],
            "diff": "",
        }
        t = _Timer()
        limits = {"max_files": 5, "context": 30}
        debug = _compute_debug(bundle, t, limits=limits)
        self.assertEqual(debug["limits"]["max_files"], 5)

    def test_no_score_cards_omits_key(self):
        bundle = {
            "ranked_sources": [],
            "matches": [],
            "slices": [],
            "symbols": [],
            "diff": "",
        }
        t = _Timer()
        debug = _compute_debug(bundle, t)
        self.assertNotIn("score_cards", debug)


class TestBuildDefaultBundleDebug(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_false_no_debug_key(self, _mock_slices):
        search_data = {
            "query": "test",
            "matches": [{"path": "src/foo.py", "line": 1, "snippet": "x"}],
            "_request_id": "rid",
        }
        bundle = build_default_bundle(search_data, repo="org/repo", debug=False)
        self.assertNotIn("_debug", bundle)

    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_true_has_debug_key(self, _mock_slices):
        search_data = {
            "query": "test",
            "matches": [{"path": "src/foo.py", "line": 1, "snippet": "x"}],
            "_request_id": "rid",
        }
        bundle = build_default_bundle(search_data, repo="org/repo", debug=True)
        self.assertIn("_debug", bundle)
        debug = bundle["_debug"]
        self.assertIn("timings_ms", debug)
        self.assertIn("ranking", debug["timings_ms"])
        self.assertIn("slice_fetch", debug["timings_ms"])
        self.assertIn("score_cards", debug)
        self.assertEqual(debug["limits"]["mode"], "default")

    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_explain_top(self, _mock_slices):
        matches = [
            {"path": f"src/f{i}.py", "line": i, "snippet": "x"} for i in range(15)
        ]
        search_data = {"query": "test", "matches": matches, "_request_id": "rid"}
        bundle = build_default_bundle(
            search_data, repo="org/repo", debug=True, explain_top=3
        )
        self.assertLessEqual(len(bundle["_debug"]["score_cards"]), 3)


class TestBuildErrorBundleDebug(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_with_trace(self, _mock_slices):
        search_data = {
            "query": "error",
            "matches": [{"path": "src/auth.py", "line": 42, "snippet": "raise Error"}],
            "_request_id": "rid",
        }
        error_text = """Traceback (most recent call last):
  File "src/auth.py", line 42, in login
    raise Error
Error: auth failed
"""
        bundle = build_error_bundle(
            search_data, repo="org/repo", error_text=error_text, debug=True
        )
        debug = bundle["_debug"]
        self.assertIn("trace_extract", debug["timings_ms"])
        self.assertIn("ranking", debug["timings_ms"])
        self.assertEqual(debug["limits"]["mode"], "error")
        self.assertGreater(debug["limits"]["trace_files_found"], 0)

    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_false_no_key(self, _mock_slices):
        search_data = {"query": "x", "matches": [], "_request_id": "rid"}
        bundle = build_error_bundle(search_data, repo="org/repo", debug=False)
        self.assertNotIn("_debug", bundle)


class TestBuildSymbolBundleDebug(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_true(self, _mock_slices):
        symbol_data = {
            "defs": [{"name": "Foo", "kind": "class", "file": "src/foo.py"}],
            "_request_id": "rid",
        }
        bundle = build_symbol_bundle(
            symbol_data, search_data=None, repo="org/repo", symbol="Foo", debug=True
        )
        debug = bundle["_debug"]
        self.assertIn("symbol_parse", debug["timings_ms"])
        self.assertIn("slice_fetch", debug["timings_ms"])
        self.assertEqual(debug["limits"]["mode"], "symbol")
        self.assertEqual(debug["limits"]["definitions_found"], 1)
        # Symbol mode has no ranking, so no score_cards
        self.assertNotIn("score_cards", debug)


class TestBuildChangeBundleDebug(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_true(self, _mock_slices):
        diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,5 @@
+new line
 old line
"""
        bundle = build_change_bundle(diff, repo="org/repo", debug=True)
        debug = bundle["_debug"]
        self.assertIn("diff_parse", debug["timings_ms"])
        self.assertIn("slice_fetch", debug["timings_ms"])
        self.assertEqual(debug["limits"]["mode"], "change")
        self.assertEqual(debug["limits"]["files_changed"], 1)


class TestSymbolBundleCtagsEnrichment(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_call_sites_ranked_by_ctags(self, _mock_slices):
        """Call sites in def files should rank above non-def files."""
        symbol_data = {
            "defs": [{"name": "Foo", "kind": "c", "file": "src/model.py"}],
            "_request_id": "rid",
        }
        search_data = {
            "matches": [
                {"path": "vendor/other.py", "line": 1, "snippet": "import Foo"},
                {"path": "src/model.py", "line": 20, "snippet": "class Foo:"},
            ],
        }
        bundle = build_symbol_bundle(
            symbol_data,
            search_data=search_data,
            repo="org/repo",
            symbol="Foo",
        )
        # Def file (src/model.py) should rank first in matches
        self.assertEqual(bundle["matches"][0]["path"], "src/model.py")

    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_debug_shows_ctags_info(self, _mock_slices):
        """Debug output should include ctags metadata."""
        symbol_data = {
            "defs": [{"name": "Foo", "kind": "c", "file": "src/model.py"}],
            "_request_id": "rid",
        }
        search_data = {
            "matches": [
                {"path": "src/model.py", "line": 10, "snippet": "class Foo:"},
            ],
        }
        bundle = build_symbol_bundle(
            symbol_data,
            search_data=search_data,
            repo="org/repo",
            symbol="Foo",
            debug=True,
        )
        debug = bundle["_debug"]
        self.assertEqual(debug["limits"]["ctags_best_kind"], "class")
        self.assertEqual(debug["limits"]["ctags_def_files"], 1)
        self.assertIn("score_cards", debug)

    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_no_search_data_no_score_cards(self, _mock_slices):
        """Without search data, no call site ranking occurs."""
        symbol_data = {
            "defs": [{"name": "Foo", "kind": "c", "file": "src/model.py"}],
            "_request_id": "rid",
        }
        bundle = build_symbol_bundle(
            symbol_data,
            search_data=None,
            repo="org/repo",
            symbol="Foo",
            debug=True,
        )
        debug = bundle["_debug"]
        self.assertNotIn("score_cards", debug)


class TestDefaultBundleCtagsInfo(unittest.TestCase):
    @patch("cts.bundle.fetch_slices", return_value=[])
    def test_ctags_info_boosts_def_file(self, _mock_slices):
        """Default bundle with ctags_info should boost def files."""
        search_data = {
            "query": "MyClass",
            "matches": [
                {"path": "docs/readme.md", "line": 5, "snippet": "MyClass usage"},
                {"path": "src/model.py", "line": 10, "snippet": "class MyClass:"},
            ],
            "_request_id": "rid",
        }
        ctags_info = {
            "def_files": {"src/model.py"},
            "kind_weight": 0.6,
            "best_kind": "class",
        }
        bundle = build_default_bundle(
            search_data, repo="org/repo", ctags_info=ctags_info
        )
        # Def file should rank first
        self.assertEqual(bundle["ranked_sources"][0]["path"], "src/model.py")


if __name__ == "__main__":
    unittest.main()
