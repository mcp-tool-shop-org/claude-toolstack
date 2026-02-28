"""cts CLI — Claude Toolstack command-line client.

Usage:
  cts status
  cts search <query> --repo org/repo [--glob ...] [--max N]
  cts search <query> --repo org/repo --format claude --bundle error
  cts slice --repo org/repo <path>:<start>-<end>
  cts symbol <sym> --repo org/repo --format claude --bundle symbol
  cts index ctags --repo org/repo
  cts job (test|build|lint) --repo org/repo [--preset ...]

Output modes: --json | --text (default) | --claude
Bundle modes: default | error | symbol | change  (requires --format claude)
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from cts import __version__
from cts import bundle as bundle_mod
from cts import http
from cts import render


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=["json", "text", "claude"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--request-id",
        default=None,
        help="Override request ID (default: auto-generated UUID)",
    )


def _add_repo_arg(parser: argparse.ArgumentParser, required: bool = True) -> None:
    parser.add_argument(
        "--repo",
        required=required,
        help="Repository identifier (org/repo)",
    )


def _add_bundle_args(parser: argparse.ArgumentParser) -> None:
    """Add --bundle and related evidence flags to a subparser."""
    parser.add_argument(
        "--bundle",
        choices=["default", "error", "symbol", "change"],
        default="default",
        help="Bundle mode for --format claude (default: default)",
    )
    parser.add_argument(
        "--evidence-files",
        type=int,
        default=5,
        help="Files to include slices for in --claude mode (default 5)",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=30,
        help="Lines of context around matches in --claude mode (default 30)",
    )
    parser.add_argument(
        "--error-text",
        default="",
        help="Error/stack trace text for --bundle error mode",
    )
    parser.add_argument(
        "--prefer-paths",
        default=None,
        help="Comma-separated path segments to boost (e.g. src,core)",
    )
    parser.add_argument(
        "--avoid-paths",
        default=None,
        help="Comma-separated path segments to demote (e.g. vendor,test)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Local repo root for git recency scoring (optional)",
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    data = http.get("/v1/status", request_id=args.request_id)
    if args.format == "json":
        render.render_json(data)
    else:
        render.render_text_status(data)


def cmd_search(args: argparse.Namespace) -> None:
    body = {
        "repo": args.repo,
        "query": args.query,
        "max_matches": args.max,
    }
    if args.glob:
        body["path_globs"] = args.glob

    data = http.post("/v1/search/rg", body, request_id=args.request_id)

    if args.format == "json":
        render.render_json(data)
        return

    if args.format == "claude":
        mode = getattr(args, "bundle", "default")
        prefer = _parse_csv(getattr(args, "prefer_paths", None))
        avoid = _parse_csv(getattr(args, "avoid_paths", None))
        repo_root = getattr(args, "repo_root", None)

        if mode == "error":
            error_text = getattr(args, "error_text", "") or ""
            b = bundle_mod.build_error_bundle(
                data,
                repo=args.repo,
                error_text=error_text,
                request_id=data.get("_request_id"),
                max_files=args.evidence_files,
                context=args.context,
                prefer_paths=prefer,
                avoid_paths=avoid,
                repo_root=repo_root,
            )
        else:
            b = bundle_mod.build_default_bundle(
                data,
                repo=args.repo,
                request_id=data.get("_request_id"),
                max_files=args.evidence_files,
                context=args.context,
                prefer_paths=prefer,
                avoid_paths=avoid,
                repo_root=repo_root,
            )
        render.render_bundle(b)
        return

    render.render_text_search(data)


def _parse_csv(val: Optional[str]) -> Optional[List[str]]:
    """Split comma-separated value into list, or None."""
    if not val:
        return None
    return [v.strip() for v in val.split(",") if v.strip()]


def cmd_slice(args: argparse.Namespace) -> None:
    # Parse path:start-end
    spec = args.spec
    if ":" not in spec:
        print("Error: slice spec must be path:start-end", file=sys.stderr)
        raise SystemExit(1)

    path, _, range_part = spec.partition(":")
    if "-" not in range_part:
        print("Error: range must be start-end (e.g. 120-180)", file=sys.stderr)
        raise SystemExit(1)

    start_s, _, end_s = range_part.partition("-")
    try:
        start, end = int(start_s), int(end_s)
    except ValueError:
        print("Error: start and end must be integers", file=sys.stderr)
        raise SystemExit(1)

    body = {"repo": args.repo, "path": path, "start": start, "end": end}
    data = http.post("/v1/file/slice", body, request_id=args.request_id)

    if args.format == "json":
        render.render_json(data)
    else:
        render.render_text_slice(data)


def cmd_symbol(args: argparse.Namespace) -> None:
    body = {"repo": args.repo, "symbol": args.symbol}
    data = http.post("/v1/symbol/ctags", body, request_id=args.request_id)

    if args.format == "json":
        render.render_json(data)
        return

    if args.format == "claude":
        # Symbol bundle: defs + call site search
        search_data = None
        if getattr(args, "bundle", "default") == "symbol":
            try:
                search_data = http.post(
                    "/v1/search/rg",
                    {
                        "repo": args.repo,
                        "query": args.symbol,
                        "max_matches": 30,
                    },
                    request_id=args.request_id,
                )
            except SystemExit:
                pass  # search is optional enrichment

        b = bundle_mod.build_symbol_bundle(
            data,
            search_data=search_data,
            repo=args.repo,
            symbol=args.symbol,
            request_id=data.get("_request_id"),
            max_files=getattr(args, "evidence_files", 5),
            context=getattr(args, "context", 30),
        )
        render.render_bundle(b)
        return

    render.render_text_symbol(data)


def cmd_index(args: argparse.Namespace) -> None:
    if args.type != "ctags":
        print(f"Error: unknown index type: {args.type}", file=sys.stderr)
        raise SystemExit(1)

    body = {"repo": args.repo}
    data = http.post("/v1/index/ctags", body, request_id=args.request_id)

    if args.format == "json":
        render.render_json(data)
    else:
        render.render_text_index(data)


def cmd_job(args: argparse.Namespace) -> None:
    body: dict = {"repo": args.repo, "job": args.job_type}
    if args.preset:
        body["preset"] = args.preset

    data = http.post("/v1/run/job", body, request_id=args.request_id)

    if args.format == "json":
        render.render_json(data)
    elif args.format == "claude":
        render.render_claude_job(data)
    else:
        render.render_text_job(data)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cts",
        description="Claude Toolstack CLI — bounded code intelligence client",
    )
    parser.add_argument("--version", action="version", version=f"cts {__version__}")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # status
    p_status = sub.add_parser("status", help="Gateway health + config")
    _add_common_args(p_status)

    # search
    p_search = sub.add_parser("search", help="Ripgrep search with guardrails")
    p_search.add_argument("query", help="Search pattern")
    _add_repo_arg(p_search)
    p_search.add_argument("--glob", action="append", help="Path glob filters")
    p_search.add_argument(
        "--max", type=int, default=50, help="Max matches (default 50)"
    )
    _add_bundle_args(p_search)
    _add_common_args(p_search)

    # slice
    p_slice = sub.add_parser("slice", help="Fetch file range")
    p_slice.add_argument("spec", help="File range: path:start-end")
    _add_repo_arg(p_slice)
    _add_common_args(p_slice)

    # symbol
    p_symbol = sub.add_parser("symbol", help="Query symbol definitions")
    p_symbol.add_argument("symbol", help="Symbol name to look up")
    _add_repo_arg(p_symbol)
    _add_bundle_args(p_symbol)
    _add_common_args(p_symbol)

    # index
    p_index = sub.add_parser("index", help="Build index (ctags)")
    p_index.add_argument("type", choices=["ctags"], help="Index type")
    _add_repo_arg(p_index)
    _add_common_args(p_index)

    # job
    p_job = sub.add_parser("job", help="Run test/build/lint job")
    p_job.add_argument("job_type", choices=["test", "build", "lint"], help="Job type")
    _add_repo_arg(p_job)
    p_job.add_argument(
        "--preset",
        default="",
        help="Preset (node, python, rust, etc). Falls back to repos.yaml default.",
    )
    _add_common_args(p_job)

    return parser


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        raise SystemExit(0)

    commands = {
        "status": cmd_status,
        "search": cmd_search,
        "slice": cmd_slice,
        "symbol": cmd_symbol,
        "index": cmd_index,
        "job": cmd_job,
    }
    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()
        raise SystemExit(1)
