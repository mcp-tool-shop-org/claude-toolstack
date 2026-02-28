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

    def test_error_mode_trace_bonus_full(self):
        """Error mode with trace detected + all trace files covered by slices."""
        b = _rich_bundle()
        b["mode"] = "error"
        b["notes"] = ["Stack trace detected: 2 file(s) extracted"]
        # Mark sources as trace targets and ensure slices cover them
        b["ranked_sources"][0]["in_trace"] = True
        b["ranked_sources"][1]["in_trace"] = True
        conf = bundle_confidence(b)
        # 0.05 (trace note) + 0.10 (2/2 covered) = 0.15
        self.assertEqual(conf["signals"]["mode_bonus"], 0.15)

    def test_error_mode_trace_bonus_partial(self):
        """Error mode with trace but no in_trace markers → only note bonus."""
        b = _rich_bundle()
        b["mode"] = "error"
        b["notes"] = ["Stack trace detected: 2 file(s) extracted"]
        conf = bundle_confidence(b)
        # Only 0.05 for trace note, no in_trace markers
        self.assertEqual(conf["signals"]["mode_bonus"], 0.05)

    def test_symbol_mode_bonus_full(self):
        """Symbol mode with defs + callers all covered by slices."""
        b = _rich_bundle()
        b["mode"] = "symbol"
        b["symbols"] = [
            {"name": "login", "kind": "function", "file": "src/auth.py"},
        ]
        # matches are callers (different from def file)
        b["matches"] = [
            {"path": "src/session.py", "line": 5, "snippet": "login(user)"},
        ]
        # slices cover both def file and caller file
        conf = bundle_confidence(b)
        # 0.05 (symbols) + 0.05 (def covered) + 0.05 (caller covered) = 0.15
        self.assertEqual(conf["signals"]["mode_bonus"], 0.15)

    def test_symbol_mode_bonus_defs_only(self):
        """Symbol mode with defs but def file not in slices."""
        b = _rich_bundle()
        b["mode"] = "symbol"
        b["symbols"] = [
            {"name": "login", "kind": "function", "file": "missing.py"},
        ]
        conf = bundle_confidence(b)
        # 0.05 (symbols) + 0.0 (missing.py not in slices) = 0.05+partial
        self.assertGreaterEqual(conf["signals"]["mode_bonus"], 0.05)
        self.assertLess(conf["signals"]["mode_bonus"], 0.15)

    def test_no_mode_bonus_for_default(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertEqual(conf["signals"]["mode_bonus"], 0.0)

    def test_match_penalty_zero_matches(self):
        conf = bundle_confidence(_empty_bundle())
        self.assertLess(conf["signals"]["low_match_penalty"], 0.0)

    def test_match_penalty_sufficient_matches(self):
        conf = bundle_confidence(_rich_bundle())
        self.assertEqual(conf["signals"]["low_match_penalty"], 0.0)


    def test_change_mode_bonus_full(self):
        """Change mode with diff + all changed files covered."""
        b = _rich_bundle()
        b["mode"] = "change"
        b["diff"] = "+++ b/src/auth.py\n- old\n+ new\n"
        conf = bundle_confidence(b)
        # 0.05 (diff) + 0.10 * (3/3 covered) = 0.15
        self.assertEqual(conf["signals"]["mode_bonus"], 0.15)

    def test_change_mode_bonus_no_diff(self):
        """Change mode without diff → no bonus."""
        b = _rich_bundle()
        b["mode"] = "change"
        conf = bundle_confidence(b)
        # No diff text → only coverage part (sources exist in slices)
        self.assertLess(conf["signals"]["mode_bonus"], 0.15)

    def test_change_mode_bonus_no_slices(self):
        """Change mode with diff but no slice coverage."""
        b = _sparse_bundle()
        b["mode"] = "change"
        b["diff"] = "+++ b/src/x.py\n+ new\n"
        conf = bundle_confidence(b)
        # 0.05 (diff) + 0.0 (no slices) = 0.05
        self.assertEqual(conf["signals"]["mode_bonus"], 0.05)


if __name__ == "__main__":
    unittest.main()
