"""Evidence bundle orchestrator — v2 structured bundles.

Modes:
  default  — search + ranked matches + context slices
  error    — stack-trace-aware: extract files from trace, boost in ranking
  symbol   — definition + call site bundles
  change   — git diff + hunk context slices

When debug=True, bundles include a _debug key with:
  - timings_ms: per-step timing breakdown
  - sections: per-section byte/line counts
  - score_cards: ranking signal breakdown (top N)
  - limits: parameters used for this bundle
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from cts import http
from cts.ctags import kind_weight, normalize_kind
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
# Telemetry helpers
# ---------------------------------------------------------------------------


class _Timer:
    """Lightweight step timer for debug telemetry."""

    def __init__(self) -> None:
        self._steps: List[Tuple[str, float]] = []
        self._start = time.monotonic()
        self._lap = self._start

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self._steps.append((name, (now - self._lap) * 1000))
        self._lap = now

    def to_dict(self) -> Dict[str, float]:
        total = (time.monotonic() - self._start) * 1000
        d = {name: round(ms, 2) for name, ms in self._steps}
        d["total"] = round(total, 2)
        return d


def _section_size(data: Any) -> Dict[str, int]:
    """Estimate byte and line counts for a section."""
    text = json.dumps(data, ensure_ascii=False) if data else ""
    return {
        "bytes": len(text.encode("utf-8")),
        "lines": text.count("\n") + (1 if text else 0),
        "items": len(data) if isinstance(data, list) else (1 if data else 0),
    }


def _compute_debug(
    bundle: Dict[str, Any],
    timer: _Timer,
    score_cards: Optional[List[Dict[str, Any]]] = None,
    explain_top: int = 10,
    limits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the _debug telemetry object."""
    sections = {}
    for key in ("ranked_sources", "matches", "slices", "symbols", "diff"):
        val = bundle.get(key)
        if val:
            sections[key] = _section_size(val)

    # Bundle-level sizing
    full_text = json.dumps(bundle, ensure_ascii=False, default=str)
    bundle_bytes = len(full_text.encode("utf-8"))
    bundle_lines = full_text.count("\n") + 1

    debug: Dict[str, Any] = {
        "timings_ms": timer.to_dict(),
        "sections": sections,
        "bundle_bytes": bundle_bytes,
        "bundle_lines": bundle_lines,
    }

    if score_cards:
        debug["score_cards"] = score_cards[:explain_top]

    if limits:
        debug["limits"] = limits

    return debug


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
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_symbol(query: str) -> bool:
    """Heuristic: is this query a single identifier (plausible symbol name)?"""
    import re

    return bool(query and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", query))


# ---------------------------------------------------------------------------
# Ctags enrichment helper
# ---------------------------------------------------------------------------


def _build_ctags_info(
    defs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build ctags_info dict from symbol definitions for ranking.

    Returns dict with def_files, kind_weight, and best_kind.
    """
    def_files = {d.get("file", "") for d in defs if d.get("file")}
    best_w = 0.0
    best_kind = ""
    for d in defs:
        raw = d.get("kind") or ""
        if not raw:
            continue
        name = normalize_kind(raw)
        w = kind_weight(name)
        if w > best_w:
            best_w = w
            best_kind = name
    return {
        "def_files": def_files,
        "kind_weight": best_w,
        "best_kind": best_kind,
    }


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
    debug: bool = False,
    explain_top: int = 10,
    ctags_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a default evidence bundle from search results."""
    timer = _Timer()
    rid = search_data.get("_request_id", request_id or "")
    query = search_data.get("query", "")
    matches = search_data.get("matches", [])

    bundle = _empty_bundle("default", repo, rid, query)
    bundle["truncated"] = search_data.get("truncated", False)

    # Structural heuristic: pass query as symbol if it looks like one
    q_sym = query if _looks_like_symbol(query) else None

    # Rank matches
    score_cards = None
    if debug:
        ranked, score_cards = rank_matches(
            matches,
            prefer_paths=prefer_paths,
            avoid_paths=avoid_paths,
            repo_root=repo_root,
            explain=True,
            ctags_info=ctags_info,
            query_symbol=q_sym,
        )
    else:
        ranked = rank_matches(
            matches,
            prefer_paths=prefer_paths,
            avoid_paths=avoid_paths,
            repo_root=repo_root,
            ctags_info=ctags_info,
            query_symbol=q_sym,
        )
    timer.lap("ranking")

    # Ranked sources + trimmed snippets
    _populate_ranked_and_matches(bundle, ranked)

    # Top K file slices
    top_files = _dedupe_top_files(ranked, max_files)
    bundle["slices"] = fetch_slices(top_files, repo, request_id=rid, context=context)
    timer.lap("slice_fetch")

    # Suggested next commands
    bundle["suggested_commands"] = _suggest_commands(
        repo, query, matches, mode="default"
    )

    if debug:
        bundle["_debug"] = _compute_debug(
            bundle,
            timer,
            score_cards=score_cards,
            explain_top=explain_top,
            limits={
                "max_files": max_files,
                "context": context,
                "mode": "default",
            },
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
    debug: bool = False,
    explain_top: int = 10,
    ctags_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an error-aware evidence bundle.

    If error_text contains a stack trace, extract file references
    and boost them in ranking. Falls back to default otherwise.
    """
    timer = _Timer()
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
    timer.lap("trace_extract")

    # Structural heuristic: pass query as symbol if it looks like one
    q_sym = query if _looks_like_symbol(query) else None

    # Rank with trace boost
    score_cards = None
    if debug:
        ranked, score_cards = rank_matches(
            matches,
            trace_files=trace_files,
            prefer_paths=prefer_paths,
            avoid_paths=avoid_paths,
            repo_root=repo_root,
            explain=True,
            ctags_info=ctags_info,
            query_symbol=q_sym,
        )
    else:
        ranked = rank_matches(
            matches,
            trace_files=trace_files,
            prefer_paths=prefer_paths,
            avoid_paths=avoid_paths,
            repo_root=repo_root,
            ctags_info=ctags_info,
            query_symbol=q_sym,
        )
    timer.lap("ranking")

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
    timer.lap("slice_fetch")

    bundle["suggested_commands"] = _suggest_commands(repo, query, matches, mode="error")

    if debug:
        bundle["_debug"] = _compute_debug(
            bundle,
            timer,
            score_cards=score_cards,
            explain_top=explain_top,
            limits={
                "max_files": max_files,
                "context": context,
                "mode": "error",
                "trace_files_found": len(trace_files),
            },
        )

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
    debug: bool = False,
) -> Dict[str, Any]:
    """Build a symbol evidence bundle.

    Combines symbol definitions with search results for call sites.
    """
    timer = _Timer()
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
    timer.lap("symbol_parse")

    # Slices around definitions
    def_files = [{"path": d.get("file", ""), "line": 1} for d in defs if d.get("file")]
    bundle["slices"] = fetch_slices(
        def_files[:max_files], repo, request_id=rid, context=context
    )
    timer.lap("slice_fetch")

    # Build ctags_info from symbol defs for ranking call sites
    ci = _build_ctags_info(defs) if defs else None

    # Call sites from search (if available) — ranked with ctags + heuristics
    score_cards = None
    if search_data:
        call_matches = search_data.get("matches", [])
        if debug:
            ranked_calls, score_cards = rank_matches(
                call_matches, ctags_info=ci, explain=True, query_symbol=symbol
            )
        else:
            ranked_calls = rank_matches(
                call_matches, ctags_info=ci, query_symbol=symbol
            )
        timer.lap("call_site_ranking")

        for m in ranked_calls:
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

    if debug:
        limits: Dict[str, Any] = {
            "max_files": max_files,
            "context": context,
            "mode": "symbol",
            "definitions_found": len(defs),
        }
        if ci:
            limits["ctags_best_kind"] = ci.get("best_kind", "")
            limits["ctags_def_files"] = len(ci.get("def_files", set()))
        bundle["_debug"] = _compute_debug(
            bundle,
            timer,
            score_cards=score_cards,
            limits=limits,
        )

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
    debug: bool = False,
) -> Dict[str, Any]:
    """Build a change evidence bundle from git diff output.

    Parses diff hunks and fetches surrounding context.
    """
    timer = _Timer()
    bundle = _empty_bundle("change", repo, request_id or "", "")
    bundle["diff"] = diff_text

    # Parse changed files from diff headers
    changed_files = _parse_diff_files(diff_text)
    bundle["notes"].append(f"{len(changed_files)} file(s) changed")
    timer.lap("diff_parse")

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
    timer.lap("slice_fetch")

    bundle["suggested_commands"] = _suggest_commands(repo, "", [], mode="change")

    if debug:
        bundle["_debug"] = _compute_debug(
            bundle,
            timer,
            limits={
                "max_files": max_files,
                "context": context,
                "mode": "change",
                "files_changed": len(changed_files),
            },
        )

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
