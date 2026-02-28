"""Evidence bundle orchestrator — v2 structured bundles.

Modes:
  default  — search + ranked matches + context slices
  error    — stack-trace-aware: extract files from trace, boost in ranking
  symbol   — definition + call site bundles
  change   — git diff + hunk context slices
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from cts import http
from cts.ranking import (
    extract_trace_files,
    looks_like_stack_trace,
    rank_matches,
)

# Bundle modes
MODES = ("default", "error", "symbol", "change")

# Limits
DEFAULT_MAX_FILES = 5
DEFAULT_CONTEXT = 30
MAX_SNIPPET_LEN = 200
MINIFIED_AVG_THRESHOLD = 500


# ---------------------------------------------------------------------------
# Bundle data structure
# ---------------------------------------------------------------------------


def _empty_bundle(
    mode: str,
    repo: str,
    request_id: str = "",
    query: str = "",
) -> Dict[str, Any]:
    """Create a blank bundle with v2 metadata header."""
    return {
        "version": 2,
        "mode": mode,
        "repo": repo,
        "request_id": request_id,
        "timestamp": time.time(),
        "query": query,
        "ranked_sources": [],  # path-scored match list
        "matches": [],  # trimmed snippets
        "slices": [],  # context slices
        "symbols": [],  # symbol mode only
        "diff": "",  # change mode only
        "suggested_commands": [],
        "notes": [],
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Slice fetcher (shared across modes)
# ---------------------------------------------------------------------------


def fetch_slices(
    files: List[Dict[str, Any]],
    repo: str,
    request_id: Optional[str] = None,
    context: int = DEFAULT_CONTEXT,
) -> List[Dict[str, Any]]:
    """Fetch file slices around match locations.

    Each entry in *files* should have 'path' and 'line' keys.
    Skips minified files (avg line > 500 chars).
    """
    slices: List[Dict[str, Any]] = []
    seen_paths: set = set()

    for f in files:
        path = f.get("path", "")
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)

        line_no = f.get("line", 1)
        start = max(1, line_no - context)
        end = line_no + context

        try:
            s = http.post(
                "/v1/file/slice",
                {"repo": repo, "path": path, "start": start, "end": end},
                request_id=request_id,
            )
            file_lines = s.get("lines", [])
            if file_lines:
                avg_len = sum(len(ln) for ln in file_lines) / len(file_lines)
                if avg_len > MINIFIED_AVG_THRESHOLD:
                    continue  # skip minified
            slices.append(s)
        except SystemExit:
            continue  # skip on error, don't abort

    return slices


# ---------------------------------------------------------------------------
# Default bundle
# ---------------------------------------------------------------------------


def build_default_bundle(
    search_data: Dict[str, Any],
    repo: str,
    request_id: Optional[str] = None,
    max_files: int = DEFAULT_MAX_FILES,
    context: int = DEFAULT_CONTEXT,
    prefer_paths: Optional[List[str]] = None,
    avoid_paths: Optional[List[str]] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a default evidence bundle from search results."""
    rid = search_data.get("_request_id", request_id or "")
    query = search_data.get("query", "")
    matches = search_data.get("matches", [])

    bundle = _empty_bundle("default", repo, rid, query)
    bundle["truncated"] = search_data.get("truncated", False)

    # Rank matches
    ranked = rank_matches(
        matches,
        prefer_paths=prefer_paths,
        avoid_paths=avoid_paths,
        repo_root=repo_root,
    )

    # Ranked sources + trimmed snippets
    _populate_ranked_and_matches(bundle, ranked)

    # Top K file slices
    top_files = _dedupe_top_files(ranked, max_files)
    bundle["slices"] = fetch_slices(top_files, repo, request_id=rid, context=context)

    # Suggested next commands
    bundle["suggested_commands"] = _suggest_commands(
        repo, query, matches, mode="default"
    )

    return bundle


# ---------------------------------------------------------------------------
# Error bundle (stub — filled in commit 2)
# ---------------------------------------------------------------------------


