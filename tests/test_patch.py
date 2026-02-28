"""Tests for cts.corpus.patch — patch planner."""

from __future__ import annotations

import json
import tempfile
import unittest

from cts.corpus.patch import (
    PatchItem,
    _MISSING,
    _get_nested,
    _map_target_to_yaml,
    _resolve_value,
    _set_nested,
    apply_plan_to_yaml,
    generate_patch_plan,
    load_tuning,
    render_plan_diff,
    render_plan_json,
    render_plan_text,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_repos_yaml() -> dict:
    """A repos.yaml with tunable defaults."""
    return {
        "defaults": {
            "bundle": {
                "default": {
                    "max_bytes": 524288,
                    "context_lines": 30,
                    "evidence_files": 5,
                },
                "error": {
                    "max_bytes": 524288,
                    "context_lines": 20,
                    "evidence_files": 5,
                },
            },
            "autopilot": {
                "sufficient_threshold": 0.6,
                "default_passes": 0,
                "actions": {
                    "widen_search": {"enabled": True},
                    "add_slices": {"enabled": True},
                },
            },
        },
        "repos": {
            "org/frontend": {
                "url": "https://github.com/org/frontend.git",
                "preset": "node",
            },
        },
    }


def _base_tuning(recommendations: list | None = None) -> dict:
    """A tuning envelope with optional recommendations."""
    return {
        "tuning_schema_version": 1,
        "generated_at": 1700000000.0,
        "source_corpus": "corpus.jsonl",
        "filters": {},
        "kpis_before": {
            "total_artifacts": 50,
            "truncation_rate": 0.25,
        },
        "recommendations": recommendations or [],
    }


def _make_rec(
    id: str = "test-rec",
    scope: str = "global",
    change_type: str = "set",
    target: str = "autopilot.sufficient_threshold",
    from_value=0.6,
    to_value=0.65,
    risk: str = "low",
    rationale: str = "test reason",
    rollback: str = "revert to 0.6",
) -> dict:
    return {
        "id": id,
        "scope": scope,
        "change_type": change_type,
        "target": target,
        "from": from_value,
        "to": to_value,
        "rationale": rationale,
        "evidence": {},
        "risk": risk,
        "rollback": rollback,
    }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestGetNested(unittest.TestCase):
    def test_simple_path(self):
        data = {"a": {"b": {"c": 42}}}
        self.assertEqual(_get_nested(data, "a.b.c"), 42)

    def test_missing_path(self):
        data = {"a": {"b": 1}}
        self.assertIs(_get_nested(data, "a.b.c"), _MISSING)

    def test_top_level(self):
        data = {"x": 10}
        self.assertEqual(_get_nested(data, "x"), 10)

    def test_empty_dict(self):
        self.assertIs(_get_nested({}, "a.b"), _MISSING)

    def test_non_dict_intermediate(self):
        data = {"a": 42}
        self.assertIs(_get_nested(data, "a.b"), _MISSING)


class TestSetNested(unittest.TestCase):
    def test_simple_set(self):
        data: dict = {}
        _set_nested(data, "a.b.c", 99)
        self.assertEqual(data["a"]["b"]["c"], 99)

    def test_overwrite_existing(self):
        data = {"a": {"b": 1}}
        _set_nested(data, "a.b", 2)
        self.assertEqual(data["a"]["b"], 2)

    def test_creates_intermediates(self):
        data: dict = {"x": 1}
        _set_nested(data, "a.b.c.d", "val")
        self.assertEqual(data["a"]["b"]["c"]["d"], "val")


class TestMapTarget(unittest.TestCase):
    def test_bundle_target(self):
        result = _map_target_to_yaml("bundle.default.max_bytes")
        self.assertEqual(result, "defaults.bundle.default.max_bytes")

    def test_autopilot_target(self):
        result = _map_target_to_yaml("autopilot.sufficient_threshold")
        self.assertEqual(result, "defaults.autopilot.sufficient_threshold")

    def test_autopilot_action_target(self):
        result = _map_target_to_yaml("autopilot.actions.widen_search.enabled")
        self.assertEqual(
            result,
            "defaults.autopilot.actions.widen_search.enabled",
        )

    def test_unknown_prefix_passthrough(self):
        result = _map_target_to_yaml("custom.thing.val")
        self.assertEqual(result, "custom.thing.val")


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------


class TestResolveValue(unittest.TestCase):
    def test_set(self):
        val, skip, reason = _resolve_value("set", 10, 10, 20)
        self.assertEqual(val, 20)
        self.assertFalse(skip)

    def test_toggle(self):
        val, skip, _ = _resolve_value("toggle", True, True, False)
        self.assertEqual(val, False)
        self.assertFalse(skip)

    def test_delta_add(self):
        val, skip, _ = _resolve_value("delta", 100, "current", "+50")
        self.assertEqual(val, 150)
        self.assertFalse(skip)

    def test_delta_subtract(self):
        val, skip, _ = _resolve_value("delta", 30, "current", "-10")
        self.assertEqual(val, 20)
        self.assertFalse(skip)

    def test_delta_missing_current(self):
        val, skip, reason = _resolve_value("delta", _MISSING, None, "+10")
        self.assertTrue(skip)
        self.assertIn("unknown", reason)

    def test_delta_current_string(self):
        val, skip, reason = _resolve_value("delta", "current", "current", "+10")
        self.assertTrue(skip)

    def test_cap_below(self):
        val, skip, _ = _resolve_value("cap", 300000, "current", "524288")
        self.assertEqual(val, 300000)
        self.assertFalse(skip)

    def test_cap_above(self):
        val, skip, _ = _resolve_value("cap", 800000, "current", "524288")
        self.assertEqual(val, 524288)
        self.assertFalse(skip)

    def test_cap_missing(self):
        val, skip, _ = _resolve_value("cap", _MISSING, "current", "524288")
        self.assertEqual(val, 524288)
        self.assertFalse(skip)

    def test_unknown_type(self):
        val, skip, reason = _resolve_value("magic", 1, 1, 2)
        self.assertTrue(skip)
        self.assertIn("Unknown", reason)


# ---------------------------------------------------------------------------
# Patch plan generation
# ---------------------------------------------------------------------------


class TestGeneratePatchPlan(unittest.TestCase):
    def test_empty_recommendations(self):
        items = generate_patch_plan(_base_tuning([]), _base_repos_yaml())
        self.assertEqual(items, [])

    def test_set_recommendation(self):
        rec = _make_rec(
            id="raise-threshold",
            change_type="set",
            target="autopilot.sufficient_threshold",
            from_value=0.6,
            to_value=0.65,
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.recommendation_id, "raise-threshold")
        self.assertEqual(
            item.yaml_path,
            "defaults.autopilot.sufficient_threshold",
        )
        self.assertEqual(item.old_value, 0.6)
        self.assertEqual(item.new_value, 0.65)
        self.assertFalse(item.skipped)

    def test_toggle_recommendation(self):
        rec = _make_rec(
            id="disable-widen",
            change_type="toggle",
            target="autopilot.actions.widen_search.enabled",
            from_value=True,
            to_value=False,
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.new_value, False)
        self.assertEqual(item.old_value, True)
        self.assertFalse(item.skipped)

    def test_delta_recommendation(self):
        rec = _make_rec(
            id="raise-context",
            change_type="delta",
            target="bundle.default.context_lines",
            from_value="current",
            to_value="-10",
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.old_value, 30)
        self.assertEqual(item.new_value, 20)
        self.assertFalse(item.skipped)

    def test_cap_recommendation(self):
        rec = _make_rec(
            id="cap-bytes",
            change_type="cap",
            target="bundle.default.max_bytes",
            from_value="current",
            to_value="524288",
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        item = items[0]
        # 524288 == current, so it should be skipped (no change)
        self.assertTrue(item.skipped)
        self.assertIn("already matches", item.skip_reason)

    def test_cap_reduces_value(self):
        repos = _base_repos_yaml()
        repos["defaults"]["bundle"]["default"]["max_bytes"] = 800000
        rec = _make_rec(
            id="cap-bytes",
            change_type="cap",
            target="bundle.default.max_bytes",
            from_value="current",
            to_value="524288",
        )
        items = generate_patch_plan(_base_tuning([rec]), repos)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertFalse(item.skipped)
        self.assertEqual(item.new_value, 524288)

    def test_missing_target_delta_skipped(self):
        rec = _make_rec(
            id="delta-missing",
            change_type="delta",
            target="bundle.symbol.context_lines",
            from_value="current",
            to_value="-10",
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].skipped)

    def test_repo_scope(self):
        rec = _make_rec(
            id="repo-override",
            scope="repo:org/frontend",
            change_type="set",
            target="bundle.default.max_bytes",
            to_value=262144,
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("org/frontend", item.yaml_path)
        self.assertIn("overrides", item.yaml_path)
        self.assertFalse(item.skipped)

    def test_multiple_recommendations(self):
        recs = [
            _make_rec(
                id="rec-1",
                change_type="set",
                target="autopilot.default_passes",
                to_value=2,
            ),
            _make_rec(
                id="rec-2",
                change_type="toggle",
                target="autopilot.actions.add_slices.enabled",
                from_value=True,
                to_value=False,
            ),
        ]
        items = generate_patch_plan(_base_tuning(recs), _base_repos_yaml())
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].recommendation_id, "rec-1")
        self.assertEqual(items[1].recommendation_id, "rec-2")

    def test_value_already_matches(self):
        rec = _make_rec(
            id="no-op",
            change_type="set",
            target="autopilot.sufficient_threshold",
            to_value=0.6,
        )
        items = generate_patch_plan(_base_tuning([rec]), _base_repos_yaml())
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].skipped)
        self.assertIn("already matches", items[0].skip_reason)


