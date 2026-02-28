"""Match ranking: path weighting, stack trace boost, recency.

Supports explain mode for per-candidate signal breakdown.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple, Union

# Path segments that indicate "real code" (boost)
PREFERRED_ROOTS = {
    "src",
    "app",
    "lib",
    "cmd",
    "pkg",
    "internal",
    "core",
    "server",
    "api",
    "services",
    "handlers",
}

# Path segments that indicate low-signal (demote)
DEPRIORITIZED_ROOTS = {
    "vendor",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".turbo",
    ".cache",
    "coverage",
    "__pycache__",
    "test_data",
    "fixtures",
    "testdata",
    "mocks",
}

# ---------------------------------------------------------------------------
# Stack trace extraction
# ---------------------------------------------------------------------------

# Patterns that extract file:line from common stack trace formats
_TRACE_PATTERNS = [
    # Python: File "path.py", line 42
    re.compile(r'File "([^"]+)", line (\d+)'),
    # Node/JS: at funcName (path.js:42:10) or at path.js:42:10
    re.compile(r"at (?:\S+ \()?([^():]+):(\d+)(?::\d+)?\)?"),
    # Java: at pkg.Class.method(File.java:42)
    re.compile(r"at .+\(([A-Za-z0-9_]+\.\w+):(\d+)\)"),
    # Go: /path/to/file.go:42
    re.compile(r"\s+(/?\S+\.go):(\d+)"),
    # Rust: --> src/main.rs:42:10
    re.compile(r"-->\s+(.+?):(\d+)(?::\d+)?"),
    # .NET: in Namespace.Class.Method() in /path/File.cs:line 42
    re.compile(r"in (\S+\.cs):line (\d+)"),
    # Generic: path/file.ext:42 at start of line
    re.compile(r"^\s*(\S+\.\w{1,5}):(\d+)"),
]


def extract_trace_files(
    text: str,
) -> List[Tuple[str, int]]:
    """Extract (file_path, line_number) pairs from stack trace text.

    Returns de-duplicated list ordered by first appearance.
    """
    seen = set()
    results: List[Tuple[str, int]] = []

    for line in text.splitlines():
        for pattern in _TRACE_PATTERNS:
            m = pattern.search(line)
            if m:
                fpath = m.group(1)
                try:
                    lineno = int(m.group(2))
                except (ValueError, IndexError):
                    lineno = 1
                key = (fpath, lineno)
                if key not in seen:
                    seen.add(key)
                    results.append(key)
                break  # one match per line

    return results


def looks_like_stack_trace(text: str) -> bool:
    """Heuristic: does this text contain a stack trace?"""
    indicators = [
        "Traceback (most recent call last)",
        "at ",
        'File "',
        "Error:",
        "Exception:",
        "panic:",
        "FAILED",
        "error[E",
        "System.Exception",
        "NullReferenceException",
    ]
    lines = text.splitlines()
    hits = sum(1 for ln in lines if any(ind in ln for ind in indicators))
    return hits >= 2


# ---------------------------------------------------------------------------
# Path scoring
# ---------------------------------------------------------------------------


def path_score(
    path: str,
    prefer: Optional[List[str]] = None,
    avoid: Optional[List[str]] = None,
) -> float:
    """Score a file path: higher = more relevant.

    Default range roughly -1.0 to +1.0.
    """
    return path_score_explained(path, prefer, avoid)["score"]


def path_score_explained(
    path: str,
    prefer: Optional[List[str]] = None,
    avoid: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Score a path and return signal breakdown.

    Returns dict with:
      score: float
      path_boost: float
      path_penalty: float
      test_penalty: float
      classification: preferred|avoided|neutral
      prefer_match: str|None  (which segment matched)
      avoid_match: str|None
    """
    parts = set(path.replace("\\", "/").split("/"))

    preferred = PREFERRED_ROOTS
    if prefer:
        preferred = preferred | set(prefer)
    deprioritized = DEPRIORITIZED_ROOTS
    if avoid:
        deprioritized = deprioritized | set(avoid)

    path_boost = 0.0
    path_penalty = 0.0
    test_penalty = 0.0
    prefer_match: Optional[str] = None
    avoid_match: Optional[str] = None
    classification = "neutral"

    hit = parts & preferred
    if hit:
        path_boost = 0.5
        prefer_match = sorted(hit)[0]
        classification = "preferred"

    hit_avoid = parts & deprioritized
    if hit_avoid:
        path_penalty = -0.8
        avoid_match = sorted(hit_avoid)[0]
        classification = "avoided"

    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    if basename.startswith("test_") or basename.endswith("_test.go"):
        test_penalty = -0.2
    if ".test." in basename or ".spec." in basename:
        test_penalty = -0.2

    score = path_boost + path_penalty + test_penalty

    return {
        "score": score,
        "path_boost": path_boost,
        "path_penalty": path_penalty,
        "test_penalty": test_penalty,
        "classification": classification,
        "prefer_match": prefer_match,
        "avoid_match": avoid_match,
    }


