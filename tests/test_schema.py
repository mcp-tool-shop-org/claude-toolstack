"""Tests for cts.schema — sidecar JSON wrapper stability and correctness."""

from __future__ import annotations

import json
import unittest

from cts.schema import BUNDLE_SCHEMA_VERSION, wrap_bundle


def _dummy_bundle(mode: str = "default") -> dict:
    """Minimal bundle for testing."""
    return {
        "version": 2,
        "mode": mode,
        "repo": "org/repo",
        "request_id": "req-123",
        "timestamp": 1700000000.0,
        "query": "login",
        "ranked_sources": [{"path": "src/auth.py", "line": 10, "score": 0.5}],
        "matches": [{"path": "src/auth.py", "line": 10, "snippet": "def login():"}],
        "slices": [],
        "symbols": [],
        "diff": "",
        "suggested_commands": ["cts slice --repo org/repo src/auth.py:1-100"],
        "notes": [],
        "truncated": False,
    }


class TestWrapBundle(unittest.TestCase):
    """Core wrap_bundle behavior."""

    def test_schema_version_is_integer(self):
        self.assertIsInstance(BUNDLE_SCHEMA_VERSION, int)
        self.assertGreaterEqual(BUNDLE_SCHEMA_VERSION, 1)

    def test_required_top_level_keys(self):
        sidecar = wrap_bundle(
            _dummy_bundle(),
            mode="default",
            request_id="req-123",
            cli_version="0.2.0",
            repo="org/repo",
        )
        required = {
            "bundle_schema_version",
            "created_at",
            "tool",
            "request_id",
            "repo",
            "mode",
            "debug",
            "passes",
            "final",
        }
        self.assertTrue(required.issubset(set(sidecar.keys())))

    def test_tool_info(self):
        sidecar = wrap_bundle(
            _dummy_bundle(),
            mode="default",
            cli_version="0.2.0",
            gateway_version="1.5.0",
        )
        tool = sidecar["tool"]
        self.assertEqual(tool["name"], "cts")
        self.assertEqual(tool["cli_version"], "0.2.0")
        self.assertEqual(tool["gateway_version"], "1.5.0")

    def test_gateway_version_omitted_when_none(self):
        sidecar = wrap_bundle(
            _dummy_bundle(),
            mode="default",
            cli_version="0.2.0",
        )
        self.assertNotIn("gateway_version", sidecar["tool"])

    def test_query_included_when_provided(self):
        sidecar = wrap_bundle(_dummy_bundle(), mode="default", query="login")
        self.assertEqual(sidecar["query"], "login")

    def test_query_omitted_when_none(self):
        sidecar = wrap_bundle(_dummy_bundle(), mode="default")
        self.assertNotIn("query", sidecar)

    def test_inputs_included(self):
        inputs = {"query": "login", "max": 50}
        sidecar = wrap_bundle(_dummy_bundle(), mode="default", inputs=inputs)
        self.assertEqual(sidecar["inputs"], inputs)

    def test_inputs_omitted_when_none(self):
        sidecar = wrap_bundle(_dummy_bundle(), mode="default")
        self.assertNotIn("inputs", sidecar)

    def test_passes_default_empty(self):
        sidecar = wrap_bundle(_dummy_bundle(), mode="default")
        self.assertEqual(sidecar["passes"], [])

    def test_passes_preserved(self):
        passes = [{"pass": 1, "action": "widen_search"}]
        sidecar = wrap_bundle(_dummy_bundle(), mode="default", passes=passes)
        self.assertEqual(sidecar["passes"], passes)

    def test_final_contains_bundle(self):
        bundle = _dummy_bundle()
        sidecar = wrap_bundle(bundle, mode="default")
        self.assertEqual(sidecar["final"]["mode"], "default")
        self.assertEqual(sidecar["final"]["repo"], "org/repo")

    def test_created_at_override(self):
        sidecar = wrap_bundle(_dummy_bundle(), mode="default", created_at=1234567890.0)
        self.assertEqual(sidecar["created_at"], 1234567890.0)

    def test_created_at_defaults_to_now(self):
        import time

        before = time.time()
        sidecar = wrap_bundle(_dummy_bundle(), mode="default")
        after = time.time()
        self.assertGreaterEqual(sidecar["created_at"], before)
        self.assertLessEqual(sidecar["created_at"], after)


