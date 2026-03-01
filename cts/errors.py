"""Structured error shape for Claude Toolstack CLI.

Tier 2 error contract (CLI/MCP/desktop):
  - Every error carries: code, message, hint, cause?, retryable?
  - Exit codes: 0 ok, 1 user error, 2 runtime error, 3 partial success
  - Safe mode: user-facing string (no stack traces)
  - Debug mode: full cause chain (--debug flag)

Error code namespaces:
  IO_     — file/network I/O failures
  CONFIG_ — bad configuration, missing env vars
  PERM_   — permission / auth errors
  DEP_    — missing dependency
  RUNTIME_— unexpected runtime failure
  INPUT_  — bad user input (args, flags, file format)
  STATE_  — invalid state (corrupt store, stale index)
"""

from __future__ import annotations

import sys
import traceback


class CtsError(Exception):
    """Structured CLI error with code, message, hint, and exit code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        cause: Exception | None = None,
        retryable: bool = False,
        exit_code: int = 1,
    ):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.cause = cause
        self.retryable = retryable
        self.exit_code = exit_code

    def safe_str(self) -> str:
        """User-facing string (no stack traces)."""
        parts = [f"error[{self.code}]: {self}"]
        if self.hint:
            parts.append(f"  hint: {self.hint}")
        if self.retryable:
            parts.append("  (retryable)")
        return "\n".join(parts)

    def debug_str(self) -> str:
        """Debug string with full cause chain."""
        parts = [self.safe_str()]
        if self.cause:
            parts.append(f"  cause: {type(self.cause).__name__}: {self.cause}")
            tb = traceback.format_exception(
                type(self.cause), self.cause, self.cause.__traceback__
            )
            parts.append("  traceback:")
            for line in tb:
                for sub in line.rstrip().split("\n"):
                    parts.append(f"    {sub}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        """JSON-serializable error dict."""
        d: dict = {
            "code": self.code,
            "message": str(self),
            "hint": self.hint,
            "retryable": self.retryable,
        }
        if self.cause:
            d["cause"] = f"{type(self.cause).__name__}: {self.cause}"
        return d


def handle_cli_error(exc: BaseException, *, debug: bool = False) -> int:
    """Print error and return exit code. Used by main()."""
    if isinstance(exc, CtsError):
        if debug:
            print(exc.debug_str(), file=sys.stderr)
        else:
            print(exc.safe_str(), file=sys.stderr)
        return exc.exit_code

    if isinstance(exc, SystemExit):
        return exc.code if isinstance(exc.code, int) else 0

    if isinstance(exc, KeyboardInterrupt):
        print("\nInterrupted.", file=sys.stderr)
        return 130

    # Unexpected error — runtime exit code 2
    if debug:
        traceback.print_exc(file=sys.stderr)
    else:
        print(
            f"error[RUNTIME_UNEXPECTED]: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "  hint: re-run with --debug for full traceback",
            file=sys.stderr,
        )
    return 2