# ---------------------------------------------------------------------------
# Recency scoring (git log)
# ---------------------------------------------------------------------------


def file_recency_hours(repo_path: str, file_path: str) -> Optional[float]:
    """Get hours since last commit touching this file.

    Returns None if git is unavailable or file has no history.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "-n",
                "1",
                "--format=%ct",
                "--",
                file_path,
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            import time

            ts = int(result.stdout.strip())
            hours = (time.time() - ts) / 3600
            return max(0.0, hours)
    except Exception:
        pass
    return None


def recency_score(hours: Optional[float]) -> float:
    """Convert hours-since-change to a boost score.

    Recent files (< 24h) get +0.3, older files get less.
    """
    if hours is None:
        return 0.0
    if hours < 24:
        return 0.3
    if hours < 168:  # 1 week
        return 0.15
    if hours < 720:  # 1 month
        return 0.05
    return 0.0


# ---------------------------------------------------------------------------
# Composite ranking
# ---------------------------------------------------------------------------


def rank_matches(
    matches: List[Dict[str, Any]],
    trace_files: Optional[List[Tuple[str, int]]] = None,
    prefer_paths: Optional[List[str]] = None,
    avoid_paths: Optional[List[str]] = None,
    repo_root: Optional[str] = None,
    explain: bool = False,
) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """Re-rank matches using path score + trace boost + recency.

    Returns a sorted list with _rank_score attached.
    If *explain=True*, returns (ranked, score_cards) where score_cards
    is a list of per-candidate signal breakdowns.
    """
    trace_set = set()
    if trace_files:
        for fpath, _ in trace_files:
            trace_set.add(fpath)
            if "/" in fpath:
                trace_set.add(fpath.rsplit("/", 1)[-1])

    scored = []
    cards: List[Dict[str, Any]] = []

    for m in matches:
        path = m.get("path", "")

        # Path signal
        path_detail = path_score_explained(path, prefer=prefer_paths, avoid=avoid_paths)
        path_total = path_detail["score"]

        # Trace signal
        trace_boost = 0.0
        is_trace = False
        basename = path.rsplit("/", 1)[-1] if "/" in path else path
        if path in trace_set or basename in trace_set:
            trace_boost = 2.0
            is_trace = True

        # Recency signal
        rec_boost = 0.0
        git_hours: Optional[float] = None
        if repo_root:
            git_hours = file_recency_hours(repo_root, path)
            rec_boost = recency_score(git_hours)

        total = path_total + trace_boost + rec_boost
        scored.append((total, m))

        if explain:
            cards.append(
                {
                    "path": path,
                    "line": m.get("line", 0),
                    "score_total": round(total, 2),
                    "signals": {
                        "path_boost": path_detail["path_boost"],
                        "path_penalty": path_detail["path_penalty"],
                        "test_penalty": path_detail["test_penalty"],
                        "trace_boost": trace_boost,
                        "recency_boost": rec_boost,
                    },
                    "features": {
                        "classification": path_detail["classification"],
                        "is_trace_file": is_trace,
                        "git_age_hours": (
                            round(git_hours, 1) if git_hours is not None else None
                        ),
                        "prefer_match": path_detail["prefer_match"],
                        "avoid_match": path_detail["avoid_match"],
                    },
                }
            )

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for s, m in scored:
        m_copy = dict(m)
        m_copy["_rank_score"] = round(s, 2)
        results.append(m_copy)

    if explain:
        # Sort cards to match ranked order
        cards.sort(key=lambda c: c["score_total"], reverse=True)
        return results, cards

    return results
