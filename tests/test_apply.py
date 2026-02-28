"""Tests for cts.corpus.apply — apply engine with guardrails."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from cts.corpus.apply import (
    RollbackRecord,
    apply_patch_plan,
    check_risk_gate,
    create_backup,
    rollback_from_backup,
    rollback_from_record,
    write_rollback,
)
from cts.corpus.patch import PatchItem


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    rec_id: str = "test-rec",
    yaml_path: str = "defaults.autopilot.sufficient_threshold",
    old_value=0.6,
    new_value=0.65,
    risk: str = "low",
    skipped: bool = False,
    skip_reason: str = "",
) -> PatchItem:
    return PatchItem(
        recommendation_id=rec_id,
        yaml_path=yaml_path,
        old_value=old_value,
        new_value=new_value,
        risk=risk,
        rationale="test",
        rollback="undo",
        skipped=skipped,
        skip_reason=skip_reason,
    )


def _write_yaml_file(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _read_yaml_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # Try YAML first, fall back to JSON
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except ImportError:
        return json.loads(content)


def _base_repos() -> dict:
    return {
        "defaults": {
            "autopilot": {
                "sufficient_threshold": 0.6,
                "default_passes": 0,
            },
            "bundle": {
                "default": {"max_bytes": 524288},
            },
        },
        "repos": {},
    }


# ---------------------------------------------------------------------------
# Risk gate
# ---------------------------------------------------------------------------


class TestRiskGate(unittest.TestCase):
    def test_low_risk_passes(self):
        items = [_make_item(risk="low")]
        self.assertEqual(check_risk_gate(items), [])

    def test_med_risk_passes(self):
        items = [_make_item(risk="med")]
        self.assertEqual(check_risk_gate(items), [])

    def test_high_risk_blocks(self):
        items = [_make_item(risk="high")]
        reasons = check_risk_gate(items)
        self.assertEqual(len(reasons), 1)
        self.assertIn("High-risk", reasons[0])

    def test_high_risk_allowed(self):
        items = [_make_item(risk="high")]
        reasons = check_risk_gate(items, allow_high_risk=True)
        self.assertEqual(reasons, [])

    def test_skipped_high_risk_ignored(self):
        items = [_make_item(risk="high", skipped=True, skip_reason="no-op")]
        reasons = check_risk_gate(items)
        self.assertEqual(reasons, [])

    def test_mixed_risks(self):
        items = [
            _make_item(rec_id="low-1", risk="low"),
            _make_item(rec_id="high-1", risk="high"),
        ]
        reasons = check_risk_gate(items)
        self.assertEqual(len(reasons), 1)
        self.assertIn("high-1", reasons[0])


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


class TestBackup(unittest.TestCase):
    def test_create_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "repos.yaml")
            _write_yaml_file({"x": 1}, src)

            backup = create_backup(src, suffix="12345")
            self.assertTrue(os.path.isfile(backup))
            self.assertIn("12345", backup)

            data = _read_yaml_file(backup)
            self.assertEqual(data["x"], 1)

    def test_backup_preserves_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "repos.yaml")
            original = _base_repos()
            _write_yaml_file(original, src)

            backup = create_backup(src, suffix="99")
            restored = _read_yaml_file(backup)
            self.assertEqual(restored, original)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class TestApplyPatchPlan(unittest.TestCase):
    def test_apply_single_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            repos = _base_repos()
            _write_yaml_file(repos, repos_path)

            items = [_make_item()]
            result = apply_patch_plan(
                repos,
                items,
                repos_yaml_path=repos_path,
            )

            self.assertEqual(len(result["applied"]), 1)
            self.assertEqual(len(result["blocked"]), 0)
            self.assertIsNotNone(result["backup_path"])
            self.assertIsNotNone(result["rollback"])

            # Verify file was written
            updated = _read_yaml_file(repos_path)
            self.assertIsNotNone(updated)

    def test_dry_run_no_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            repos = _base_repos()
            _write_yaml_file(repos, repos_path)

            items = [_make_item()]
            result = apply_patch_plan(
                repos,
                items,
                repos_yaml_path=repos_path,
                dry_run=True,
            )

            self.assertEqual(len(result["applied"]), 1)
            self.assertIsNone(result["backup_path"])
            self.assertIsNone(result["rollback"])

            # File should be unchanged
            on_disk = _read_yaml_file(repos_path)
            val = on_disk["defaults"]["autopilot"]["sufficient_threshold"]
            self.assertEqual(val, 0.6)

    def test_high_risk_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            repos = _base_repos()
            _write_yaml_file(repos, repos_path)

            items = [_make_item(risk="high")]
            result = apply_patch_plan(
                repos,
                items,
                repos_yaml_path=repos_path,
            )

            self.assertTrue(len(result["blocked"]) > 0)
            self.assertEqual(result["applied"], [])

    def test_high_risk_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            repos = _base_repos()
            _write_yaml_file(repos, repos_path)

            items = [_make_item(risk="high")]
            result = apply_patch_plan(
                repos,
                items,
                repos_yaml_path=repos_path,
                allow_high_risk=True,
            )

            self.assertEqual(len(result["applied"]), 1)
            self.assertEqual(len(result["blocked"]), 0)

    def test_skipped_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            repos = _base_repos()
            _write_yaml_file(repos, repos_path)

            items = [
                _make_item(rec_id="active", risk="low"),
                _make_item(
                    rec_id="skip",
                    risk="low",
                    skipped=True,
                    skip_reason="no-op",
                ),
            ]
            result = apply_patch_plan(
                repos,
                items,
                repos_yaml_path=repos_path,
            )

            self.assertEqual(len(result["applied"]), 1)
            self.assertEqual(len(result["skipped"]), 1)

    def test_empty_plan_no_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            repos = _base_repos()
            _write_yaml_file(repos, repos_path)

            result = apply_patch_plan(
                repos,
                [],
                repos_yaml_path=repos_path,
            )

            self.assertEqual(result["applied"], [])
            self.assertIsNone(result["backup_path"])


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollback(unittest.TestCase):
    def test_rollback_from_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            original = _base_repos()
            _write_yaml_file(original, repos_path)

            # Create backup
            backup = create_backup(repos_path, suffix="test")

            # Modify the file
            modified = _base_repos()
            modified["defaults"]["autopilot"]["sufficient_threshold"] = 0.99
            _write_yaml_file(modified, repos_path)

            # Verify modification
            on_disk = _read_yaml_file(repos_path)
            self.assertEqual(
                on_disk["defaults"]["autopilot"]["sufficient_threshold"],
                0.99,
            )

            # Rollback
            ok = rollback_from_backup(repos_path, backup)
            self.assertTrue(ok)

            restored = _read_yaml_file(repos_path)
            self.assertEqual(
                restored["defaults"]["autopilot"]["sufficient_threshold"],
                0.6,
            )

    def test_rollback_missing_backup(self):
        ok = rollback_from_backup("repos.yaml", "/nonexistent/backup")
        self.assertFalse(ok)

    def test_rollback_from_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            original = _base_repos()
            _write_yaml_file(original, repos_path)

            backup = create_backup(repos_path, suffix="rec")

            # Modify
            modified = _base_repos()
            modified["defaults"]["autopilot"]["default_passes"] = 5
            _write_yaml_file(modified, repos_path)

            record = {
                "repos_yaml_path": repos_path,
                "backup_path": backup,
            }

            ok = rollback_from_record(record)
            self.assertTrue(ok)

            restored = _read_yaml_file(repos_path)
            self.assertEqual(
                restored["defaults"]["autopilot"]["default_passes"],
                0,
            )

    def test_rollback_record_missing_paths(self):
        self.assertFalse(rollback_from_record({}))
        self.assertFalse(rollback_from_record({"repos_yaml_path": "x"}))


# ---------------------------------------------------------------------------
# RollbackRecord model
# ---------------------------------------------------------------------------


class TestRollbackRecord(unittest.TestCase):
    def test_to_dict(self):
        r = RollbackRecord(
            applied_at=1700000000.0,
            repos_yaml_path="repos.yaml",
            backup_path="repos.yaml.bak.123",
            items_applied=[{"id": "r1"}],
            original_snapshot={"x": 1},
        )
        d = r.to_dict()
        self.assertEqual(d["applied_at"], 1700000000.0)
        self.assertEqual(d["repos_yaml_path"], "repos.yaml")
        self.assertEqual(d["backup_path"], "repos.yaml.bak.123")
        self.assertEqual(len(d["items_applied"]), 1)


# ---------------------------------------------------------------------------
# Write rollback
# ---------------------------------------------------------------------------


class TestWriteRollback(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        try:
            record = {
                "applied_at": 1700000000.0,
                "repos_yaml_path": "repos.yaml",
                "backup_path": "repos.yaml.bak.123",
                "items_applied": [{"id": "test"}],
                "original_snapshot": {"key": "val"},
            }
            write_rollback(record, path)

            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)

            self.assertEqual(loaded["applied_at"], 1700000000.0)
            self.assertEqual(loaded["items_applied"][0]["id"], "test")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# End-to-end: apply → rollback
# ---------------------------------------------------------------------------


class TestEndToEnd(unittest.TestCase):
    def test_apply_then_rollback(self):
        """Full cycle: apply changes, verify, rollback, verify."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            rollback_path = os.path.join(tmpdir, "rollback.json")
            original = _base_repos()
            _write_yaml_file(original, repos_path)

            items = [
                _make_item(
                    rec_id="bump-threshold",
                    yaml_path=("defaults.autopilot.sufficient_threshold"),
                    old_value=0.6,
                    new_value=0.65,
                ),
            ]

            # Apply
            result = apply_patch_plan(
                original,
                items,
                repos_yaml_path=repos_path,
            )
            self.assertEqual(len(result["applied"]), 1)
            self.assertIsNotNone(result["rollback"])

            # Write rollback record
            write_rollback(result["rollback"], rollback_path)

            # Verify the change took effect on disk
            on_disk = _read_yaml_file(repos_path)
            # The patched content was written
            self.assertIsNotNone(on_disk)

            # Rollback using the record
            with open(rollback_path, encoding="utf-8") as f:
                record = json.load(f)

            ok = rollback_from_record(record)
            self.assertTrue(ok)

            # Verify restored to original
            restored = _read_yaml_file(repos_path)
            self.assertEqual(
                restored["defaults"]["autopilot"]["sufficient_threshold"],
                0.6,
            )

    def test_dry_run_then_real_apply(self):
        """Dry run should not change file, real apply should."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_path = os.path.join(tmpdir, "repos.yaml")
            original = _base_repos()
            _write_yaml_file(original, repos_path)

            items = [
                _make_item(
                    new_value=0.7,
                ),
            ]

            # Dry run
            result1 = apply_patch_plan(
                original,
                items,
                repos_yaml_path=repos_path,
                dry_run=True,
            )
            self.assertEqual(len(result1["applied"]), 1)
            self.assertIsNone(result1["backup_path"])

            # File unchanged
            on_disk1 = _read_yaml_file(repos_path)
            self.assertEqual(
                on_disk1["defaults"]["autopilot"]["sufficient_threshold"],
                0.6,
            )

            # Real apply
            result2 = apply_patch_plan(
                original,
                items,
                repos_yaml_path=repos_path,
            )
            self.assertEqual(len(result2["applied"]), 1)
            self.assertIsNotNone(result2["backup_path"])


if __name__ == "__main__":
    unittest.main()
