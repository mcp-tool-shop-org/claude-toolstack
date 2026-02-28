"""Tests for cts.ranking — path scoring, trace extraction, composite ranking."""

from __future__ import annotations

import unittest

from cts.ranking import (
    extract_trace_files,
    looks_like_stack_trace,
    path_score,
    rank_matches,
    recency_score,
)


class TestPathScore(unittest.TestCase):
    def test_preferred_root_boost(self):
        score = path_score("src/handlers/auth.py")
        self.assertGreater(score, 0.0)

    def test_deprioritized_root_demote(self):
        score = path_score("node_modules/lodash/index.js")
        self.assertLess(score, 0.0)

    def test_test_file_slight_demotion(self):
        score = path_score("test_auth.py")
        self.assertLess(score, 0.0)

    def test_spec_file_demotion(self):
        score = path_score("auth.spec.ts")
        self.assertLess(score, 0.0)

    def test_neutral_path(self):
        score = path_score("README.md")
        self.assertEqual(score, 0.0)

    def test_custom_prefer(self):
        score = path_score("mydir/foo.py", prefer=["mydir"])
        self.assertGreater(score, 0.0)

    def test_custom_avoid(self):
        score = path_score("generated/foo.py", avoid=["generated"])
        self.assertLess(score, 0.0)

    def test_backslash_normalization(self):
        score = path_score("src\\handlers\\auth.py")
        self.assertGreater(score, 0.0)


class TestTraceExtraction(unittest.TestCase):
    def test_python_traceback(self):
        text = """Traceback (most recent call last):
  File "src/handlers/auth.py", line 42, in login
    validate(token)
  File "src/core/validator.py", line 18, in validate
    raise InvalidToken()
"""
        files = extract_trace_files(text)
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0], ("src/handlers/auth.py", 42))
        self.assertEqual(files[1], ("src/core/validator.py", 18))

    def test_node_traceback(self):
        text = """Error: Connection refused
    at TCPConnectWrap.afterConnect (net.js:1141:16)
    at handleAuth (src/auth.js:55:10)
"""
        files = extract_trace_files(text)
        self.assertTrue(len(files) >= 1)
        paths = [f[0] for f in files]
        self.assertTrue(
            any("auth" in p for p in paths),
            f"Expected auth file in {paths}",
        )

    def test_go_traceback(self):
        text = """goroutine 1 [running]:
	/home/user/app/cmd/server.go:123
	/home/user/app/internal/handler.go:45
"""
        files = extract_trace_files(text)
        self.assertEqual(len(files), 2)
        self.assertIn("server.go", files[0][0])

    def test_rust_traceback(self):
        text = """error[E0308]: mismatched types
  --> src/main.rs:42:10
  --> src/lib.rs:15:5
"""
        files = extract_trace_files(text)
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0], ("src/main.rs", 42))

    def test_deduplication(self):
        text = """  File "a.py", line 1
  File "a.py", line 1
  File "b.py", line 2
"""
        files = extract_trace_files(text)
        self.assertEqual(len(files), 2)

    def test_empty_text(self):
        self.assertEqual(extract_trace_files(""), [])


class TestLooksLikeStackTrace(unittest.TestCase):
    def test_python_trace(self):
        text = """Traceback (most recent call last):
  File "app.py", line 10
Error: something broke
"""
        self.assertTrue(looks_like_stack_trace(text))

    def test_normal_text(self):
        self.assertFalse(looks_like_stack_trace("hello world"))

    def test_single_indicator(self):
        self.assertFalse(looks_like_stack_trace("Error: one line"))


class TestRecencyScore(unittest.TestCase):
    def test_none(self):
        self.assertEqual(recency_score(None), 0.0)

    def test_recent(self):
        self.assertEqual(recency_score(1.0), 0.3)

    def test_one_week(self):
        self.assertEqual(recency_score(100.0), 0.15)

    def test_one_month(self):
        self.assertEqual(recency_score(500.0), 0.05)

    def test_old(self):
        self.assertEqual(recency_score(1000.0), 0.0)


class TestRankMatches(unittest.TestCase):
    def test_basic_ranking(self):
        matches = [
            {"path": "vendor/lodash/index.js", "line": 1},
            {"path": "src/core/auth.py", "line": 10},
        ]
        ranked = rank_matches(matches)
        self.assertEqual(ranked[0]["path"], "src/core/auth.py")

    def test_trace_boost(self):
        matches = [
            {"path": "src/core/auth.py", "line": 10},
            {"path": "vendor/lib.py", "line": 1},
        ]
        trace_files = [("vendor/lib.py", 1)]
        ranked = rank_matches(matches, trace_files=trace_files)
        # Trace boost (+2.0) should override path penalty (-0.8)
        self.assertEqual(ranked[0]["path"], "vendor/lib.py")

    def test_rank_score_attached(self):
        matches = [{"path": "src/foo.py", "line": 1}]
        ranked = rank_matches(matches)
        self.assertIn("_rank_score", ranked[0])
        self.assertIsInstance(ranked[0]["_rank_score"], float)

    def test_empty_matches(self):
        self.assertEqual(rank_matches([]), [])


if __name__ == "__main__":
    unittest.main()
