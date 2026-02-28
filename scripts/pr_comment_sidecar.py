#!/usr/bin/env python3
"""Post or update a PR comment with a sidecar artifact summary.

Usage:
  python scripts/pr_comment_sidecar.py <artifact.json> [--pr NUMBER]

Requires:
  - ``gh`` CLI authenticated and on PATH
  - ``GITHUB_REPOSITORY`` env var (set automatically in GitHub Actions)
  - Run inside a PR context or pass ``--pr NUMBER``

The script posts a markdown summary to the PR. If a previous summary
comment exists (identified by a hidden marker), it updates that comment
instead of creating a duplicate.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from cts.sidecar import load, summarize

# Hidden HTML marker to identify our comment for updates
_MARKER = "<!-- cts-sidecar-summary -->"


def _get_pr_number() -> str | None:
    """Try to detect the PR number from environment."""
    # GitHub Actions: pull_request event
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        with open(event_path) as f:
            event = json.load(f)
        pr = event.get("pull_request", {}).get("number")
        if pr:
            return str(pr)

    # GITHUB_REF_NAME for PR refs like "123/merge"
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/"):
        parts = ref.split("/")
        if len(parts) >= 3:
            return parts[2]

    return None


def _find_existing_comment(repo: str, pr: str) -> str | None:
    """Find the ID of an existing sidecar summary comment."""
    result = subprocess.run(
        [
            "gh", "api",
            f"repos/{repo}/issues/{pr}/comments",
            "--jq", f'[.[] | select(.body | contains("{_MARKER}"))][0].id',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        cid = result.stdout.strip()
        if cid != "null":
            return cid
    return None


def _post_comment(repo: str, pr: str, body: str) -> None:
    """Create a new PR comment."""
    subprocess.run(
        [
            "gh", "api",
            f"repos/{repo}/issues/{pr}/comments",
            "-f", f"body={body}",
        ],
        check=True,
    )
    print(f"Posted sidecar summary to PR #{pr}")


def _update_comment(repo: str, comment_id: str, body: str, pr: str) -> None:
    """Update an existing PR comment."""
    subprocess.run(
        [
            "gh", "api",
            "-X", "PATCH",
            f"repos/{repo}/issues/comments/{comment_id}",
            "-f", f"body={body}",
        ],
        check=True,
    )
    print(f"Updated sidecar summary on PR #{pr} (comment {comment_id})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post sidecar summary as a PR comment"
    )
    parser.add_argument("artifact", help="Path to sidecar JSON artifact")
    parser.add_argument("--pr", help="PR number (auto-detected in GitHub Actions)")
    args = parser.parse_args()

    # Load and summarize
    try:
        data = load(args.artifact)
    except (json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
        print(f"Error loading artifact: {exc}", file=sys.stderr)
        raise SystemExit(1)

    summary = summarize(data, format="markdown")

    # Build comment body with marker
    body = f"{_MARKER}\n{summary}"

    # Resolve PR number
    pr = args.pr or _get_pr_number()
    if not pr:
        print("Error: could not detect PR number. Pass --pr NUMBER.", file=sys.stderr)
        raise SystemExit(1)

    # Resolve repo
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("Error: GITHUB_REPOSITORY not set.", file=sys.stderr)
        raise SystemExit(1)

    # Post or update
    existing = _find_existing_comment(repo, pr)
    if existing:
        _update_comment(repo, existing, body, pr)
    else:
        _post_comment(repo, pr, body)


if __name__ == "__main__":
    main()