# ---------------------------------------------------------------------------
# Apply plan (in memory)
# ---------------------------------------------------------------------------


class TestApplyPlan(unittest.TestCase):
    def test_apply_creates_new_value(self):
        repos = _base_repos_yaml()
        item = PatchItem(
            recommendation_id="test",
            yaml_path="defaults.autopilot.sufficient_threshold",
            old_value=0.6,
            new_value=0.65,
        )
        patched = apply_plan_to_yaml(repos, [item])
        self.assertEqual(
            patched["defaults"]["autopilot"]["sufficient_threshold"],
            0.65,
        )
        # Original unchanged
        self.assertEqual(repos["defaults"]["autopilot"]["sufficient_threshold"], 0.6)

    def test_apply_skips_skipped(self):
        repos = _base_repos_yaml()
        item = PatchItem(
            recommendation_id="skip-me",
            yaml_path="defaults.autopilot.sufficient_threshold",
            old_value=0.6,
            new_value=999,
            skipped=True,
            skip_reason="test",
        )
        patched = apply_plan_to_yaml(repos, [item])
        self.assertEqual(
            patched["defaults"]["autopilot"]["sufficient_threshold"],
            0.6,
        )

    def test_apply_creates_new_path(self):
        repos = _base_repos_yaml()
        item = PatchItem(
            recommendation_id="new-path",
            yaml_path="defaults.bundle.symbol.context_lines",
            new_value=15,
        )
        patched = apply_plan_to_yaml(repos, [item])
        self.assertEqual(
            patched["defaults"]["bundle"]["symbol"]["context_lines"],
            15,
        )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderPlanJson(unittest.TestCase):
    def test_json_output(self):
        items = [
            PatchItem(
                recommendation_id="r1",
                yaml_path="defaults.x",
                old_value=1,
                new_value=2,
                risk="low",
                rationale="test",
                rollback="undo",
            ),
            PatchItem(
                recommendation_id="r2",
                yaml_path="defaults.y",
                old_value=3,
                new_value=4,
                skipped=True,
                skip_reason="no change",
            ),
        ]
        output = render_plan_json(items)
        data = json.loads(output)
        self.assertEqual(data["patch_count"], 1)
        self.assertEqual(data["skipped_count"], 1)
        self.assertEqual(len(data["items"]), 2)
        self.assertIn("skipped", data["items"][1])

    def test_empty_plan(self):
        output = render_plan_json([])
        data = json.loads(output)
        self.assertEqual(data["patch_count"], 0)
        self.assertEqual(data["items"], [])


