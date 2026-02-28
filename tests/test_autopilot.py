"""Tests for cts.autopilot — refinement planning and execution."""

from __future__ import annotations

import unittest

from cts.autopilot import (
    DEFAULT_MAX_EXTRA_SLICES,
    apply_refinement,
    execute_refinements,
    plan_refinements,
)
from cts.confidence import bundle_confidence


def _low_conf_bundle() -> dict:
    """Bundle with low confidence (few matches, low scores)."""
    return {
        "version": 2,
        "mode": "default",
        "repo": "org/repo",
        "request_id": "r1",
        "query": "obscureFunc",
        "ranked_sources": [
            {"path": "src/x.py", "line": 1, "score": 0.1},
        ],
        "matches": [
            {"path": "src/x.py", "line": 1, "snippet": "# obscureFunc ref"},
        ],
        "slices": [],
        "symbols": [],
        "diff": "",
        "suggested_commands": [],
        "notes": [],
        "truncated": False,
    }


def _high_conf_bundle() -> dict:
    """Bundle with high confidence."""
    return {
        "version": 2,
        "mode": "default",
        "repo": "org/repo",
        "request_id": "r2",
        "query": "login",
        "ranked_sources": [
            {"path": "src/auth.py", "line": 10, "score": 1.5},
            {"path": "src/session.py", "line": 5, "score": 0.8},
            {"path": "lib/utils.py", "line": 20, "score": 0.5},
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


class TestPlanRefinements(unittest.TestCase):
    def test_sufficient_confidence_returns_empty(self):
        b = _high_conf_bundle()
        conf = bundle_confidence(b)
        # Force sufficient
        conf["sufficient"] = True
        conf["score"] = 0.8
        actions = plan_refinements(b, conf)
        self.assertEqual(actions, [])

    def test_low_confidence_suggests_actions(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf, current_params={"max_matches": 50})
        self.assertGreater(len(actions), 0)

    def test_widen_search_suggested_for_few_sources(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf, current_params={"max_matches": 50})
        names = [a["name"] for a in actions]
        self.assertIn("widen_search", names)

    def test_add_slices_suggested_for_no_slices(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertIn("add_slices", names)

    def test_try_symbol_suggested_for_symbol_like_query(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        # Force definition_found = 0
        conf["signals"]["definition_found"] = 0.0
        # Use high max_matches so widen_search doesn't consume a slot
        actions = plan_refinements(b, conf, current_params={"max_matches": 200})
        names = [a["name"] for a in actions]
        # obscureFunc looks like a symbol
        self.assertIn("try_symbol", names)

    def test_max_two_actions_per_pass(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf, current_params={"max_matches": 50})
        self.assertLessEqual(len(actions), 2)

    def test_broaden_glob_on_later_pass(self):
        # Bundle with few sources, some slices (so add_slices doesn't fire),
        # no symbol-like query (so try_symbol doesn't fire), and globs present
        b = _low_conf_bundle()
        b["query"] = "some complex query"  # not a symbol
        b["slices"] = [
            {"path": "src/x.py", "start": 1, "lines": ["x"] * 10},
            {"path": "src/y.py", "start": 1, "lines": ["y"] * 10},
            {"path": "src/z.py", "start": 1, "lines": ["z"] * 10},
        ]
        conf = bundle_confidence(b)
        actions = plan_refinements(
            b,
            conf,
            current_params={"max_matches": 200, "path_globs": ["*.py"]},
            pass_number=2,
        )
        names = [a["name"] for a in actions]
        self.assertIn("broaden_glob", names)


class TestApplyRefinement(unittest.TestCase):
    def test_widen_search_doubles_max(self):
        params = {"max_matches": 50}
        new = apply_refinement(params, {"name": "widen_search"})
        self.assertEqual(new["max_matches"], 100)
        # Original unchanged
        self.assertEqual(params["max_matches"], 50)

    def test_widen_search_caps_at_200(self):
        params = {"max_matches": 150}
        new = apply_refinement(params, {"name": "widen_search"})
        self.assertEqual(new["max_matches"], 200)

    def test_add_slices_increases_evidence_files(self):
        params = {"evidence_files": 5}
        new = apply_refinement(params, {"name": "add_slices"})
        self.assertEqual(new["evidence_files"], 5 + DEFAULT_MAX_EXTRA_SLICES)

    def test_add_slices_caps_at_15(self):
        params = {"evidence_files": 12}
        new = apply_refinement(params, {"name": "add_slices"})
        self.assertEqual(new["evidence_files"], 15)

    def test_broaden_glob_removes_globs(self):
        params = {"path_globs": ["*.py", "src/**"]}
        new = apply_refinement(params, {"name": "broaden_glob"})
        self.assertNotIn("path_globs", new)

    def test_try_symbol_no_param_change(self):
        params = {"max_matches": 50}
        new = apply_refinement(params, {"name": "try_symbol"})
        self.assertEqual(new, params)

    def test_original_not_mutated(self):
        params = {"max_matches": 50, "evidence_files": 5}
        apply_refinement(params, {"name": "widen_search"})
        self.assertEqual(params["max_matches"], 50)


class TestExecuteRefinements(unittest.TestCase):
    def _build_fn(self, **kwargs) -> dict:
        """Mock build function that returns a progressively better bundle."""
        max_matches = kwargs.get("max_matches", 50)
        n = min(max_matches // 10, 5)
        sources = [
            {"path": f"src/f{i}.py", "line": i * 10, "score": 0.3 * (i + 1)}
            for i in range(n)
        ]
        matches = [
            {
                "path": f"src/f{i}.py",
                "line": i * 10,
                "snippet": f"func_{i}()",
            }
            for i in range(n)
        ]
        slices = [
            {"path": f"src/f{i}.py", "start": 1, "lines": ["..."] * 10}
            for i in range(min(n, kwargs.get("evidence_files", 5)))
        ]
        return {
            "version": 2,
            "mode": "default",
            "repo": kwargs.get("repo", "org/repo"),
            "request_id": kwargs.get("request_id", ""),
            "query": kwargs.get("query", "func"),
            "ranked_sources": sources,
            "matches": matches,
            "slices": slices,
            "symbols": [],
            "diff": "",
            "suggested_commands": [],
            "notes": [],
            "truncated": False,
        }

    def test_no_passes_when_already_sufficient(self):
        result = execute_refinements(
            _high_conf_bundle(),
            build_fn=self._build_fn,
            build_kwargs={"max_matches": 50},
            max_passes=2,
        )
        self.assertEqual(result["total_passes"], 0)
        self.assertEqual(result["passes"], [])

    def test_executes_passes_when_insufficient(self):
        result = execute_refinements(
            _low_conf_bundle(),
            build_fn=self._build_fn,
            build_kwargs={"max_matches": 50, "evidence_files": 5},
            max_passes=2,
        )
        self.assertGreater(result["total_passes"], 0)
        self.assertGreater(len(result["passes"]), 0)

    def test_respects_max_passes(self):
        result = execute_refinements(
            _low_conf_bundle(),
            build_fn=self._build_fn,
            build_kwargs={"max_matches": 10, "evidence_files": 1},
            max_passes=1,
        )
        self.assertLessEqual(result["total_passes"], 1)

    def test_respects_time_budget(self):
        result = execute_refinements(
            _low_conf_bundle(),
            build_fn=self._build_fn,
            build_kwargs={"max_matches": 50},
            max_passes=5,
            max_seconds=0.001,  # Very tight budget
        )
        # Should stop quickly
        self.assertLessEqual(result["total_passes"], 1)

    def test_pass_records_contain_actions(self):
        result = execute_refinements(
            _low_conf_bundle(),
            build_fn=self._build_fn,
            build_kwargs={"max_matches": 50, "evidence_files": 5},
            max_passes=2,
        )
        for p in result["passes"]:
            self.assertIn("pass", p)
            self.assertIn("actions", p)
            self.assertIn("confidence_before", p)
            self.assertIn("status", p)
            self.assertIsInstance(p["actions"], list)

    def test_returns_final_confidence(self):
        result = execute_refinements(
            _low_conf_bundle(),
            build_fn=self._build_fn,
            build_kwargs={"max_matches": 50},
            max_passes=2,
        )
        self.assertIn("confidence", result)
        self.assertIn("score", result["confidence"])
        self.assertIn("sufficient", result["confidence"])

    def test_handles_build_fn_error(self):
        def failing_build(**kwargs):
            raise RuntimeError("gateway down")

        result = execute_refinements(
            _low_conf_bundle(),
            build_fn=failing_build,
            build_kwargs={"max_matches": 50},
            max_passes=1,
        )
        # Should record the error, not crash
        self.assertEqual(len(result["passes"]), 1)
        self.assertEqual(result["passes"][0]["status"], "error")


class TestAutopilotCliArgs(unittest.TestCase):
    def test_autopilot_flag_accepted(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "search",
                "login",
                "--repo",
                "org/repo",
                "--autopilot",
                "3",
            ]
        )
        self.assertEqual(args.autopilot, 3)

    def test_autopilot_default_zero(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["search", "login", "--repo", "org/repo"])
        self.assertEqual(args.autopilot, 0)

    def test_autopilot_max_seconds_flag(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "search",
                "login",
                "--repo",
                "org/repo",
                "--autopilot",
                "2",
                "--autopilot-max-seconds",
                "15",
            ]
        )
        self.assertEqual(args.autopilot_max_seconds, 15.0)

    def test_autopilot_max_extra_slices_flag(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "search",
                "login",
                "--repo",
                "org/repo",
                "--autopilot-max-extra-slices",
                "10",
            ]
        )
        self.assertEqual(args.autopilot_max_extra_slices, 10)


if __name__ == "__main__":
    unittest.main()
