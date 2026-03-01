"""Tests for cts.corpus.archive — experiment registry archival."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from cts.corpus.archive import (
    REGISTRY_VERSION,
    _content_hash,
    _file_hash,
    archive_experiment,
    build_meta,
    validate_registry_entry,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _make_experiment(tmpdir: str) -> str:
    """Create a minimal experiment.json and return its path."""
    path = os.path.join(tmpdir, "experiment.json")
    _write_json(
        path,
        {
            "experiment_schema_version": 1,
            "id": "exp-test-001",
            "created_at": 1700000000.0,
            "description": "Test experiment",
            "hypothesis": "Things get better",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {"primary_kpi": "confidence_final_mean"},
        },
    )
    return path


def _make_result(tmpdir: str) -> str:
    """Create a minimal result.json and return its path."""
    path = os.path.join(tmpdir, "result.json")
    _write_json(
        path,
        {
            "experiment_id": "exp-test-001",
            "winner": "B",
            "verdict": "winner",
            "reasoning": "B wins on confidence_final_mean",
            "per_variant": {
                "A": {"total": 10, "kpis": {"confidence_final_mean": 0.5}},
                "B": {"total": 10, "kpis": {"confidence_final_mean": 0.8}},
            },
        },
    )
    return path


def _make_result_md(tmpdir: str) -> str:
    """Create a minimal result.md and return its path."""
    path = os.path.join(tmpdir, "result.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Experiment Result\n\nWinner: B\n")
    return path


def _make_variant_dir(tmpdir: str) -> str:
    """Create a variant directory with tuning files."""
    vdir = os.path.join(tmpdir, "variants_src")
    os.makedirs(vdir, exist_ok=True)
    _write_json(
        os.path.join(vdir, "tuning_A.json"),
        {"recommendations": [], "variant_metadata": {"strategy": "conservative"}},
    )
    _write_json(
        os.path.join(vdir, "tuning_B.json"),
        {"recommendations": [], "variant_metadata": {"strategy": "aggressive"}},
    )
    with open(os.path.join(vdir, "patch_A.diff"), "w") as f:
        f.write("# No changes\n")
    with open(os.path.join(vdir, "patch_B.diff"), "w") as f:
        f.write("# No changes\n")
    return vdir


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


class TestHashing(unittest.TestCase):
    def test_file_hash_deterministic(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"hello": "world"}')
            path = f.name
        try:
            h1 = _file_hash(path)
            h2 = _file_hash(path)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # SHA-256 hex
        finally:
            os.unlink(path)

    def test_content_hash(self):
        h = _content_hash("test content")
        self.assertEqual(len(h), 64)

    def test_different_content_different_hash(self):
        h1 = _content_hash("content A")
        h2 = _content_hash("content B")
        self.assertNotEqual(h1, h2)


# ---------------------------------------------------------------------------
# build_meta
# ---------------------------------------------------------------------------


class TestBuildMeta(unittest.TestCase):
    def test_basic_meta(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            meta = build_meta(
                exp_id="test-001",
                experiment_path=exp_path,
                variant_files={},
                run_id="run_123",
            )
            self.assertEqual(meta["registry_version"], REGISTRY_VERSION)
            self.assertEqual(meta["exp_id"], "test-001")
            self.assertEqual(meta["run_id"], "run_123")
            self.assertIn("experiment", meta["hashes"])

    def test_meta_with_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            vdir = _make_variant_dir(tmpdir)
            variant_files = {
                "tuning_A.json": os.path.join(vdir, "tuning_A.json"),
                "tuning_B.json": os.path.join(vdir, "tuning_B.json"),
            }
            meta = build_meta(
                exp_id="test-001",
                experiment_path=exp_path,
                variant_files=variant_files,
            )
            self.assertIn("variants", meta["hashes"])
            self.assertIn("tuning_A.json", meta["hashes"]["variants"])
            self.assertIn("tuning_B.json", meta["hashes"]["variants"])

    def test_meta_with_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            result_path = _make_result(tmpdir)
            meta = build_meta(
                exp_id="test-001",
                experiment_path=exp_path,
                variant_files={},
                result_path=result_path,
            )
            self.assertIn("result", meta["hashes"])

    def test_meta_with_repos_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            repos_path = os.path.join(tmpdir, "repos.yaml")
            with open(repos_path, "w") as f:
                f.write("defaults: {}\n")
            meta = build_meta(
                exp_id="test-001",
                experiment_path=exp_path,
                variant_files={},
                repos_yaml_path=repos_path,
            )
            self.assertIn("repos_yaml", meta["hashes"])


# ---------------------------------------------------------------------------
# archive_experiment
# ---------------------------------------------------------------------------


class TestArchiveExperiment(unittest.TestCase):
    def test_basic_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            result_path = _make_result(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                result_path=result_path,
                registry_root=registry,
            )

            self.assertEqual(summary["status"], "archived")
            self.assertEqual(summary["exp_id"], "exp-test-001")
            self.assertTrue(os.path.isdir(summary["exp_dir"]))
            self.assertTrue(os.path.isdir(summary["run_dir"]))

            # Check files exist
            self.assertTrue(
                os.path.exists(os.path.join(summary["exp_dir"], "experiment.json"))
            )
            self.assertTrue(os.path.exists(summary["meta_path"]))
            self.assertTrue(
                os.path.exists(os.path.join(summary["run_dir"], "result.json"))
            )

    def test_archive_with_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            vdir = _make_variant_dir(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                variant_dir=vdir,
                registry_root=registry,
            )

            variants_dir = os.path.join(summary["exp_dir"], "variants")
            self.assertTrue(os.path.exists(os.path.join(variants_dir, "tuning_A.json")))
            self.assertTrue(os.path.exists(os.path.join(variants_dir, "tuning_B.json")))
            self.assertTrue(os.path.exists(os.path.join(variants_dir, "patch_A.diff")))

    def test_archive_with_result_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            result_path = _make_result(tmpdir)
            result_md = _make_result_md(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                result_path=result_path,
                result_md_path=result_md,
                registry_root=registry,
            )

            self.assertTrue(
                os.path.exists(os.path.join(summary["run_dir"], "result.md"))
            )

    def test_idempotency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            result_path = _make_result(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            s1 = archive_experiment(
                experiment_path=exp_path,
                result_path=result_path,
                registry_root=registry,
            )
            self.assertEqual(s1["status"], "archived")

            s2 = archive_experiment(
                experiment_path=exp_path,
                result_path=result_path,
                registry_root=registry,
            )
            self.assertEqual(s2["status"], "already_archived")

    def test_different_results_get_new_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            # First result
            r1_path = os.path.join(tmpdir, "result1.json")
            _write_json(r1_path, {"winner": "A", "verdict": "winner"})

            s1 = archive_experiment(
                experiment_path=exp_path,
                result_path=r1_path,
                registry_root=registry,
            )

            # Second result (different content)
            r2_path = os.path.join(tmpdir, "result2.json")
            _write_json(r2_path, {"winner": "B", "verdict": "winner"})

            s2 = archive_experiment(
                experiment_path=exp_path,
                result_path=r2_path,
                registry_root=registry,
            )

            self.assertEqual(s1["status"], "archived")
            self.assertEqual(s2["status"], "archived")
            self.assertNotEqual(s1["run_id"], s2["run_id"])

    def test_no_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                registry_root=registry,
            )

            self.assertEqual(summary["status"], "archived")
            self.assertIsNone(summary["result_hash"])

    def test_experiment_not_overwritten(self):
        """Second archive should not overwrite experiment.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            s1 = archive_experiment(
                experiment_path=exp_path,
                registry_root=registry,
            )

            dest_exp = os.path.join(s1["exp_dir"], "experiment.json")
            mtime1 = os.path.getmtime(dest_exp)

            # Modify source and re-archive
            time.sleep(0.05)
            with open(exp_path, encoding="utf-8") as f:
                data = json.load(f)
            data["description"] = "Modified"
            _write_json(exp_path, data)

            archive_experiment(
                experiment_path=exp_path,
                registry_root=registry,
            )

            mtime2 = os.path.getmtime(dest_exp)
            # Should NOT have been overwritten
            self.assertEqual(mtime1, mtime2)

    def test_meta_json_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            result_path = _make_result(tmpdir)
            vdir = _make_variant_dir(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                result_path=result_path,
                variant_dir=vdir,
                registry_root=registry,
            )

            with open(summary["meta_path"]) as f:
                meta = json.load(f)

            self.assertEqual(meta["registry_version"], REGISTRY_VERSION)
            self.assertEqual(meta["exp_id"], "exp-test-001")
            self.assertIn("experiment", meta["hashes"])
            self.assertIn("result", meta["hashes"])
            self.assertIn("variants", meta["hashes"])