def build_error_bundle(
    search_data: Dict[str, Any],
    repo: str,
    error_text: str = "",
    request_id: Optional[str] = None,
    max_files: int = DEFAULT_MAX_FILES,
    context: int = DEFAULT_CONTEXT,
    prefer_paths: Optional[List[str]] = None,
    avoid_paths: Optional[List[str]] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an error-aware evidence bundle.

    If error_text contains a stack trace, extract file references
    and boost them in ranking. Falls back to default otherwise.
    """
    rid = search_data.get("_request_id", request_id or "")
    query = search_data.get("query", "")
    matches = search_data.get("matches", [])

    bundle = _empty_bundle("error", repo, rid, query)
    bundle["truncated"] = search_data.get("truncated", False)

    # Extract trace files if error text is provided
    trace_files: List[Tuple[str, int]] = []
    if error_text and looks_like_stack_trace(error_text):
        trace_files = extract_trace_files(error_text)
        bundle["notes"].append(
            f"Stack trace detected: {len(trace_files)} file(s) extracted"
        )

    # Rank with trace boost
    ranked = rank_matches(
        matches,
        trace_files=trace_files,
        prefer_paths=prefer_paths,
        avoid_paths=avoid_paths,
        repo_root=repo_root,
    )

    # Ranked sources + trimmed snippets
    trace_path_set = {tf[0] for tf in trace_files}
    _populate_ranked_and_matches(bundle, ranked, trace_path_set)

    # If we have trace files, prioritize slices around trace lines
    if trace_files:
        trace_entries = [{"path": fp, "line": ln} for fp, ln in trace_files]
        # Trace files first, then top ranked matches
        top_files = _dedupe_top_files(trace_entries + ranked, max_files)
    else:
        top_files = _dedupe_top_files(ranked, max_files)

    bundle["slices"] = fetch_slices(top_files, repo, request_id=rid, context=context)

    bundle["suggested_commands"] = _suggest_commands(repo, query, matches, mode="error")

    return bundle


# ---------------------------------------------------------------------------
# Symbol bundle (stub — filled in commit 3)
# ---------------------------------------------------------------------------


def build_symbol_bundle(
    symbol_data: Dict[str, Any],
    search_data: Optional[Dict[str, Any]],
    repo: str,
    symbol: str = "",
    request_id: Optional[str] = None,
    max_files: int = DEFAULT_MAX_FILES,
    context: int = DEFAULT_CONTEXT,
) -> Dict[str, Any]:
    """Build a symbol evidence bundle.

    Combines symbol definitions with search results for call sites.
    """
    rid = symbol_data.get("_request_id", request_id or "")

    bundle = _empty_bundle("symbol", repo, rid, symbol)

    # Symbol definitions
    defs = symbol_data.get("defs", [])
    for d in defs:
        bundle["symbols"].append(
            {
                "name": d.get("name", ""),
                "kind": d.get("kind", ""),
                "file": d.get("file", ""),
            }
        )

    # Slices around definitions
    def_files = [{"path": d.get("file", ""), "line": 1} for d in defs if d.get("file")]
    bundle["slices"] = fetch_slices(
        def_files[:max_files], repo, request_id=rid, context=context
    )

    # Call sites from search (if available)
    if search_data:
        call_matches = search_data.get("matches", [])
        for m in call_matches:
            snippet = m.get("snippet", "").rstrip()
            if len(snippet) > MAX_SNIPPET_LEN:
                snippet = snippet[:MAX_SNIPPET_LEN] + "..."
            bundle["matches"].append(
                {
                    "path": m.get("path", ""),
                    "line": m.get("line", 0),
                    "snippet": snippet,
                }
            )

    bundle["suggested_commands"] = _suggest_commands(repo, symbol, [], mode="symbol")

    return bundle


# ---------------------------------------------------------------------------
# Change bundle (stub — filled in commit 4)
# ---------------------------------------------------------------------------


def build_change_bundle(
    diff_text: str,
    repo: str,
    request_id: Optional[str] = None,
    max_files: int = DEFAULT_MAX_FILES,
    context: int = DEFAULT_CONTEXT,
) -> Dict[str, Any]:
    """Build a change evidence bundle from git diff output.

    Parses diff hunks and fetches surrounding context.
    """
    bundle = _empty_bundle("change", repo, request_id or "", "")
    bundle["diff"] = diff_text

    # Parse changed files from diff headers
    changed_files = _parse_diff_files(diff_text)
    bundle["notes"].append(f"{len(changed_files)} file(s) changed")

    for cf in changed_files:
        bundle["ranked_sources"].append(
            {
                "path": cf["path"],
                "line": cf.get("line", 1),
                "score": 0.0,
            }
        )

    # Fetch slices around change locations
    bundle["slices"] = fetch_slices(
        changed_files[:max_files],
        repo,
        request_id=request_id,
        context=context,
    )

    bundle["suggested_commands"] = _suggest_commands(repo, "", [], mode="change")

    return bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_ranked_and_matches(
    bundle: Dict[str, Any],
    ranked: List[Dict[str, Any]],
    trace_path_set: Optional[set] = None,
) -> None:
    """Fill ranked_sources and matches from ranked match list."""
    for m in ranked:
        p = m.get("path", "")
        entry: Dict[str, Any] = {
            "path": p,
            "line": m.get("line", 0),
            "score": m.get("_rank_score", 0.0),
        }
        if trace_path_set and p in trace_path_set:
            entry["in_trace"] = True
        bundle["ranked_sources"].append(entry)

    for m in ranked:
        snippet = m.get("snippet", "").rstrip()
        if len(snippet) > MAX_SNIPPET_LEN:
            snippet = snippet[:MAX_SNIPPET_LEN] + "..."
        bundle["matches"].append(
            {
                "path": m.get("path", ""),
                "line": m.get("line", 0),
                "snippet": snippet,
            }
        )


def _dedupe_top_files(
    matches: List[Dict[str, Any]], max_files: int
) -> List[Dict[str, Any]]:
    """Pick top K files by first occurrence, de-duplicated."""
    seen: set = set()
    result: List[Dict[str, Any]] = []
    for m in matches:
        path = m.get("path", "")
        if path and path not in seen:
            seen.add(path)
            result.append(m)
        if len(result) >= max_files:
            break
    return result


def _parse_diff_files(diff_text: str) -> List[Dict[str, Any]]:
    """Extract changed file paths and first hunk line from unified diff."""
    import re

    files: List[Dict[str, Any]] = []
    current_path = ""

    for line in diff_text.splitlines():
        # +++ b/path/to/file.ext
        if line.startswith("+++ b/"):
            current_path = line[6:]
        # @@ -old,count +new,count @@
        elif line.startswith("@@") and current_path:
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 1
            files.append({"path": current_path, "line": line_no})
            current_path = ""  # only first hunk per file

    return files


def _suggest_commands(
    repo: str,
    query: str,
    matches: List[Dict[str, Any]],
    mode: str = "default",
) -> List[str]:
    """Generate suggested follow-up commands based on mode."""
    cmds: List[str] = []

    if mode == "default":
        if matches:
            first = matches[0].get("path", "")
            if first:
                cmds.append(f"cts slice --repo {repo} {first}:1-100")
        cmds.append(f"cts search {query!r} --repo {repo} --max 100 --format claude")

    elif mode == "error":
        cmds.append(f"cts symbol <ErrorClass> --repo {repo}")
        if matches:
            first = matches[0].get("path", "")
            if first:
                cmds.append(f"cts slice --repo {repo} {first}:1-200")

    elif mode == "symbol":
        cmds.append(
            f"cts search {query!r} --repo {repo} --format claude --bundle default"
        )

    elif mode == "change":
        cmds.append(f"cts search <changed_symbol> --repo {repo} --format claude")
        cmds.append(f"cts job test --repo {repo}")

    return cmds
