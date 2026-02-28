"""Diff context extraction for change-aware ranking signals.

Parses unified diff text to extract:
  - changed_files: set of file paths that were modified
  - hunk_ranges: per-file line ranges from @@ headers
  - changed_identifiers: word-like tokens from added (+) lines
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

# Hunk header: @@ -old,count +new,count @@
_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Identifier-like tokens (letters/digits/underscores, >= 2 chars)
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]+\b")

# Tolerance for hunk overlap (lines before/after hunk)
HUNK_OVERLAP_TOLERANCE = 5


def build_diff_context(diff_text: str) -> Dict[str, Any]:
    """Parse unified diff and extract context for ranking.

    Returns dict with:
      changed_files: set of file paths
      hunk_ranges: dict mapping path -> list of (start, end) tuples
      changed_identifiers: set of identifier tokens from + lines
    """
    if not diff_text:
        return _empty_context()

    changed_files: Set[str] = set()
    hunk_ranges: Dict[str, List[Tuple[int, int]]] = {}
    changed_identifiers: Set[str] = set()

    current_path = ""

    for line in diff_text.splitlines():
        # +++ b/path/to/file.ext
        if line.startswith("+++ b/"):
            current_path = line[6:]
            changed_files.add(current_path)
            continue

        # @@ -old,count +new,count @@
        if line.startswith("@@") and current_path:
            m = _HUNK_RE.search(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                end = start + count - 1
                if current_path not in hunk_ranges:
                    hunk_ranges[current_path] = []
                hunk_ranges[current_path].append((start, end))
            continue

        # Added lines: extract identifiers
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:]  # strip leading +
            for tok in _IDENT_RE.findall(content):
                changed_identifiers.add(tok)

    return {
        "changed_files": changed_files,
        "hunk_ranges": hunk_ranges,
        "changed_identifiers": changed_identifiers,
    }


def is_in_hunk(
    path: str,
    line: int,
    hunk_ranges: Dict[str, List[Tuple[int, int]]],
    tolerance: int = HUNK_OVERLAP_TOLERANCE,
) -> bool:
    """Check if a file:line falls within (or near) a diff hunk."""
    ranges = hunk_ranges.get(path, [])
    for start, end in ranges:
        if (start - tolerance) <= line <= (end + tolerance):
            return True
    return False


def _empty_context() -> Dict[str, Any]:
    return {
        "changed_files": set(),
        "hunk_ranges": {},
        "changed_identifiers": set(),
    }