# ---------------------------------------------------------------------------
# validate_registry_entry
# ---------------------------------------------------------------------------


class TestValidateRegistryEntry(unittest.TestCase):
    def test_valid_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            result_path = _make_result(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                result_path=result_path,
                registry_root=registry,
            )

            errors = validate_registry_entry(summary["exp_dir"])
            self.assertEqual(errors, [])

    def test_missing_experiment_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "variants"))
            os.makedirs(os.path.join(tmpdir, "results"))
            _write_json(
                os.path.join(tmpdir, "meta.json"),
                {"registry_version": REGISTRY_VERSION},
            )
            errors = validate_registry_entry(tmpdir)
            self.assertIn("Missing experiment.json", errors)

    def test_missing_meta_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_json(
                os.path.join(tmpdir, "experiment.json"),
                {"experiment_schema_version": 1, "id": "test"},
            )
            os.makedirs(os.path.join(tmpdir, "variants"))
            os.makedirs(os.path.join(tmpdir, "results"))
            errors = validate_registry_entry(tmpdir)
            self.assertIn("Missing meta.json", errors)

    def test_disallowed_file_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_path = _make_experiment(tmpdir)
            registry = os.path.join(tmpdir, "experiments")

            summary = archive_experiment(
                experiment_path=exp_path,
                registry_root=registry,
            )

            # Add a disallowed file
            bad_file = os.path.join(summary["exp_dir"], "binary.exe")
            with open(bad_file, "w") as f:
                f.write("bad")

            errors = validate_registry_entry(summary["exp_dir"])
            self.assertTrue(any("Disallowed file type" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
