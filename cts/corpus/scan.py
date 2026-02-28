"""Scan directories for sidecar artifact files.

Walks a directory tree, skipping common junk directories,
and returns candidate JSON file paths sorted by modification time.
"""

from __future__ import annotations

import fnmatch
import os
from typing import List, Optional, Set

# Directories to always skip during scan
SKIP_DIRS: Set[str] = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
    ".astro",
    ".next",
    ".nuxt",
}

# Default patterns — all JSON files (CI artifact dirs are flat)
DEFAULT_PATTERNS: List[str] = [
    "*.json",
    "*.sidecar.json",
    "artifact*.json",
]


def scan_dir(
    path: str,
    *,
    patterns: Optional[List[str]] = None,
    max_files: int = 0,
) -> List[str]:
    """Find candidate sidecar JSON files in a directory tree.

    Args:
        path: Root directory to scan.
        patterns: File name patterns (fnmatch-style). Defaults to
                  ``["*.json", "*.sidecar.json", "artifact*.json"]``.
        max_files: Stop after finding this many files (0 = unlimited).

    Returns:
        List of absolute file paths sorted by modification time
        (newest first).
    """
    if not os.path.isdir(path):
        return []

    pats = patterns or DEFAULT_PATTERNS
    found: List[str] = []

    for dirpath, dirnames, filenames in os.walk(path):
        # Prune junk directories in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fname in filenames:
            if any(fnmatch.fnmatch(fname, p) for p in pats):
                fpath = os.path.join(dirpath, fname)
                found.append(fpath)
                if 0 < max_files <= len(found):
                    return _sort_by_mtime(found)

    return _sort_by_mtime(found)


def _sort_by_mtime(paths: List[str]) -> List[str]:
    """Sort file paths by modification time, newest first."""

    def mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    return sorted(paths, key=mtime, reverse=True)
