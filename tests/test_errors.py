"""Tests for structured error shape (Tier 2)."""

from __future__ import annotations

import json

from cts.errors import CtsError, handle_cli_error


class TestCtsError:
    """CtsError structured shape tests."""

    def test_safe_str_no_hint(self):
        err = CtsError("INPUT_BAD_QUERY", "query cannot be empty")
        s = err.safe_str()
        assert "error[INPUT_BAD_QUERY]" in s
        assert "query cannot be empty" in s
        assert "hint" not in s

    def test_safe_str_with_hint(self):
        err = CtsError(
            "CONFIG_MISSING_KEY",
            "API key not set",
            hint="export CLAUDE_TOOLSTACK_API_KEY=...",
        )
        s = err.safe_str()
        assert "error[CONFIG_MISSING_KEY]" in s
        assert "hint: export" in s

    def test_safe_str_retryable(self):
        err = CtsError("IO_TIMEOUT", "gateway timed out", retryable=True)
        s = err.safe_str()
        assert "(retryable)" in s

    def test_debug_str_includes_cause(self):
        cause = ValueError("bad value")
        err = CtsError("RUNTIME_UNEXPECTED", "failed", cause=cause)
        s = err.debug_str()
        assert "cause: ValueError: bad value" in s
        assert "traceback:" in s

    def test_to_dict_shape(self):
        err = CtsError(
            "IO_READ",
            "cannot read file",
            hint="check permissions",
            retryable=False,
        )
        d = err.to_dict()
        assert d["code"] == "IO_READ"
        assert d["message"] == "cannot read file"
        assert d["hint"] == "check permissions"
        assert d["retryable"] is False
        assert "cause" not in d

    def test_to_dict_with_cause(self):
        cause = OSError("ENOENT")
        err = CtsError("IO_READ", "file not found", cause=cause)
        d = err.to_dict()
        assert "cause" in d
        assert "OSError" in d["cause"]

    def test_exit_code_default(self):
        err = CtsError("INPUT_BAD", "bad input")
        assert err.exit_code == 1

    def test_exit_code_custom(self):
        err = CtsError("RUNTIME_CRASH", "crash", exit_code=2)
        assert err.exit_code == 2

    def test_to_dict_json_serializable(self):
        err = CtsError("CONFIG_MISSING", "missing config", hint="add .env")
        # Should not raise
        json.dumps(err.to_dict())


class TestHandleCliError:
    """Tests for handle_cli_error dispatch."""

    def test_cts_error_returns_exit_code(self):
        err = CtsError("INPUT_BAD", "bad", exit_code=1)
        assert handle_cli_error(err) == 1

    def test_cts_error_runtime_exit_code(self):
        err = CtsError("RUNTIME_FAIL", "fail", exit_code=2)
        assert handle_cli_error(err) == 2

    def test_system_exit_passthrough(self):
        assert handle_cli_error(SystemExit(0)) == 0
        assert handle_cli_error(SystemExit(1)) == 1

    def test_keyboard_interrupt(self):
        assert handle_cli_error(KeyboardInterrupt()) == 130

    def test_unexpected_error_returns_2(self):
        assert handle_cli_error(RuntimeError("boom")) == 2

    def test_debug_mode_shows_traceback(self, capsys):
        try:
            raise ValueError("inner")
        except ValueError as e:
            err = CtsError("TEST", "outer", cause=e)
        handle_cli_error(err, debug=True)
        captured = capsys.readouterr()
        assert "traceback:" in captured.err
