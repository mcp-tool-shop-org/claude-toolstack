"""Tests for cts.confidence — bundle confidence scoring."""

from __future__ import annotations

import unittest

from cts.confidence import SUFFICIENT_THRESHOLD, bundle_confidence


def _rich_bundle() -> dict:
    """A well-populated bundle that should score high."""
    return {
        "version": 2,
        "mode": "default",
        "repo": "org/repo",
        "request_id": "r1",
        "query": "login",
        "ranked_sources": [
            {"path": "src/auth.py", "line": 10, "score": 1.8},
            {"path": "src/session.py", "line": 5, "score": 1.0},
            {"path": "lib/utils.py", "line": 20, "score": 0.7},
        ],
        "matches": [
            {"path": "src/auth.py", "line": 10, "snippet": "def login():"},
            {"path": "src/session.py", "line": 5, "snippet": "login(user)"},
            {"path": "lib/utils.py", "line": 20, "snippet": "validate(login)"},
        ],
        "slices": [
            {"path": "src/auth.py", "start": 1, "lines": ["..."] * 30},
            {"path": "src/session.py", "start": 1, "lines": ["..."] * 30},
            {"path": "lib/utils.py", "start": 1, "lines": ["..."] * 30},
        ],
        "symbols": [],
        "diff": "",
        "suggested_commands": [],
        "notes": [],
        "truncated": False,
    }


def _sparse_bundle() -> dict:
    """A bundle with very few results."""
    return {
        "version": 2,
        "mode": "default",
        "repo": "org/repo",
        "request_id": "r2",
        "query": "obscure_symbol",
        "ranked_sources": [
            {"path": "src/x.py", "line": 1, "score": 0.1},
        ],
        "matches": [
            {"path": "src/x.py", "line": 1, "snippet": "# obscure_symbol"},
        ],
        "slices": [],
        "symbols": [],
        "diff": "",
        "suggested_commands": [],
        "notes": [],
        "truncated": False,
    }


def _empty_bundle() -> dict:
    """A bundle with no results."""
    return {
        "version": 2,
        "mode": "default",
        "repo": "org/repo",
        "request_id": "r3",
        "query": "nonexistent",
        "ranked_sources": [],
        "matches": [],
        "slices": [],
        "symbols": [],
        "diff": "",
        "suggested_commands": [],
        "notes": [],
        "truncated": False,
    }


class TestBundleConfidence(unittest.TestCase):
    def test_rich_bundle_is_sufficient(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertTrue(conf["sufficient"])
        self.assertGreaterEqual(conf["score"], SUFFICIENT_THRESHOLD)

    def test_empty_bundle_is_insufficient(self):
        conf = bundle_confidence(_empty_bundle())
        self.assertFalse(conf["sufficient"])
        self.assertLess(conf["score"], SUFFICIENT_THRESHOLD)

    def test_sparse_bundle_is_insufficient(self):
        conf = bundle_confidence(_sparse_bundle())
        self.assertFalse(conf["sufficient"])

    def test_score_range(self):
        for bundle_fn in (_rich_bundle, _sparse_bundle, _empty_bundle):
            conf = bundle_confidence(bundle_fn())
            self.assertGreaterEqual(conf["score"], 0.0)
            self.assertLessEqual(conf["score"], 1.0)

    def test_signals_present(self):
        conf = bundle_confidence(_rich_bundle())
        signals = conf["signals"]
        expected_keys = {
            "top_score_weight",
            "definition_found",
            "source_diversity",
            "slice_coverage",
            "low_match_penalty",
            "mode_bonus",
        }
        self.assertTrue(expected_keys.issubset(set(signals.keys())))

    def test_reason_string_present(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertIsInstance(conf["reason"], str)
        self.assertGreater(len(conf["reason"]), 0)

    def test_definition_found_with_score_cards(self):
        cards = [
            {
                "path": "src/auth.py",
                "score_total": 1.2,
                "features": {"is_prob_def": True, "is_def_file": False},
            }
        ]
        conf = bundle_confidence(_rich_bundle(), score_cards=cards)
        self.assertEqual(conf["signals"]["definition_found"], 0.2)

    def test_definition_not_found_without_cards(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertEqual(conf["signals"]["definition_found"], 0.0)

    def test_error_mode_trace_bonus(self):
        b = _rich_bundle()
        b["mode"] = "error"
        b["notes"] = ["Stack trace detected: 3 file(s) extracted"]
        conf = bundle_confidence(b)
        self.assertEqual(conf["signals"]["mode_bonus"], 0.15)

    def test_symbol_mode_bonus(self):
        b = _rich_bundle()
        b["mode"] = "symbol"
        b["symbols"] = [{"name": "login", "kind": "function", "file": "a.py"}]
        conf = bundle_confidence(b)
        self.assertEqual(conf["signals"]["mode_bonus"], 0.15)

    def test_no_mode_bonus_for_default(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertEqual(conf["signals"]["mode_bonus"], 0.0)

    def test_match_penalty_zero_matches(self):
        conf = bundle_confidence(_empty_bundle())
        self.assertLess(conf["signals"]["low_match_penalty"], 0.0)

    def test_match_penalty_sufficient_matches(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertEqual(conf["signals"]["low_match_penalty"], 0.0)


if __name__ == "__main__":
    unittest.main()