class TestSchemaStability(unittest.TestCase):
    """Golden-path tests: sidecar structure must not drift."""

    def test_roundtrip_json_serializable(self):
        sidecar = wrap_bundle(
            _dummy_bundle(),
            mode="default",
            request_id="req-123",
            cli_version="0.2.0",
            repo="org/repo",
            query="login",
            created_at=1700000000.0,
            inputs={"query": "login", "max": 50},
        )
        # Must serialize without error
        payload = json.dumps(sidecar, default=str)
        parsed = json.loads(payload)
        self.assertEqual(parsed["bundle_schema_version"], 1)
        self.assertEqual(parsed["mode"], "default")
        self.assertEqual(parsed["final"]["repo"], "org/repo")

    def test_golden_keys_order(self):
        """Top-level keys should be stable across versions."""
        sidecar = wrap_bundle(
            _dummy_bundle(),
            mode="error",
            request_id="r1",
            cli_version="0.2.0",
            repo="org/repo",
            query="crash",
            inputs={"query": "crash"},
        )
        keys = list(sidecar.keys())
        # bundle_schema_version must always be first
        self.assertEqual(keys[0], "bundle_schema_version")
        # final must always be last
        self.assertEqual(keys[-1], "final")


class TestDebugStripping(unittest.TestCase):
    """_debug key handling in sidecar wrapper."""

    def test_debug_stripped_when_false(self):
        bundle = _dummy_bundle()
        bundle["_debug"] = {"timings_ms": {"total": 42.0}}
        sidecar = wrap_bundle(bundle, mode="default", debug=False)
        self.assertNotIn("_debug", sidecar["final"])

    def test_debug_kept_when_true(self):
        bundle = _dummy_bundle()
        bundle["_debug"] = {"timings_ms": {"total": 42.0}}
        sidecar = wrap_bundle(bundle, mode="default", debug=True)
        self.assertIn("_debug", sidecar["final"])
        self.assertEqual(sidecar["final"]["_debug"]["timings_ms"]["total"], 42.0)

    def test_no_debug_key_no_error(self):
        bundle = _dummy_bundle()
        # No _debug key in bundle — should not raise
        sidecar = wrap_bundle(bundle, mode="default", debug=False)
        self.assertNotIn("_debug", sidecar["final"])


class TestNoSecrets(unittest.TestCase):
    """Sidecar must never leak sensitive data."""

    SENSITIVE_PATTERNS = [
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "credential",
    ]

    def test_sidecar_keys_no_secrets(self):
        """No sidecar key name should suggest it holds secrets."""
        sidecar = wrap_bundle(
            _dummy_bundle(),
            mode="default",
            request_id="r1",
            cli_version="0.2.0",
            repo="org/repo",
            query="test",
            inputs={"query": "test"},
        )
        all_keys = self._collect_keys(sidecar)
        for key in all_keys:
            for pattern in self.SENSITIVE_PATTERNS:
                self.assertNotIn(
                    pattern,
                    key.lower(),
                    f"Key {key!r} looks like it holds secrets",
                )

    def _collect_keys(self, d: dict, prefix: str = "") -> list:
        """Recursively collect all dict key names."""
        keys = []
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            keys.append(full)
            if isinstance(v, dict):
                keys.extend(self._collect_keys(v, full))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        keys.extend(self._collect_keys(item, full))
        return keys


class TestEmitCli(unittest.TestCase):
    """CLI --emit and --format sidecar argument parsing."""

    def test_format_sidecar_accepted(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["search", "login", "--repo", "org/repo", "--format", "sidecar"]
        )
        self.assertEqual(args.format, "sidecar")

    def test_emit_flag_accepted(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "search",
                "login",
                "--repo",
                "org/repo",
                "--emit",
                "/tmp/out.json",
            ]
        )
        self.assertEqual(args.emit, "/tmp/out.json")

    def test_emit_default_none(self):
        from cts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["search", "login", "--repo", "org/repo"])
        self.assertIsNone(args.emit)


if __name__ == "__main__":
    unittest.main()
