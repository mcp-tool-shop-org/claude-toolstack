"""Tests for cts doctor and cts perf commands."""

from __future__ import annotations

import json
import os
import sys
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# cts doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    """Tests for cmd_doctor."""

    def _run_doctor(self, monkeypatch, fmt="text", cwd=None):
        """Run cmd_doctor with captured output."""
        from cts.cli import cmd_doctor

        ns = mock.MagicMock()
        ns.format = fmt

        # Avoid gateway calls
        monkeypatch.setattr("cts.http.get", mock.MagicMock(side_effect=SystemExit))
        if cwd:
            monkeypatch.chdir(cwd)

        import io

        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        try:
            cmd_doctor(ns)
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0
        return buf.getvalue(), exit_code

    def test_doctor_text_output_has_markers(self, monkeypatch, tmp_path):
        """Doctor output contains pass/warn/fail markers."""
        # Create a minimal project root
        (tmp_path / "repos.yaml").touch()
        (tmp_path / ".git").mkdir()

        out, code = self._run_doctor(monkeypatch, cwd=str(tmp_path))
        # Should have at least one [+] for repo root
        assert "[+]" in out or "[~]" in out

    def test_doctor_json_output_valid(self, monkeypatch, tmp_path):
        """--format json produces valid JSON array."""
        (tmp_path / "repos.yaml").touch()

        out, _ = self._run_doctor(monkeypatch, fmt="json", cwd=str(tmp_path))
        data = json.loads(out)
        assert isinstance(data, list)
        assert all("check" in item and "status" in item for item in data)

    def test_doctor_fails_no_repos_yaml(self, monkeypatch, tmp_path):
        """Doctor reports FAIL when repos.yaml is missing."""
        out, code = self._run_doctor(monkeypatch, cwd=str(tmp_path))
        assert "[!]" in out
        assert code == 1

    def test_doctor_warns_missing_rg(self, monkeypatch, tmp_path):
        """Doctor reports FAIL when ripgrep is not found."""
        import shutil

        (tmp_path / "repos.yaml").touch()
        monkeypatch.setattr(shutil, "which", lambda x: None)

        out, _ = self._run_doctor(monkeypatch, fmt="json", cwd=str(tmp_path))
        data = json.loads(out)
        rg_check = [c for c in data if c["check"] == "Ripgrep"]
        assert len(rg_check) == 1
        assert rg_check[0]["status"] == "FAIL"

    def test_doctor_semantic_store_found(self, monkeypatch, tmp_path):
        """Doctor reports PASS when semantic store exists."""
        (tmp_path / "repos.yaml").touch()

        # Create a fake semantic store
        cache_dir = tmp_path / "gw-cache" / "test_repo"
        cache_dir.mkdir(parents=True)

        from cts.semantic.store import SemanticStore

        db_path = str(cache_dir / "semantic.sqlite3")
        store = SemanticStore(db_path)
        store.set_meta("model", "test-model")
        store.set_meta("dim", "384")
        store.close()

        out, _ = self._run_doctor(monkeypatch, fmt="json", cwd=str(tmp_path))
        data = json.loads(out)
        sem_checks = [c for c in data if c["check"].startswith("Semantic [")]
        assert len(sem_checks) == 1
        assert sem_checks[0]["status"] == "PASS"
        assert "test-model" in sem_checks[0]["detail"]

    def test_doctor_warns_no_semantic_stores(self, monkeypatch, tmp_path):
        """Doctor warns when no semantic stores exist."""
        (tmp_path / "repos.yaml").touch()

        out, _ = self._run_doctor(monkeypatch, fmt="json", cwd=str(tmp_path))
        data = json.loads(out)
        sem_checks = [c for c in data if "Semantic" in c["check"]]
        assert len(sem_checks) >= 1
        assert sem_checks[0]["status"] == "WARN"


# ---------------------------------------------------------------------------
# cts perf
# ---------------------------------------------------------------------------


class TestPerf:
    """Tests for cmd_perf."""

    def _run_perf(self, monkeypatch, fmt="text", env_overrides=None):
        """Run cmd_perf with captured output."""
        from cts.cli import cmd_perf

        ns = mock.MagicMock()
        ns.format = fmt

        if env_overrides:
            for k, v in env_overrides.items():
                monkeypatch.setenv(k, v)

        import io

        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        cmd_perf(ns)
        return buf.getvalue()

    def test_perf_text_output_has_knobs(self, monkeypatch):
        """Perf output contains known knob names."""
        out = self._run_perf(monkeypatch)
        assert "semantic_enabled" in out
        assert "chunk_lines" in out
        assert "candidate_strategy" in out
        assert "device" in out

    def test_perf_json_valid(self, monkeypatch):
        """--format json produces valid JSON array."""
        out = self._run_perf(monkeypatch, fmt="json")
        data = json.loads(out)
        assert isinstance(data, list)
        assert all("name" in k and "value" in k and "source" in k for k in data)

    def test_perf_env_override_detected(self, monkeypatch):
        """Env-overridden knobs show source='env'."""
        out = self._run_perf(
            monkeypatch,
            fmt="json",
            env_overrides={"CTS_SEMANTIC_TOPK": "16"},
        )
        data = json.loads(out)
        topk = [k for k in data if k["name"] == "topk_chunks"]
        assert len(topk) == 1
        assert topk[0]["source"] == "env"
        assert topk[0]["value"] == 16

    def test_perf_default_source(self, monkeypatch):
        """Knobs without env override show source='default'."""
        # Clear any semantic env vars
        for key in list(os.environ):
            if key.startswith("CTS_SEMANTIC_"):
                monkeypatch.delenv(key, raising=False)

        out = self._run_perf(monkeypatch, fmt="json")
        data = json.loads(out)
        defaults = [k for k in data if k["source"] == "default"]
        assert len(defaults) > 0

    def test_perf_text_has_env_marker(self, monkeypatch):
        """Text output uses * marker for env-set values."""
        out = self._run_perf(
            monkeypatch,
            env_overrides={"CTS_SEMANTIC_DEVICE": "cuda"},
        )
        # The * marker should appear for the device line
        assert "* " in out or "*" in out

    def test_perf_shows_tips(self, monkeypatch):
        """Text output includes tip text."""
        out = self._run_perf(monkeypatch)
        assert "Embedding model" in out or "auto" in out