class TestRenderPlanText(unittest.TestCase):
    def test_text_output(self):
        items = [
            PatchItem(
                recommendation_id="r1",
                yaml_path="defaults.x",
                old_value=1,
                new_value=2,
                risk="med",
                rationale="because",
                rollback="undo it",
            ),
        ]
        output = render_plan_text(items)
        self.assertIn("TUNING PATCH PLAN", output)
        self.assertIn("r1", output)
        self.assertIn("[MED]", output)
        self.assertIn("defaults.x", output)
        self.assertIn("because", output)
        self.assertIn("undo it", output)

    def test_empty_plan(self):
        output = render_plan_text([])
        self.assertIn("Active patches: 0", output)


class TestRenderPlanDiff(unittest.TestCase):
    def test_diff_with_change(self):
        repos = _base_repos_yaml()
        items = [
            PatchItem(
                recommendation_id="r1",
                yaml_path="defaults.autopilot.sufficient_threshold",
                old_value=0.6,
                new_value=0.65,
            ),
        ]
        output = render_plan_diff(repos, items)
        self.assertIn("---", output)
        self.assertIn("+++", output)

    def test_diff_no_change(self):
        repos = _base_repos_yaml()
        items = [
            PatchItem(
                recommendation_id="skip",
                yaml_path="defaults.x",
                skipped=True,
                skip_reason="test",
            ),
        ]
        output = render_plan_diff(repos, items)
        self.assertIn("No changes", output)


