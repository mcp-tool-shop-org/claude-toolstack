"""Tests for cts.sidecar — load, validate, summarize, secrets scan."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from cts.schema import BUNDLE_SCHEMA_VERSION, wrap_bundle
from cts.sidecar import (
    load,
    load_text,
    secrets_scan,
    summarize,
    validate_envelope,
    validate_stability_contract,
)


def _valid_sidecar() -> dict:
    """A fully valid sidecar envelope."""
    bundle = {
        "version": 2,
        "mode": "default",
        "repo": "org/repo",
        "request_id": "req-1",
        "timestamp": 1700000000.0,
        "query": "login",
        "ranked_sources": [
            {"path": "src/auth.py", "line": 10, "score": 1.2},
        ],
        "matches": [
            {"path": "src/auth.py", "line": 10, "snippet": "def login():"},
        ],
        "slices": [],
        "symbols": [],
        "diff": "",
        "suggested_commands": ["cts slice --repo org/repo src/auth.py:1-50"],
        "notes": [],
        "truncated": False,
    }
    return wrap_bundle(
        bundle,
        mode="default",
        request_id="req-1",
        cli_version="0.2.0",
        repo="org/repo",
        query="login",
        created_at=1700000000.0,
        inputs={"query": "login", "max": 50},
    )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


class TestLoad(unittest.TestCase):
    def test_load_valid_file(self):
        sidecar = _valid_sidecar()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sidecar, f)
            f.flush()
            path = f.name
        try:
            data = load(path)
            self.assertEqual(data["bundle_schema_version"], BUNDLE_SCHEMA_VERSION)
        finally:
            os.unlink(path)

    def test_load_text(self):
        sidecar = _valid_sidecar()
        text = json.dumps(sidecar)
        data = load_text(text)
        self.assertEqual(data["mode"], "default")

    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json {{{")
            path = f.name
        try:
            with self.assertRaises(json.JSONDecodeError):
                load(path)
        finally:
            os.unlink(path)

    def test_load_array_raises(self):
        with self.assertRaises(ValueError):
            load_text("[1, 2, 3]")

    def test_load_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load("/nonexistent/path/bundle.json")


# ---------------------------------------------------------------------------
# Validate envelope
# ---------------------------------------------------------------------------


class TestValidateEnvelope(unittest.TestCase):
    def test_valid_sidecar_no_errors(self):
        errors = validate_envelope(_valid_sidecar())
        self.assertEqual(errors, [])

    def test_missing_required_key(self):
        s = _valid_sidecar()
        del s["mode"]
        errors = validate_envelope(s)
        self.assertTrue(any("missing required key: mode" in e for e in errors))

    def test_wrong_type(self):
        s = _valid_sidecar()
        s["mode"] = 42
        errors = validate_envelope(s)
        self.assertTrue(any("mode:" in e and "expected" in e for e in errors))

    def test_unsupported_schema_version(self):
        s = _valid_sidecar()
        s["bundle_schema_version"] = 99
        errors = validate_envelope(s)
        self.assertTrue(any("unsupported" in e for e in errors))

    def test_invalid_mode(self):
        s = _valid_sidecar()
        s["mode"] = "bogus"
        errors = validate_envelope(s)
        self.assertTrue(any("invalid mode" in e for e in errors))

    def test_missing_tool_name(self):
        s = _valid_sidecar()
        del s["tool"]["name"]
        errors = validate_envelope(s)
        self.assertTrue(any("tool.name" in e for e in errors))

    def test_missing_tool_cli_version(self):
        s = _valid_sidecar()
        del s["tool"]["cli_version"]
        errors = validate_envelope(s)
        self.assertTrue(any("tool.cli_version" in e for e in errors))

    def test_bad_passes_item(self):
        s = _valid_sidecar()
        s["passes"] = ["not_a_dict"]
        errors = validate_envelope(s)
        self.assertTrue(any("passes[0]" in e for e in errors))

    def test_missing_final_version(self):
        s = _valid_sidecar()
        del s["final"]["version"]
        errors = validate_envelope(s)
        self.assertTrue(any("final.version" in e for e in errors))


# ---------------------------------------------------------------------------
# Validate stability contract
# ---------------------------------------------------------------------------


class TestValidateStabilityContract(unittest.TestCase):
    def test_valid_keys_no_warnings(self):
        warnings = validate_stability_contract(_valid_sidecar())
        self.assertEqual(warnings, [])

    def test_unexpected_key(self):
        s = _valid_sidecar()
        s["_sneaky_extra"] = "whoops"
        warnings = validate_stability_contract(s)
        self.assertTrue(any("_sneaky_extra" in w for w in warnings))

    def test_extra_ok_suppresses_warning(self):
        s = _valid_sidecar()
        s["custom_field"] = "allowed"
        warnings = validate_stability_contract(s, extra_ok={"custom_field"})
        self.assertEqual(warnings, [])


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------


class TestSummarize(unittest.TestCase):
    def test_text_summary_contains_repo(self):
        text = summarize(_valid_sidecar(), format="text")
        self.assertIn("org/repo", text)

    def test_text_summary_contains_mode(self):
        text = summarize(_valid_sidecar(), format="text")
        self.assertIn("default", text)

    def test_text_summary_contains_top_files(self):
        text = summarize(_valid_sidecar(), format="text")
        self.assertIn("src/auth.py", text)

    def test_markdown_summary_contains_table(self):
        md = summarize(_valid_sidecar(), format="markdown")
        self.assertIn("| Repo |", md)
        self.assertIn("`org/repo`", md)

    def test_markdown_summary_contains_header(self):
        md = summarize(_valid_sidecar(), format="markdown")
        self.assertIn("### Evidence Bundle Summary", md)

    def test_markdown_contains_download_note(self):
        md = summarize(_valid_sidecar(), format="markdown")
        self.assertIn("Download the full artifact", md)

    def test_summary_with_passes(self):
        s = _valid_sidecar()
        s["passes"] = [
            {
                "pass": 1,
                "actions": ["widen_search"],
                "confidence_before": 0.35,
                "status": "ok",
            }
        ]
        text = summarize(s, format="text")
        self.assertIn("passes:", text)

        md = summarize(s, format="markdown")
        self.assertIn("Autopilot", md)
        self.assertIn("widen_search", md)


# ---------------------------------------------------------------------------
# Secrets scan
# ---------------------------------------------------------------------------


class TestSecretsScan(unittest.TestCase):
    def test_clean_sidecar_no_findings(self):
        findings = secrets_scan(_valid_sidecar())
        self.assertEqual(findings, [])

    def test_detects_github_token(self):
        s = _valid_sidecar()
        s["final"]["matches"][0]["snippet"] = (
            "token = 'ghp_ABC123def456ghi789jkl012mno345pqr678'"
        )
        findings = secrets_scan(s)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["type"], "GitHub Token")

    def test_detects_aws_key(self):
        s = _valid_sidecar()
        s["final"]["matches"][0]["snippet"] = "access_key = AKIAIOSFODNN7EXAMPLE"
        findings = secrets_scan(s)
        types = [f["type"] for f in findings]
        self.assertIn("AWS Access Key", types)

    def test_detects_private_key(self):
        s = _valid_sidecar()
        s["final"]["diff"] = "-----BEGIN RSA PRIVATE KEY-----\nMIIEp..."
        findings = secrets_scan(s)
        self.assertTrue(any(f["type"] == "Private Key Block" for f in findings))

    def test_detects_slack_token(self):
        s = _valid_sidecar()
        s["final"]["matches"][0]["snippet"] = "SLACK_TOKEN=xoxb-1234567890-abcdefghij"
        findings = secrets_scan(s)
        types = [f["type"] for f in findings]
        self.assertIn("Slack Token", types)

    def test_detects_generic_api_key(self):
        s = _valid_sidecar()
        s["final"]["matches"][0]["snippet"] = (
            "api_key = 'test_fake_key_not_real_abcdef1234567890'"
        )
        findings = secrets_scan(s)
        types = [f["type"] for f in findings]
        self.assertIn("Generic API Key", types)

    def test_detects_jwt(self):
        s = _valid_sidecar()
        header = "eyJhbGciOiJIUzI1NiJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        s["final"]["matches"][0]["snippet"] = f"token = '{header}.{payload}.signature'"
        findings = secrets_scan(s)
        types = [f["type"] for f in findings]
        self.assertIn("JWT Token", types)

    def test_redacted_format(self):
        s = _valid_sidecar()
        s["final"]["matches"][0]["snippet"] = (
            "token = 'ghp_ABC123def456ghi789jkl012mno345pqr678'"
        )
        findings = secrets_scan(s)
        redacted = findings[0]["redacted"]
        # Should show first 4 and last 4 chars
        self.assertTrue(redacted.startswith("ghp_"))
        self.assertIn("***", redacted)

    def test_ignore_patterns(self):
        s = _valid_sidecar()
        s["final"]["matches"][0]["snippet"] = (
            "token = 'ghp_ABC123def456ghi789jkl012mno345pqr678'"
        )
        findings = secrets_scan(s, ignore_patterns=[r"ghp_ABC"])
        self.assertEqual(findings, [])

    def test_scans_passes(self):
        s = _valid_sidecar()
        s["passes"] = [{"reason": "ghp_SEC123def456ghi789jkl012mno345pqr678"}]
        findings = secrets_scan(s)
        locs = [f["location"] for f in findings]
        self.assertTrue(any("passes[0]" in loc for loc in locs))

    def test_scans_debug_section(self):
        s = _valid_sidecar()
        s["final"]["_debug"] = {"note": "api_key = SUPERSECRETKEY12345678901234"}
        findings = secrets_scan(s)
        self.assertGreater(len(findings), 0)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestSidecarCli(unittest.TestCase):
    def test_sidecar_validate_subcommand(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["sidecar", "validate", "artifact.json"])
        self.assertEqual(args.command, "sidecar")
        self.assertEqual(args.sidecar_action, "validate")
        self.assertEqual(args.file, "artifact.json")

    def test_sidecar_summarize_subcommand(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["sidecar", "summarize", "artifact.json", "--format", "markdown"]
        )
        self.assertEqual(args.sidecar_action, "summarize")
        self.assertEqual(args.summary_format, "markdown")

    def test_sidecar_secrets_scan_subcommand(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["sidecar", "secrets-scan", "artifact.json", "--fail", "--report"]
        )
        self.assertEqual(args.sidecar_action, "secrets-scan")
        self.assertTrue(args.fail)
        self.assertTrue(args.report)

    def test_validate_on_valid_file(self):
        """End-to-end: write valid sidecar, validate it."""
        sidecar = _valid_sidecar()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sidecar, f)
            path = f.name
        try:
            from cts.cli import main
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["sidecar", "validate", path])
            output = buf.getvalue()
            self.assertIn("OK", output)
        finally:
            os.unlink(path)

    def test_validate_on_invalid_file(self):
        """End-to-end: invalid sidecar fails validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"not": "a sidecar"}, f)
            path = f.name
        try:
            from cts.cli import main

            with self.assertRaises(SystemExit) as ctx:
                main(["sidecar", "validate", path])
            self.assertEqual(ctx.exception.code, 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
