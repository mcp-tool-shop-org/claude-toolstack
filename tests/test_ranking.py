"""Tests for cts.ranking — path scoring, trace extraction, composite ranking."""

from __future__ import annotations

import unittest

from cts.ranking import (
    extract_trace_files,
    looks_like_stack_trace,
    path_score,
    path_score_explained,
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


class TestPathScoreExplained(unittest.TestCase):
    def test_preferred_returns_classification(self):
        detail = path_score_explained("src/auth.py")
        self.assertEqual(detail["classification"], "preferred")
        self.assertEqual(detail["path_boost"], 0.5)
        self.assertIsNotNone(detail["prefer_match"])

    def test_avoided_returns_classification(self):
        detail = path_score_explained("node_modules/lodash/index.js")
        self.assertEqual(detail["classification"], "avoided")
        self.assertEqual(detail["path_penalty"], -0.8)
        self.assertIsNotNone(detail["avoid_match"])

    def test_neutral_returns_classification(self):
        detail = path_score_explained("README.md")
        self.assertEqual(detail["classification"], "neutral")
        self.assertEqual(detail["path_boost"], 0.0)
        self.assertEqual(detail["path_penalty"], 0.0)

    def test_test_penalty(self):
        detail = path_score_explained("test_auth.py")
        self.assertEqual(detail["test_penalty"], -0.2)

    def test_score_matches_path_score(self):
        for p in ["src/foo.py", "vendor/bar.js", "README.md", "test_x.py"]:
            detail = path_score_explained(p)
            self.assertAlmostEqual(detail["score"], path_score(p))

    def test_signals_sum_to_score(self):
        detail = path_score_explained("src/test_handler.py")
        signals_sum = (
            detail["path_boost"] + detail["path_penalty"] + detail["test_penalty"]
        )
        self.assertAlmostEqual(detail["score"], signals_sum)


class TestRankMatchesExplain(unittest.TestCase):
    def test_explain_returns_tuple(self):
        matches = [
            {"path": "src/core/auth.py", "line": 10, "snippet": "def login():"},
        ]
        result = rank_matches(matches, explain=True)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        ranked, cards = result
        self.assertEqual(len(ranked), 1)
        self.assertEqual(len(cards), 1)

    def test_explain_false_returns_list(self):
        matches = [{"path": "src/foo.py", "line": 1}]
        result = rank_matches(matches, explain=False)
        self.assertIsInstance(result, list)

    def test_score_card_structure(self):
        matches = [
            {"path": "src/core/auth.py", "line": 10, "snippet": "x"},
        ]
        _, cards = rank_matches(matches, explain=True)
        card = cards[0]
        self.assertIn("score_total", card)
        self.assertIn("signals", card)
        self.assertIn("features", card)
        # Signal keys
        signals = card["signals"]
        for key in [
            "path_boost",
            "path_penalty",
            "test_penalty",
            "trace_boost",
            "recency_boost",
            "ctags_def_boost",
            "ctags_kind_boost",
            "def_likeness_boost",
            "export_boost",
            "caller_proximity_boost",
        ]:
            self.assertIn(key, signals)
        # Feature keys
        features = card["features"]
        for key in [
            "classification",
            "is_trace_file",
            "is_def_file",
            "ctags_best_kind",
            "is_prob_def",
            "is_prob_export",
            "is_prob_call",
            "struct_rule",
            "struct_def_conf",
            "struct_export_conf",
            "struct_call_conf",
            "git_age_hours",
            "prefer_match",
            "avoid_match",
        ]:
            self.assertIn(key, features)

    def test_score_total_equals_signal_sum(self):
        matches = [
            {"path": "src/core/auth.py", "line": 10, "snippet": "x"},
        ]
        _, cards = rank_matches(matches, explain=True)
        card = cards[0]
        signals = card["signals"]
        expected = sum(signals.values())
        self.assertAlmostEqual(card["score_total"], expected, places=2)

    def test_trace_file_in_explanation(self):
        matches = [
            {"path": "src/auth.py", "line": 42, "snippet": "x"},
        ]
        trace_files = [("src/auth.py", 42)]
        _, cards = rank_matches(matches, trace_files=trace_files, explain=True)
        card = cards[0]
        self.assertTrue(card["features"]["is_trace_file"])
        self.assertEqual(card["signals"]["trace_boost"], 2.0)

    def test_preferred_path_attribution(self):
        matches = [{"path": "src/handler.py", "line": 1, "snippet": "x"}]
        _, cards = rank_matches(matches, explain=True)
        card = cards[0]
        self.assertEqual(card["features"]["classification"], "preferred")
        self.assertIsNotNone(card["features"]["prefer_match"])

    def test_explain_sorted_by_score(self):
        matches = [
            {"path": "vendor/x.py", "line": 1, "snippet": "a"},
            {"path": "src/y.py", "line": 2, "snippet": "b"},
        ]
        ranked, cards = rank_matches(matches, explain=True)
        self.assertEqual(ranked[0]["path"], "src/y.py")
        self.assertEqual(cards[0]["path"], "src/y.py")
        self.assertGreaterEqual(cards[0]["score_total"], cards[1]["score_total"])


class TestCtagsRankingSignals(unittest.TestCase):
    def test_def_file_boost(self):
        """File defining the symbol should rank above non-def files."""
        matches = [
            {"path": "src/utils.py", "line": 5, "snippet": "import MyClass"},
            {"path": "src/model.py", "line": 10, "snippet": "class MyClass:"},
        ]
        ctags_info = {
            "def_files": {"src/model.py"},
            "kind_weight": 0.6,
            "best_kind": "class",
        }
        ranked = rank_matches(matches, ctags_info=ctags_info)
        self.assertEqual(ranked[0]["path"], "src/model.py")

    def test_def_file_boost_overrides_vendor_penalty(self):
        """Ctags def boost (+0.8 + kind) should overcome vendor penalty (-0.8)."""
        matches = [
            {"path": "src/handler.py", "line": 1, "snippet": "use Lib"},
            {"path": "vendor/lib.py", "line": 5, "snippet": "class Lib:"},
        ]
        ctags_info = {
            "def_files": {"vendor/lib.py"},
            "kind_weight": 0.6,
            "best_kind": "class",
        }
        ranked = rank_matches(matches, ctags_info=ctags_info)
        # vendor/lib.py: -0.8 (vendor) + 0.8 (def) + 0.6 (kind) = +0.6
        # src/handler.py: +0.5 (src) = +0.5
        self.assertEqual(ranked[0]["path"], "vendor/lib.py")

    def test_no_ctags_info_no_boost(self):
        """Without ctags_info, no structural boost applied."""
        matches = [{"path": "src/foo.py", "line": 1}]
        ranked = rank_matches(matches)
        # Score should just be path score (0.5 for src/)
        self.assertAlmostEqual(ranked[0]["_rank_score"], 0.5)

    def test_ctags_info_none_is_safe(self):
        """Passing ctags_info=None should not break anything."""
        matches = [{"path": "src/foo.py", "line": 1}]
        ranked = rank_matches(matches, ctags_info=None)
        self.assertEqual(len(ranked), 1)

    def test_explain_includes_ctags_signals(self):
        """Score cards should include ctags signal keys."""
        matches = [
            {"path": "src/model.py", "line": 10, "snippet": "class X:"},
        ]
        ctags_info = {
            "def_files": {"src/model.py"},
            "kind_weight": 0.5,
            "best_kind": "function",
        }
        _, cards = rank_matches(matches, ctags_info=ctags_info, explain=True)
        card = cards[0]
        self.assertIn("ctags_def_boost", card["signals"])
        self.assertIn("ctags_kind_boost", card["signals"])
        self.assertEqual(card["signals"]["ctags_def_boost"], 0.8)
        self.assertEqual(card["signals"]["ctags_kind_boost"], 0.5)
        self.assertTrue(card["features"]["is_def_file"])
        self.assertEqual(card["features"]["ctags_best_kind"], "function")

    def test_explain_non_def_file_zero_ctags(self):
        """Non-def files should have zero ctags signals."""
        matches = [
            {"path": "src/other.py", "line": 1, "snippet": "x"},
        ]
        ctags_info = {
            "def_files": {"src/model.py"},
            "kind_weight": 0.6,
            "best_kind": "class",
        }
        _, cards = rank_matches(matches, ctags_info=ctags_info, explain=True)
        card = cards[0]
        self.assertEqual(card["signals"]["ctags_def_boost"], 0.0)
        self.assertEqual(card["signals"]["ctags_kind_boost"], 0.0)
        self.assertFalse(card["features"]["is_def_file"])

    def test_signal_sum_includes_ctags(self):
        """Total score should equal sum of all signals including ctags."""
        matches = [
            {"path": "src/model.py", "line": 10, "snippet": "x"},
        ]
        ctags_info = {
            "def_files": {"src/model.py"},
            "kind_weight": 0.6,
            "best_kind": "class",
        }
        _, cards = rank_matches(matches, ctags_info=ctags_info, explain=True)
        card = cards[0]
        expected = sum(card["signals"].values())
        self.assertAlmostEqual(card["score_total"], expected, places=2)


class TestStructuralRankingSignals(unittest.TestCase):
    """Structural heuristic signals: def-like snippets outrank mere mentions."""

    def test_def_snippet_outranks_comment(self):
        """A snippet containing a function def should rank above a comment."""
        matches = [
            {"path": "src/utils.py", "line": 5, "snippet": "# login is deprecated"},
            {"path": "src/auth.py", "line": 10, "snippet": "def login(user):"},
        ]
        ranked = rank_matches(matches, query_symbol="login")
        self.assertEqual(ranked[0]["path"], "src/auth.py")

    def test_def_snippet_outranks_usage(self):
        """A definition snippet outranks a mere usage (call site)."""
        matches = [
            {
                "path": "src/handler.py",
                "line": 3,
                "snippet": "    result = login(user)",
            },
            {
                "path": "src/auth.py",
                "line": 10,
                "snippet": "def login(user):",
            },
        ]
        ranked = rank_matches(matches, query_symbol="login")
        self.assertEqual(ranked[0]["path"], "src/auth.py")

    def test_export_snippet_gets_boost(self):
        """An exported symbol should get an additional export boost."""
        matches = [
            {"path": "src/model.ts", "line": 1, "snippet": "class User {"},
            {"path": "src/index.ts", "line": 5, "snippet": "export class User {"},
        ]
        ranked = rank_matches(matches, query_symbol="User")
        self.assertEqual(ranked[0]["path"], "src/index.ts")

    def test_no_query_symbol_skips_structural(self):
        """Without query_symbol, structural heuristics should not fire."""
        matches = [
            {"path": "src/auth.py", "line": 10, "snippet": "def login(user):"},
        ]
        _, cards = rank_matches(matches, explain=True)
        card = cards[0]
        self.assertEqual(card["signals"]["def_likeness_boost"], 0.0)
        self.assertEqual(card["signals"]["export_boost"], 0.0)

    def test_explain_shows_structural_signals(self):
        """Score cards should reflect structural heuristic results."""
        matches = [
            {"path": "src/auth.py", "line": 10, "snippet": "def login(user):"},
        ]
        _, cards = rank_matches(matches, query_symbol="login", explain=True)
        card = cards[0]
        self.assertGreater(card["signals"]["def_likeness_boost"], 0.0)
        self.assertTrue(card["features"]["is_prob_def"])
        self.assertEqual(card["features"]["struct_rule"], "py_def")
        self.assertGreater(card["features"]["struct_def_conf"], 0.9)

    def test_export_explain_signals(self):
        """Export detection should appear in score card features."""
        matches = [
            {
                "path": "src/api.ts",
                "line": 1,
                "snippet": "export const handler = () => {",
            },
        ]
        _, cards = rank_matches(matches, query_symbol="handler", explain=True)
        card = cards[0]
        self.assertGreater(card["signals"]["export_boost"], 0.0)
        self.assertTrue(card["features"]["is_prob_export"])

    def test_signal_sum_includes_structural(self):
        """Total score should include structural boost contributions."""
        matches = [
            {"path": "src/auth.py", "line": 10, "snippet": "def login(user):"},
        ]
        _, cards = rank_matches(matches, query_symbol="login", explain=True)
        card = cards[0]
        expected = sum(card["signals"].values())
        self.assertAlmostEqual(card["score_total"], expected, places=2)

    def test_no_snippet_no_crash(self):
        """Matches without a snippet key should not crash structural eval."""
        matches = [{"path": "src/auth.py", "line": 10}]
        ranked = rank_matches(matches, query_symbol="login")
        self.assertEqual(len(ranked), 1)

    def test_call_site_outranks_comment(self):
        """A call site should rank above a mere comment mention."""
        matches = [
            {
                "path": "src/docs.py",
                "line": 5,
                "snippet": "# login is deprecated",
            },
            {
                "path": "src/handler.py",
                "line": 10,
                "snippet": "    result = login(user)",
            },
        ]
        ranked = rank_matches(matches, query_symbol="login")
        self.assertEqual(ranked[0]["path"], "src/handler.py")

    def test_def_outranks_call_site(self):
        """A definition should still rank above a call site."""
        matches = [
            {
                "path": "src/handler.py",
                "line": 10,
                "snippet": "    result = login(user)",
            },
            {
                "path": "src/auth.py",
                "line": 1,
                "snippet": "def login(user):",
            },
        ]
        ranked = rank_matches(matches, query_symbol="login")
        self.assertEqual(ranked[0]["path"], "src/auth.py")

    def test_caller_proximity_in_explain(self):
        """Score cards should show caller proximity signal."""
        matches = [
            {
                "path": "src/handler.py",
                "line": 10,
                "snippet": "    result = login(user)",
            },
        ]
        _, cards = rank_matches(matches, query_symbol="login", explain=True)
        card = cards[0]
        self.assertGreater(card["signals"]["caller_proximity_boost"], 0.0)
        self.assertTrue(card["features"]["is_prob_call"])


if __name__ == "__main__":
    unittest.main()
