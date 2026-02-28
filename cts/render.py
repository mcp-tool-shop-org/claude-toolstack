"""Output renderers: --json, --text, --claude (v1 + v2 bundles)."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List


def _strip_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove internal _fields from output."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


def render_json(data: Dict[str, Any]) -> None:
    print(json.dumps(_strip_meta(data), indent=2))


def render_text_status(data: Dict[str, Any]) -> None:
    rid = data.get("_request_id", "")
    print(f"Request-ID: {rid}")
    print(f"Gateway v{data.get('version', '?')}  ok={data.get('ok')}")
    print(f"  Repo root:        {data.get('repo_root')}")
    print(f"  Cache root:       {data.get('cache_root')}")
    print(f"  RG threads:       {data.get('rg_threads')}")
    print(f"  RG concurrency:   {data.get('rg_concurrency')}")
    print(f"  Job concurrency:  {data.get('job_concurrency')}")
    print(f"  Max response:     {data.get('max_response_bytes')} bytes")
    print(f"  Timeout:          {data.get('timeout_sec')}s")
    print(f"  Docker:           {data.get('docker_host')}")
    print(f"  Containers:       {', '.join(data.get('allowed_containers', []))}")
    print(f"  Allowed repos:    {', '.join(data.get('allowed_repos', []))}")


def render_text_search(data: Dict[str, Any]) -> None:
    rid = data.get("_request_id", "")
    matches = data.get("matches", [])
    print(f"Request-ID: {rid}")
    repo = data.get("repo")
    cnt = data.get("count")
    print(f"Search: {data.get('query')!r} in {repo}  ({cnt} matches)")
    if data.get("truncated"):
        print("  [truncated — output exceeded 512 KB]")
    print()
    for m in matches:
        path = m.get("path", "?")
        line = m.get("line", "?")
        snippet = m.get("snippet", "").rstrip()
        print(f"  {path}:{line}  {snippet}")


def render_text_slice(data: Dict[str, Any]) -> None:
    rid = data.get("_request_id", "")
    print(f"Request-ID: {rid}")
    path = data.get("path", "?")
    start = data.get("start", "?")
    print(f"File: {data.get('repo')}/{path}  (from line {start})")
    if data.get("truncated"):
        print("  [truncated]")
    print()
    for i, line in enumerate(data.get("lines", []), start=int(start)):
        print(f"  {i:>6}  {line}")


def render_text_symbol(data: Dict[str, Any]) -> None:
    rid = data.get("_request_id", "")
    defs = data.get("defs", [])
    print(f"Request-ID: {rid}")
    repo = data.get("repo")
    cnt = data.get("count")
    print(f"Symbol: {data.get('symbol')!r} in {repo}  ({cnt} defs)")
    print()
    for d in defs:
        kind = d.get("kind", "?")
        name = d.get("name", "?")
        fpath = d.get("file", "?")
        print(f"  [{kind}] {name}  {fpath}")


def render_text_job(data: Dict[str, Any]) -> None:
    rid = data.get("_request_id", "")
    ok = data.get("ok", False)
    tag = "PASS" if ok else "FAIL"
    print(f"Request-ID: {rid}")
    print(
        f"Job: {data.get('job')} ({data.get('preset')}) on {data.get('repo')}  "
        f"[{tag}]  exit={data.get('exit_code')}  {data.get('duration_sec')}s"
    )
    if data.get("truncated"):
        print("  [output truncated]")
    stdout = data.get("stdout", "").rstrip()
    stderr = data.get("stderr", "").rstrip()
    if stdout:
        print("\n--- stdout ---")
        print(stdout)
    if stderr:
        print("\n--- stderr ---")
        print(stderr)


def render_text_index(data: Dict[str, Any]) -> None:
    rid = data.get("_request_id", "")
    ok = data.get("ok", False)
    tag = "OK" if ok else "FAIL"
    print(f"Request-ID: {rid}")
    print(f"Index ctags: {data.get('repo')}  [{tag}]  {data.get('duration_sec')}s")
    stderr = data.get("stderr", "").rstrip()
    if stderr:
        print(f"  stderr: {stderr[:200]}")


# ---------------------------------------------------------------------------
# Claude evidence bundle renderers
# ---------------------------------------------------------------------------


def render_claude_search(
    data: Dict[str, Any], slices: List[Dict[str, Any]] | None = None
) -> None:
    """Render a compact evidence bundle for Claude."""
    rid = data.get("_request_id", "")
    repo = data.get("repo", "?")
    query = data.get("query", "?")
    matches = data.get("matches", [])

    lines: List[str] = []
    lines.append("## Evidence Bundle")
    lines.append(f"repo: {repo}  query: {query!r}  request_id: {rid}")
    lines.append(f"matches: {data.get('count', 0)}")
    if data.get("truncated"):
        lines.append("[search results truncated at 512 KB]")
    lines.append("")

    # Match summary
    lines.append("### Matches")
    for m in matches:
        path = m.get("path", "?")
        line_no = m.get("line", "?")
        snippet = m.get("snippet", "").rstrip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        lines.append(f"  {path}:{line_no}  {snippet}")
    lines.append("")

    # Inline slices (if provided)
    if slices:
        lines.append("### Context Slices")
        for s in slices:
            spath = s.get("path", "?")
            sstart = s.get("start", 0)
            lines.append(f"--- {s.get('repo', repo)}/{spath} (from line {sstart}) ---")
            for i, sl in enumerate(s.get("lines", []), start=int(sstart)):
                lines.append(f"{i:>6}  {sl}")
            lines.append("")

    lines.append(
        "If you need more: request wider slices, more matches, or specific files."
    )
    print("\n".join(lines))


def render_claude_job(data: Dict[str, Any]) -> None:
    """Render job result as a compact evidence bundle."""
    rid = data.get("_request_id", "")
    ok = data.get("ok", False)
    tag = "PASS" if ok else "FAIL"

    lines: List[str] = []
    lines.append("## Job Result")
    repo = data.get("repo")
    job = data.get("job")
    preset = data.get("preset")
    ec = data.get("exit_code")
    dur = data.get("duration_sec")
    lines.append(
        f"repo: {repo}  job: {job}  preset: {preset}  "
        f"result: {tag}  exit: {ec}  "
        f"duration: {dur}s  request_id: {rid}"
    )

    stdout = data.get("stdout", "").rstrip()
    stderr = data.get("stderr", "").rstrip()

    # Trim to last N lines for Claude
    max_lines = 80
    if stdout:
        stdout_lines = stdout.splitlines()
        if len(stdout_lines) > max_lines:
            lines.append(
                f"\n### stdout (last {max_lines} of {len(stdout_lines)} lines)"
            )
            lines.extend(stdout_lines[-max_lines:])
        else:
            lines.append("\n### stdout")
            lines.append(stdout)

    if stderr:
        stderr_lines = stderr.splitlines()
        if len(stderr_lines) > max_lines:
            lines.append(
                f"\n### stderr (last {max_lines} of {len(stderr_lines)} lines)"
            )
            lines.extend(stderr_lines[-max_lines:])
        else:
            lines.append("\n### stderr")
            lines.append(stderr)

    if data.get("truncated"):
        lines.append("[output truncated at 512 KB]")

    print("\n".join(lines))


# ---------------------------------------------------------------------------
# v2 structured bundle renderer
# ---------------------------------------------------------------------------


def render_bundle(bundle: Dict[str, Any]) -> None:
    """Render a v2 structured evidence bundle."""
    lines: List[str] = []

    mode = bundle.get("mode", "default")
    repo = bundle.get("repo", "?")
    rid = bundle.get("request_id", "")
    query = bundle.get("query", "")
    ts = bundle.get("timestamp", 0)
    ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    # Header
    lines.append("# Evidence Bundle")
    lines.append("")

    # Metadata
    lines.append("## Metadata")
    lines.append(f"  repo: {repo}")
    lines.append(f"  mode: {mode}")
    lines.append(f"  request_id: {rid}")
    lines.append(f"  timestamp: {ts_str}")
    if bundle.get("truncated"):
        lines.append("  [search results truncated at 512 KB]")
    lines.append("")

    # Query
    if query:
        lines.append("## Query")
        lines.append(f"  {query}")
        lines.append("")

    # Ranked evidence sources
    sources = bundle.get("ranked_sources", [])
    if sources:
        lines.append(f"## Ranked Evidence Sources ({len(sources)})")
        for s in sources:
            path = s.get("path", "?")
            line = s.get("line", 0)
            score = s.get("score", 0.0)
            extra = ""
            if s.get("in_trace"):
                extra = "  [trace]"
            lines.append(f"  {score:+.2f}  {path}:{line}{extra}")
        lines.append("")

    # Top matches
    matches = bundle.get("matches", [])
    if matches:
        lines.append(f"## Top Matches ({len(matches)})")
        for m in matches:
            path = m.get("path", "?")
            line = m.get("line", 0)
            snippet = m.get("snippet", "")
            lines.append(f"  {path}:{line}  {snippet}")
        lines.append("")

    # File slices
    slices = bundle.get("slices", [])
    if slices:
        lines.append(f"## File Slices ({len(slices)})")
        for s in slices:
            spath = s.get("path", "?")
            srepo = s.get("repo", repo)
            sstart = s.get("start", 0)
            lines.append(f"--- {srepo}/{spath} (from line {sstart}) ---")
            for i, sl in enumerate(s.get("lines", []), start=int(sstart)):
                lines.append(f"{i:>6}  {sl}")
            lines.append("")

    # Symbols (symbol mode)
    symbols = bundle.get("symbols", [])
    if symbols:
        lines.append(f"## Symbols ({len(symbols)})")
        for sym in symbols:
            kind = sym.get("kind", "?")
            name = sym.get("name", "?")
            fpath = sym.get("file", "?")
            lines.append(f"  [{kind}] {name}  {fpath}")
        lines.append("")

    # Diff (change mode)
    diff = bundle.get("diff", "")
    if diff:
        lines.append("## Diff")
        # Trim to reasonable size
        diff_lines = diff.splitlines()
        if len(diff_lines) > 200:
            lines.append(f"(showing last 200 of {len(diff_lines)} lines)")
            lines.extend(diff_lines[-200:])
        else:
            lines.extend(diff_lines)
        lines.append("")

    # Suggested next commands
    cmds = bundle.get("suggested_commands", [])
    if cmds:
        lines.append("## Suggested Next Commands")
        for c in cmds:
            lines.append(f"  {c}")
        lines.append("")

    # Notes
    notes = bundle.get("notes", [])
    if notes:
        lines.append("## Notes")
        for n in notes:
            lines.append(f"  {n}")
        lines.append("")

    print("\n".join(lines))
