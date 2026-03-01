"""Tests for cts.autopilot — refinement planning and execution."""

from __future__ import annotations

import unittest

from cts.autopilot import (
    DEFAULT_MAX_EXTRA_SLICES,
    _count_diff_idents,
    _find_uncovered_caller_files,
    _find_uncovered_changed_files,
    _find_uncovered_def_files,
    _find_uncovered_trace_targets,
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


# ---------------------------------------------------------------------------
# Error-mode helpers
# ---------------------------------------------------------------------------


def _error_bundle_with_trace(*, slices_cover_trace: bool = False) -> dict:
    """Error bundle with trace markers on ranked_sources.

    If slices_cover_trace is False, trace files are NOT in slices
    (so force_trace_slices should trigger). Scores are kept low
    to ensure confidence stays below threshold (0.6) when uncovered.
    """
    slices = []
    if slices_cover_trace:
        slices = [
            {"path": "app/handler.py", "start": 1, "lines": ["..."] * 20},
            {"path": "lib/db.py", "start": 1, "lines": ["..."] * 20},
        ]
    else:
        slices = []  # No slices at all → low slice_coverage

    return {
        "version": 2,
        "mode": "error",
        "repo": "org/repo",
        "request_id": "e1",
        "query": "ConnectionError",
        "ranked_sources": [
            {"path": "app/handler.py", "line": 42, "score": 0.3, "in_trace": True},
            {"path": "lib/db.py", "line": 15, "score": 0.2, "in_trace": True},
        ],
        "matches": [
            {"path": "app/handler.py", "line": 42, "snippet": "raise ConnectionError"},
            {"path": "lib/db.py", "line": 15, "snippet": "conn = db.connect()"},
        ],
        "slices": slices,
        "symbols": [],
        "diff": "",
        "suggested_commands": [],
        "notes": ["Stack trace detected: 2 file(s) extracted"],
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Tests: _find_uncovered_trace_targets
# ---------------------------------------------------------------------------


class TestFindUncoveredTraceTargets(unittest.TestCase):
    def test_returns_uncovered_trace_files(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        uncovered = _find_uncovered_trace_targets(b)
        paths = [t["path"] for t in uncovered]
        self.assertIn("app/handler.py", paths)
        self.assertIn("lib/db.py", paths)

    def test_returns_empty_when_trace_covered(self):
        b = _error_bundle_with_trace(slices_cover_trace=True)
        uncovered = _find_uncovered_trace_targets(b)
        self.assertEqual(uncovered, [])

    def test_returns_empty_for_no_trace_markers(self):
        b = _low_conf_bundle()  # default mode, no in_trace
        uncovered = _find_uncovered_trace_targets(b)
        self.assertEqual(uncovered, [])

    def test_partial_coverage_returns_only_uncovered(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        # Cover one trace file manually
        b["slices"] = [
            {"path": "app/handler.py", "start": 1, "lines": ["..."] * 10},
        ]
        uncovered = _find_uncovered_trace_targets(b)
        paths = [t["path"] for t in uncovered]
        self.assertNotIn("app/handler.py", paths)
        self.assertIn("lib/db.py", paths)

    def test_includes_line_numbers(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        uncovered = _find_uncovered_trace_targets(b)
        handler = [t for t in uncovered if t["path"] == "app/handler.py"]
        self.assertEqual(len(handler), 1)
        self.assertEqual(handler[0]["line"], 42)


# ---------------------------------------------------------------------------
# Tests: force_trace_slices planner integration
# ---------------------------------------------------------------------------


class TestForceTraceSlicesPlanner(unittest.TestCase):
    def test_error_mode_triggers_force_trace_slices(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertIn("force_trace_slices", names)

    def test_error_mode_no_action_when_covered(self):
        b = _error_bundle_with_trace(slices_cover_trace=True)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertNotIn("force_trace_slices", names)

    def test_force_trace_slices_is_highest_priority(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        # force_trace_slices should be the first action
        self.assertEqual(actions[0]["name"], "force_trace_slices")

    def test_force_trace_slices_carries_targets(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        fts = [a for a in actions if a["name"] == "force_trace_slices"][0]
        self.assertIn("trace_targets", fts)
        self.assertEqual(len(fts["trace_targets"]), 2)

    def test_default_mode_never_triggers_force_trace_slices(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertNotIn("force_trace_slices", names)


# ---------------------------------------------------------------------------
# Tests: force_trace_slices apply_refinement
# ---------------------------------------------------------------------------


class TestForceTraceSlicesApply(unittest.TestCase):
    def test_increases_evidence_files(self):
        action = {
            "name": "force_trace_slices",
            "trace_targets": [
                {"path": "a.py", "line": 1},
                {"path": "b.py", "line": 2},
            ],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["evidence_files"], 7)  # 5 + 2 targets

    def test_caps_evidence_files_at_15(self):
        action = {
            "name": "force_trace_slices",
            "trace_targets": [{"path": f"f{i}.py", "line": i} for i in range(10)],
        }
        params = {"evidence_files": 12}
        new = apply_refinement(params, action)
        self.assertEqual(new["evidence_files"], 15)

    def test_caps_extra_at_max_extra_slices(self):
        action = {
            "name": "force_trace_slices",
            "trace_targets": [{"path": f"f{i}.py", "line": i} for i in range(10)],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        # min(10 targets, DEFAULT_MAX_EXTRA_SLICES=5) + 5 = 10
        self.assertEqual(new["evidence_files"], 10)

    def test_stores_force_slice_paths(self):
        action = {
            "name": "force_trace_slices",
            "trace_targets": [
                {"path": "a.py", "line": 1},
                {"path": "b.py", "line": 2},
            ],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["_force_slice_paths"], ["a.py", "b.py"])

    def test_original_not_mutated(self):
        action = {
            "name": "force_trace_slices",
            "trace_targets": [{"path": "a.py", "line": 1}],
        }
        params = {"evidence_files": 5}
        apply_refinement(params, action)
        self.assertEqual(params["evidence_files"], 5)
        self.assertNotIn("_force_slice_paths", params)


# ---------------------------------------------------------------------------
# Tests: execute_refinements with error-mode bundle
# ---------------------------------------------------------------------------


class TestForceTraceSlicesExecution(unittest.TestCase):
    def _build_fn_error(self, **kwargs) -> dict:
        """Mock build fn that adds trace-targeted slices when forced."""
        force_paths = kwargs.get("_force_slice_paths", [])
        evidence_files = kwargs.get("evidence_files", 5)
        slices = []
        if force_paths:
            for fp in force_paths:
                slices.append({"path": fp, "start": 1, "lines": ["..."] * 20})
        # Also add some generic slices
        for i in range(min(evidence_files - len(slices), 2)):
            slices.append({"path": f"gen/f{i}.py", "start": 1, "lines": ["..."] * 10})

        return {
            "version": 2,
            "mode": "error",
            "repo": kwargs.get("repo", "org/repo"),
            "request_id": kwargs.get("request_id", ""),
            "query": kwargs.get("query", "ConnectionError"),
            "ranked_sources": [
                {
                    "path": "app/handler.py",
                    "line": 42,
                    "score": 1.2,
                    "in_trace": True,
                },
                {
                    "path": "lib/db.py",
                    "line": 15,
                    "score": 0.9,
                    "in_trace": True,
                },
                {"path": "gen/f0.py", "line": 1, "score": 0.3},
            ],
            "matches": [
                {"path": "app/handler.py", "line": 42, "snippet": "raise Err"},
                {"path": "lib/db.py", "line": 15, "snippet": "conn"},
                {"path": "gen/f0.py", "line": 1, "snippet": "# gen"},
            ],
            "slices": slices,
            "symbols": [],
            "diff": "",
            "suggested_commands": [],
            "notes": ["Stack trace detected: 2 file(s) extracted"],
            "truncated": False,
        }

    def test_pass_records_include_action_details(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        result = execute_refinements(
            b,
            build_fn=self._build_fn_error,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        self.assertGreater(len(result["passes"]), 0)
        p = result["passes"][0]
        self.assertIn("action_details", p)
        fts = [d for d in p["action_details"] if d["name"] == "force_trace_slices"]
        self.assertEqual(len(fts), 1)
        self.assertIn("trace_targets", fts[0])
        self.assertGreater(fts[0]["trace_targets_count"], 0)

    def test_trace_slices_added_after_execution(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        result = execute_refinements(
            b,
            build_fn=self._build_fn_error,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        final = result["final_bundle"]
        slice_paths = [s["path"] for s in final["slices"]]
        self.assertIn("app/handler.py", slice_paths)
        self.assertIn("lib/db.py", slice_paths)


# ---------------------------------------------------------------------------
# Symbol-mode helpers
# ---------------------------------------------------------------------------


def _symbol_bundle(*, defs_in_slices: bool = False, callers_in_slices: bool = False):
    """Symbol bundle with definitions and callers.

    Scores are low so confidence is below threshold. Defs are in symbols[],
    callers are in matches[].
    """
    slices = []
    if defs_in_slices:
        slices.append({"path": "lib/parser.py", "start": 1, "lines": ["..."] * 20})
    if callers_in_slices:
        slices.extend(
            [
                {"path": "src/main.py", "start": 1, "lines": ["..."] * 10},
                {"path": "src/handler.py", "start": 1, "lines": ["..."] * 10},
                {"path": "tests/test_p.py", "start": 1, "lines": ["..."] * 10},
            ]
        )

    return {
        "version": 2,
        "mode": "symbol",
        "repo": "org/repo",
        "request_id": "s1",
        "query": "parse_config",
        "ranked_sources": [
            {"path": "lib/parser.py", "line": 10, "score": 0.3},
            {"path": "src/main.py", "line": 42, "score": 0.2},
        ],
        "matches": [
            {"path": "src/main.py", "line": 42, "snippet": "parse_config(data)"},
            {"path": "src/handler.py", "line": 7, "snippet": "cfg = parse_config(f)"},
            {"path": "tests/test_p.py", "line": 3, "snippet": "parse_config({})"},
        ],
        "slices": slices,
        "symbols": [
            {"name": "parse_config", "kind": "function", "file": "lib/parser.py"},
        ],
        "diff": "",
        "suggested_commands": [],
        "notes": [],
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Tests: _find_uncovered_def_files
# ---------------------------------------------------------------------------


class TestFindUncoveredDefFiles(unittest.TestCase):
    def test_returns_uncovered_def_files(self):
        b = _symbol_bundle(defs_in_slices=False)
        uncov = _find_uncovered_def_files(b)
        paths = [t["path"] for t in uncov]
        self.assertIn("lib/parser.py", paths)

    def test_returns_empty_when_defs_covered(self):
        b = _symbol_bundle(defs_in_slices=True)
        uncov = _find_uncovered_def_files(b)
        self.assertEqual(uncov, [])

    def test_returns_empty_for_no_symbols(self):
        b = _low_conf_bundle()  # default mode, no symbols
        uncov = _find_uncovered_def_files(b)
        self.assertEqual(uncov, [])

    def test_deduplicates_def_files(self):
        b = _symbol_bundle(defs_in_slices=False)
        # Add duplicate symbol for same file
        b["symbols"].append(
            {"name": "parse_config_v2", "kind": "function", "file": "lib/parser.py"}
        )
        uncov = _find_uncovered_def_files(b)
        paths = [t["path"] for t in uncov]
        self.assertEqual(paths.count("lib/parser.py"), 1)


# ---------------------------------------------------------------------------
# Tests: _find_uncovered_caller_files
# ---------------------------------------------------------------------------


class TestFindUncoveredCallerFiles(unittest.TestCase):
    def test_returns_uncovered_callers(self):
        b = _symbol_bundle(defs_in_slices=True, callers_in_slices=False)
        uncov = _find_uncovered_caller_files(b)
        paths = [t["path"] for t in uncov]
        self.assertIn("src/main.py", paths)
        self.assertIn("src/handler.py", paths)

    def test_excludes_def_files(self):
        b = _symbol_bundle(defs_in_slices=False, callers_in_slices=False)
        uncov = _find_uncovered_caller_files(b)
        paths = [t["path"] for t in uncov]
        # lib/parser.py is a def file, should not appear as caller
        self.assertNotIn("lib/parser.py", paths)

    def test_returns_empty_when_callers_covered(self):
        b = _symbol_bundle(defs_in_slices=True, callers_in_slices=True)
        uncov = _find_uncovered_caller_files(b)
        self.assertEqual(uncov, [])

    def test_respects_max_callers(self):
        b = _symbol_bundle(defs_in_slices=True, callers_in_slices=False)
        uncov = _find_uncovered_caller_files(b, max_callers=1)
        self.assertLessEqual(len(uncov), 1)

    def test_includes_line_numbers(self):
        b = _symbol_bundle(defs_in_slices=True, callers_in_slices=False)
        uncov = _find_uncovered_caller_files(b)
        main = [t for t in uncov if t["path"] == "src/main.py"]
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0]["line"], 42)


# ---------------------------------------------------------------------------
# Tests: symbol-mode planner integration
# ---------------------------------------------------------------------------


class TestSymbolModePlanner(unittest.TestCase):
    def test_symbol_mode_triggers_pin_def_slices(self):
        b = _symbol_bundle(defs_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertIn("pin_def_slices", names)

    def test_symbol_mode_triggers_expand_callers(self):
        b = _symbol_bundle(defs_in_slices=True, callers_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertIn("expand_callers", names)

    def test_symbol_mode_both_actions_when_uncovered(self):
        b = _symbol_bundle(defs_in_slices=False, callers_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        # Both should be present (2-action cap, symbol actions fill both slots)
        self.assertIn("pin_def_slices", names)
        self.assertIn("expand_callers", names)

    def test_pin_def_slices_is_first_priority(self):
        b = _symbol_bundle(defs_in_slices=False, callers_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        self.assertEqual(actions[0]["name"], "pin_def_slices")

    def test_no_symbol_actions_for_default_mode(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertNotIn("pin_def_slices", names)
        self.assertNotIn("expand_callers", names)

    def test_pin_def_slices_carries_targets(self):
        b = _symbol_bundle(defs_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        pds = [a for a in actions if a["name"] == "pin_def_slices"][0]
        self.assertIn("def_targets", pds)
        paths = [t["path"] for t in pds["def_targets"]]
        self.assertIn("lib/parser.py", paths)

    def test_expand_callers_carries_targets(self):
        b = _symbol_bundle(defs_in_slices=True, callers_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        ec = [a for a in actions if a["name"] == "expand_callers"][0]
        self.assertIn("caller_targets", ec)
        self.assertGreater(len(ec["caller_targets"]), 0)


# ---------------------------------------------------------------------------
# Tests: symbol-mode apply_refinement
# ---------------------------------------------------------------------------


class TestSymbolModeApply(unittest.TestCase):
    def test_pin_def_slices_increases_evidence_files(self):
        action = {
            "name": "pin_def_slices",
            "def_targets": [{"path": "lib/parser.py", "line": 1}],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["evidence_files"], 6)

    def test_pin_def_slices_stores_force_paths(self):
        action = {
            "name": "pin_def_slices",
            "def_targets": [{"path": "lib/parser.py", "line": 1}],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["_force_slice_paths"], ["lib/parser.py"])

    def test_expand_callers_increases_evidence_files(self):
        action = {
            "name": "expand_callers",
            "caller_targets": [
                {"path": "src/main.py", "line": 42},
                {"path": "src/handler.py", "line": 7},
            ],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["evidence_files"], 7)

    def test_expand_callers_appends_to_existing_force_paths(self):
        action = {
            "name": "expand_callers",
            "caller_targets": [{"path": "src/main.py", "line": 42}],
        }
        # Simulate pin_def_slices already applied
        params = {
            "evidence_files": 6,
            "_force_slice_paths": ["lib/parser.py"],
        }
        new = apply_refinement(params, action)
        self.assertEqual(new["_force_slice_paths"], ["lib/parser.py", "src/main.py"])

    def test_expand_callers_caps_at_15(self):
        action = {
            "name": "expand_callers",
            "caller_targets": [{"path": f"f{i}.py", "line": i} for i in range(10)],
        }
        params = {"evidence_files": 12}
        new = apply_refinement(params, action)
        self.assertEqual(new["evidence_files"], 15)


# ---------------------------------------------------------------------------
# Tests: symbol-mode execution
# ---------------------------------------------------------------------------


class TestSymbolModeExecution(unittest.TestCase):
    def _build_fn_symbol(self, **kwargs) -> dict:
        """Mock build fn that adds def/caller slices when forced."""
        force_paths = kwargs.get("_force_slice_paths", [])
        evidence_files = kwargs.get("evidence_files", 5)
        slices = []
        for fp in force_paths:
            slices.append({"path": fp, "start": 1, "lines": ["..."] * 20})
        # Fill remaining
        for i in range(min(evidence_files - len(slices), 1)):
            slices.append({"path": f"gen/f{i}.py", "start": 1, "lines": ["..."] * 10})

        return {
            "version": 2,
            "mode": "symbol",
            "repo": kwargs.get("repo", "org/repo"),
            "request_id": "",
            "query": "parse_config",
            "ranked_sources": [
                {"path": "lib/parser.py", "line": 10, "score": 0.3},
            ],
            "matches": [
                {"path": "src/main.py", "line": 42, "snippet": "parse_config(d)"},
            ],
            "slices": slices,
            "symbols": [
                {"name": "parse_config", "kind": "function", "file": "lib/parser.py"},
            ],
            "diff": "",
            "suggested_commands": [],
            "notes": [],
            "truncated": False,
        }

    def test_pass_records_include_def_details(self):
        b = _symbol_bundle(defs_in_slices=False)
        result = execute_refinements(
            b,
            build_fn=self._build_fn_symbol,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        self.assertGreater(len(result["passes"]), 0)
        p = result["passes"][0]
        pds = [d for d in p["action_details"] if d["name"] == "pin_def_slices"]
        self.assertEqual(len(pds), 1)
        self.assertIn("def_targets", pds[0])

    def test_def_slices_added_after_execution(self):
        b = _symbol_bundle(defs_in_slices=False)
        result = execute_refinements(
            b,
            build_fn=self._build_fn_symbol,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        final = result["final_bundle"]
        slice_paths = [s["path"] for s in final["slices"]]
        self.assertIn("lib/parser.py", slice_paths)


# ---------------------------------------------------------------------------
# Change-mode helpers
# ---------------------------------------------------------------------------

_SAMPLE_DIFF = """\
--- a/src/config.py
+++ b/src/config.py
@@ -10,7 +10,7 @@
-    timeout = 30
+    timeout = get_timeout()
--- a/src/server.py
+++ b/src/server.py
@@ -5,6 +5,8 @@
+import logging
+logger = logging.getLogger(__name__)
"""


def _change_bundle(*, changed_in_slices: bool = False) -> dict:
    """Change bundle with diff and changed file sources.

    Scores are low so confidence is below threshold.
    """
    slices = []
    if changed_in_slices:
        slices = [
            {"path": "src/config.py", "start": 1, "lines": ["..."] * 20},
            {"path": "src/server.py", "start": 1, "lines": ["..."] * 20},
        ]

    return {
        "version": 2,
        "mode": "change",
        "repo": "org/repo",
        "request_id": "c1",
        "query": "",
        "ranked_sources": [
            {"path": "src/config.py", "line": 10, "score": 0.0},
            {"path": "src/server.py", "line": 5, "score": 0.0},
        ],
        "matches": [],
        "slices": slices,
        "symbols": [],
        "diff": _SAMPLE_DIFF,
        "suggested_commands": [],
        "notes": ["2 file(s) changed"],
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Tests: _find_uncovered_changed_files
# ---------------------------------------------------------------------------


class TestFindUncoveredChangedFiles(unittest.TestCase):
    def test_returns_uncovered_changed_files(self):
        b = _change_bundle(changed_in_slices=False)
        uncov = _find_uncovered_changed_files(b)
        paths = [t["path"] for t in uncov]
        self.assertIn("src/config.py", paths)
        self.assertIn("src/server.py", paths)

    def test_returns_empty_when_covered(self):
        b = _change_bundle(changed_in_slices=True)
        uncov = _find_uncovered_changed_files(b)
        self.assertEqual(uncov, [])

    def test_partial_coverage(self):
        b = _change_bundle(changed_in_slices=False)
        b["slices"] = [
            {"path": "src/config.py", "start": 1, "lines": ["..."] * 10},
        ]
        uncov = _find_uncovered_changed_files(b)
        paths = [t["path"] for t in uncov]
        self.assertNotIn("src/config.py", paths)
        self.assertIn("src/server.py", paths)


# ---------------------------------------------------------------------------
# Tests: _count_diff_idents
# ---------------------------------------------------------------------------


class TestCountDiffIdents(unittest.TestCase):
    def test_counts_identifiers_from_diff(self):
        b = _change_bundle()
        count = _count_diff_idents(b)
        # Should find: timeout, get_timeout, import, logging, logger, getLogger
        self.assertGreater(count, 3)

    def test_zero_for_no_diff(self):
        b = _change_bundle()
        b["diff"] = ""
        count = _count_diff_idents(b)
        self.assertEqual(count, 0)

    def test_ignores_diff_header_lines(self):
        b = {"diff": "--- a/foo.py\n+++ b/foo.py\n"}
        count = _count_diff_idents(b)
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# Tests: change-mode planner integration
# ---------------------------------------------------------------------------


class TestChangeModePlanner(unittest.TestCase):
    def test_change_mode_triggers_focus_changed_files(self):
        b = _change_bundle(changed_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertIn("focus_changed_files", names)

    def test_change_mode_triggers_expand_diff_idents(self):
        b = _change_bundle(changed_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertIn("expand_diff_idents", names)

    def test_no_focus_when_covered(self):
        b = _change_bundle(changed_in_slices=True)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertNotIn("focus_changed_files", names)

    def test_focus_changed_is_highest_priority(self):
        b = _change_bundle(changed_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        self.assertEqual(actions[0]["name"], "focus_changed_files")

    def test_no_change_actions_for_default_mode(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        names = [a["name"] for a in actions]
        self.assertNotIn("focus_changed_files", names)
        self.assertNotIn("expand_diff_idents", names)

    def test_focus_changed_carries_targets(self):
        b = _change_bundle(changed_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        fcf = [a for a in actions if a["name"] == "focus_changed_files"][0]
        self.assertIn("changed_targets", fcf)
        self.assertEqual(len(fcf["changed_targets"]), 2)

    def test_expand_diff_idents_carries_count(self):
        b = _change_bundle(changed_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        edi = [a for a in actions if a["name"] == "expand_diff_idents"]
        if edi:
            self.assertIn("ident_count", edi[0])
            self.assertGreater(edi[0]["ident_count"], 0)


# ---------------------------------------------------------------------------
# Tests: change-mode apply_refinement
# ---------------------------------------------------------------------------


class TestChangeModeApply(unittest.TestCase):
    def test_focus_changed_increases_evidence_files(self):
        action = {
            "name": "focus_changed_files",
            "changed_targets": [
                {"path": "a.py", "line": 1},
                {"path": "b.py", "line": 2},
            ],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["evidence_files"], 7)

    def test_focus_changed_stores_force_paths(self):
        action = {
            "name": "focus_changed_files",
            "changed_targets": [{"path": "a.py", "line": 1}],
        }
        params = {"evidence_files": 5}
        new = apply_refinement(params, action)
        self.assertEqual(new["_force_slice_paths"], ["a.py"])

    def test_expand_diff_idents_bumps_cap(self):
        action = {"name": "expand_diff_idents", "ident_count": 15}
        params = {"ident_cap": 20}
        new = apply_refinement(params, action)
        self.assertEqual(new["ident_cap"], 30)

    def test_expand_diff_idents_caps_at_50(self):
        action = {"name": "expand_diff_idents", "ident_count": 30}
        params = {"ident_cap": 45}
        new = apply_refinement(params, action)
        self.assertEqual(new["ident_cap"], 50)

    def test_expand_diff_idents_default_cap(self):
        action = {"name": "expand_diff_idents", "ident_count": 10}
        params = {}  # no ident_cap set
        new = apply_refinement(params, action)
        self.assertEqual(new["ident_cap"], 30)  # 20 default + 10


# ---------------------------------------------------------------------------
# Tests: change-mode execution
# ---------------------------------------------------------------------------


class TestChangeModeExecution(unittest.TestCase):
    def _build_fn_change(self, **kwargs) -> dict:
        """Mock build fn that adds changed file slices when forced."""
        force_paths = kwargs.get("_force_slice_paths", [])
        slices = []
        for fp in force_paths:
            slices.append({"path": fp, "start": 1, "lines": ["..."] * 20})

        return {
            "version": 2,
            "mode": "change",
            "repo": kwargs.get("repo", "org/repo"),
            "request_id": "",
            "query": "",
            "ranked_sources": [
                {"path": "src/config.py", "line": 10, "score": 0.0},
                {"path": "src/server.py", "line": 5, "score": 0.0},
            ],
            "matches": [],
            "slices": slices,
            "symbols": [],
            "diff": _SAMPLE_DIFF,
            "suggested_commands": [],
            "notes": ["2 file(s) changed"],
            "truncated": False,
        }

    def test_pass_records_include_change_details(self):
        b = _change_bundle(changed_in_slices=False)
        result = execute_refinements(
            b,
            build_fn=self._build_fn_change,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        self.assertGreater(len(result["passes"]), 0)
        p = result["passes"][0]
        fcf = [d for d in p["action_details"] if d["name"] == "focus_changed_files"]
        self.assertEqual(len(fcf), 1)
        self.assertIn("changed_targets", fcf[0])

    def test_changed_slices_added_after_execution(self):
        b = _change_bundle(changed_in_slices=False)
        result = execute_refinements(
            b,
            build_fn=self._build_fn_change,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        final = result["final_bundle"]
        slice_paths = [s["path"] for s in final["slices"]]
        self.assertIn("src/config.py", slice_paths)
        self.assertIn("src/server.py", slice_paths)


# ---------------------------------------------------------------------------
# Tests: trigger_reasons in pass records
# ---------------------------------------------------------------------------


class TestTriggerReasons(unittest.TestCase):
    def test_error_mode_action_has_trigger_reason(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        fts = [a for a in actions if a["name"] == "force_trace_slices"]
        self.assertEqual(len(fts), 1)
        self.assertIn("trigger_reason", fts[0])
        self.assertIn("missing slices", fts[0]["trigger_reason"])

    def test_symbol_mode_action_has_trigger_reason(self):
        b = _symbol_bundle(defs_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        pds = [a for a in actions if a["name"] == "pin_def_slices"]
        self.assertEqual(len(pds), 1)
        self.assertIn("trigger_reason", pds[0])
        self.assertIn("def file(s) missing slices", pds[0]["trigger_reason"])

    def test_change_mode_action_has_trigger_reason(self):
        b = _change_bundle(changed_in_slices=False)
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf)
        fcf = [a for a in actions if a["name"] == "focus_changed_files"]
        self.assertEqual(len(fcf), 1)
        self.assertIn("trigger_reason", fcf[0])
        self.assertIn("changed file(s) missing slices", fcf[0]["trigger_reason"])

    def test_generic_widen_search_has_trigger_reason(self):
        b = _low_conf_bundle()
        conf = bundle_confidence(b)
        actions = plan_refinements(b, conf, current_params={"max_matches": 50})
        ws = [a for a in actions if a["name"] == "widen_search"]
        self.assertEqual(len(ws), 1)
        self.assertIn("trigger_reason", ws[0])
        self.assertIn("source(s)", ws[0]["trigger_reason"])

    def test_trigger_reasons_in_pass_records(self):
        b = _error_bundle_with_trace(slices_cover_trace=False)

        def _build(**kw):
            return _error_bundle_with_trace(slices_cover_trace=True)

        result = execute_refinements(
            b,
            build_fn=_build,
            build_kwargs={"evidence_files": 5},
            max_passes=1,
        )
        self.assertGreater(len(result["passes"]), 0)
        p = result["passes"][0]
        for detail in p["action_details"]:
            self.assertIn("trigger_reason", detail)

    def test_all_actions_have_trigger_reasons(self):
        """Every action returned by plan_refinements should have a reason."""
        bundles = [
            _low_conf_bundle(),
            _error_bundle_with_trace(slices_cover_trace=False),
            _symbol_bundle(defs_in_slices=False),
            _change_bundle(changed_in_slices=False),
        ]
        for b in bundles:
            conf = bundle_confidence(b)
            actions = plan_refinements(b, conf, current_params={"max_matches": 50})
            for a in actions:
                self.assertIn(
                    "trigger_reason",
                    a,
                    f"Action '{a['name']}' missing trigger_reason "
                    f"for mode={b.get('mode')}",
                )


if __name__ == "__main__":
    unittest.main()
