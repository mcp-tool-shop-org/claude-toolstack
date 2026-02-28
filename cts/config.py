"""Configuration: env vars + defaults."""

from __future__ import annotations

import os


def gateway_url() -> str:
    return os.getenv("CLAUDE_TOOLSTACK_URL", "http://127.0.0.1:8088").rstrip("/")


def api_key() -> str:
    key = os.getenv("CLAUDE_TOOLSTACK_API_KEY", "")
    if not key:
        raise SystemExit(
            "Error: CLAUDE_TOOLSTACK_API_KEY not set.\n"
            "  export CLAUDE_TOOLSTACK_API_KEY=<your-key>"
        )
    return key
