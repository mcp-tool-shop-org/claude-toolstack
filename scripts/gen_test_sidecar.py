#!/usr/bin/env python3
"""Generate a synthetic sidecar artifact for CI smoke tests.

Produces a valid sidecar JSON file without needing a running gateway.
Used by the sidecar-artifacts CI job to prove the validate/scan pipeline.
"""
from __future__ import annotations

import json
import sys
import time

from cts.schema import wrap_bundle


def main() -> None:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "ci-artifact.json"

    bundle = {
        "version": 2,
        "mode": "default",
        "repo": "org/example-repo",
        "request_id": "ci-smoke-test",
        "timestamp": time.time(),
        "query": "login",
        "ranked_sources": [
            {"path": "src/auth.py", "line": 10, "score": 1.5},
            {"path": "src/session.py", "line": 5, "score": 0.8},
        ],
        "matches": [
            {"path": "src/auth.py", "line": 10, "snippet": "def login(user):"},
            {"path": "src/session.py", "line": 5, "snippet": "session.login(u)"},
        ],
        "slices": [
            {
                "path": "src/auth.py",
                "start": 1,
                "lines": ["import hashlib", "def login(user):", "    pass"],
            },
        ],
        "symbols": [],
        "diff": "",
        "suggested_commands": ["cts slice --repo org/example-repo src/auth.py:1-50"],
        "notes": [],
        "truncated": False,
    }

    sidecar = wrap_bundle(
        bundle,
        mode="default",
        request_id="ci-smoke-test",
        cli_version="0.2.0",
        repo="org/example-repo",
        query="login",
        inputs={"query": "login", "max": 50},
    )

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)

    print(f"Generated: {out_path}")


if __name__ == "__main__":
    main()