# ---------------------------------------------------------------------------
# PatchItem model
# ---------------------------------------------------------------------------


class TestPatchItem(unittest.TestCase):
    def test_to_dict_basic(self):
        item = PatchItem(
            recommendation_id="test",
            yaml_path="a.b",
            old_value=1,
            new_value=2,
            risk="low",
            rationale="reason",
            rollback="undo",
        )
        d = item.to_dict()
        self.assertEqual(d["recommendation_id"], "test")
        self.assertEqual(d["yaml_path"], "a.b")
        self.assertEqual(d["old_value"], 1)
        self.assertEqual(d["new_value"], 2)
        self.assertNotIn("skipped", d)

    def test_to_dict_skipped(self):
        item = PatchItem(
            recommendation_id="skip",
            yaml_path="x",
            skipped=True,
            skip_reason="no need",
        )
        d = item.to_dict()
        self.assertTrue(d["skipped"])
        self.assertEqual(d["skip_reason"], "no need")


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


class TestLoadTuning(unittest.TestCase):
    def test_load_valid(self):
        data = _base_tuning([_make_rec()])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            path = f.name

        loaded = load_tuning(path)
        self.assertEqual(loaded["tuning_schema_version"], 1)
        self.assertEqual(len(loaded["recommendations"]), 1)

        import os

        os.unlink(path)

    def test_load_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_tuning("/nonexistent/tuning.json")


# ---------------------------------------------------------------------------
# End-to-end: tuning → patch plan
# ---------------------------------------------------------------------------


class TestEndToEnd(unittest.TestCase):
    def test_full_pipeline(self):
        """Tuning envelope → patch plan → rendered output."""
        recs = [
            _make_rec(
                id="raise-threshold",
                change_type="set",
                target="autopilot.sufficient_threshold",
                from_value=0.6,
                to_value=0.65,
            ),
            _make_rec(
                id="reduce-context",
                change_type="delta",
                target="bundle.error.context_lines",
                from_value="current",
                to_value="-5",
            ),
            _make_rec(
                id="disable-widen",
                change_type="toggle",
                target="autopilot.actions.widen_search.enabled",
                from_value=True,
                to_value=False,
            ),
        ]
        tuning = _base_tuning(recs)
        repos = _base_repos_yaml()

        items = generate_patch_plan(tuning, repos)
        self.assertEqual(len(items), 3)

        # Verify each item
        threshold_item = items[0]
        self.assertEqual(threshold_item.new_value, 0.65)
        self.assertFalse(threshold_item.skipped)

        context_item = items[1]
        self.assertEqual(context_item.new_value, 15)  # 20 - 5
        self.assertFalse(context_item.skipped)

        toggle_item = items[2]
        self.assertEqual(toggle_item.new_value, False)
        self.assertFalse(toggle_item.skipped)

        # Verify rendered outputs are non-empty
        json_out = render_plan_json(items)
        self.assertTrue(len(json_out) > 50)

        text_out = render_plan_text(items)
        self.assertIn("PATCHES TO APPLY", text_out)

        diff_out = render_plan_diff(repos, items)
        self.assertTrue(len(diff_out) > 0)

        # Verify apply doesn't mutate original
        patched = apply_plan_to_yaml(repos, items)
        self.assertEqual(
            patched["defaults"]["autopilot"]["sufficient_threshold"],
            0.65,
        )
        self.assertEqual(
            repos["defaults"]["autopilot"]["sufficient_threshold"],
            0.6,
        )

    def test_empty_repos_yaml(self):
        """Patch plan works even with empty repos.yaml."""
        rec = _make_rec(
            id="set-threshold",
            change_type="set",
            target="autopilot.sufficient_threshold",
            to_value=0.65,
        )
        items = generate_patch_plan(_base_tuning([rec]), {})
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].skipped)
        self.assertIsNone(items[0].old_value)
        self.assertEqual(items[0].new_value, 0.65)


if __name__ == "__main__":
    unittest.main()
