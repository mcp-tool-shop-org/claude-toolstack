"""cts CLI — Claude Toolstack command-line client.

Usage:
  cts status
  cts search <query> --repo org/repo [--glob ...] [--max N]
  cts search <query> --repo org/repo --format claude --bundle error
  cts search <query> --repo org/repo --format sidecar --emit bundle.json
  cts slice --repo org/repo <path>:<start>-<end>
  cts symbol <sym> --repo org/repo --format claude --bundle symbol
  cts index ctags --repo org/repo
  cts job (test|build|lint) --repo org/repo [--preset ...]
  cts sidecar validate artifact.json
  cts sidecar summarize artifact.json [--format markdown]
  cts sidecar secrets-scan artifact.json [--fail]
  cts corpus ingest <dir> --out corpus.jsonl
  cts corpus report corpus.jsonl --format markdown --out report.md

Output modes: --json | --text (default) | --claude | --sidecar
Bundle modes: default | error | symbol | change  (requires --format claude|sidecar)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from cts import __version__
from cts import bundle as bundle_mod
from cts import http
from cts import render
from cts import schema as schema_mod


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=["json", "text", "claude", "sidecar"],
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
    parser.add_argument(
        "--debug-bundle",
        action="store_true",
        default=False,
        help="Include _debug telemetry in bundle (timings, sizes, score cards)",
    )
    parser.add_argument(
        "--debug-json",
        action="store_true",
        default=False,
        help="Emit raw bundle JSON with _debug instead of rendered text",
    )
    parser.add_argument(
        "--explain-top",
        type=int,
        default=10,
        help="Number of score cards to include in debug (default 10)",
    )
    parser.add_argument(
        "--emit",
        default=None,
        metavar="PATH",
        help="Write sidecar JSON to PATH (atomic write via tmp+rename)",
    )
    parser.add_argument(
        "--autopilot",
        type=int,
        default=0,
        metavar="N",
        help="Enable autopilot with up to N refinement passes (0=off)",
    )
    parser.add_argument(
        "--autopilot-max-seconds",
        type=float,
        default=30.0,
        help="Wall-clock budget for autopilot passes (default 30s)",
    )
    parser.add_argument(
        "--autopilot-max-extra-slices",
        type=int,
        default=5,
        help="Max additional slices per refinement pass (default 5)",
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

    if args.format in ("claude", "sidecar"):
        mode = getattr(args, "bundle", "default")
        prefer = _parse_csv(getattr(args, "prefer_paths", None))
        avoid = _parse_csv(getattr(args, "avoid_paths", None))
        repo_root = getattr(args, "repo_root", None)
        debug = getattr(args, "debug_bundle", False) or getattr(
            args, "debug_json", False
        )
        explain_top = getattr(args, "explain_top", 10)

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
                debug=debug,
                explain_top=explain_top,
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
                debug=debug,
                explain_top=explain_top,
            )

        # Autopilot refinement (if requested)
        autopilot_passes: list | None = None
        autopilot_n = getattr(args, "autopilot", 0)
        if autopilot_n > 0 and mode == "default":
            from cts.autopilot import execute_refinements
            from cts.confidence import bundle_confidence

            # Get score cards for confidence check
            initial_cards = None
            if "_debug" in b:
                initial_cards = b["_debug"].get("score_cards")

            # Only refine if initial confidence is low
            initial_conf = bundle_confidence(b, score_cards=initial_cards)
            if not initial_conf["sufficient"]:
                result = execute_refinements(
                    b,
                    build_fn=bundle_mod.build_default_bundle,
                    build_kwargs={
                        "search_data": data,
                        "repo": args.repo,
                        "request_id": data.get("_request_id"),
                        "max_files": args.evidence_files,
                        "context": args.context,
                        "prefer_paths": prefer,
                        "avoid_paths": avoid,
                        "repo_root": repo_root,
                        "explain_top": explain_top,
                    },
                    max_passes=autopilot_n,
                    max_seconds=getattr(args, "autopilot_max_seconds", 30.0),
                    score_cards=initial_cards,
                )
                b = result["final_bundle"]
                autopilot_passes = result["passes"]

        if args.format == "sidecar" or getattr(args, "emit", None):
            _emit_sidecar(
                b,
                args,
                mode=mode,
                query=args.query,
                inputs={
                    "query": args.query,
                    "max": args.max,
                    "bundle_mode": mode,
                    "evidence_files": args.evidence_files,
                    "context": args.context,
                },
                passes=autopilot_passes,
            )
            if args.format == "sidecar":
                return

        if getattr(args, "debug_json", False):
            render.render_json_with_debug(b)
        else:
            render.render_bundle(b)
        return

    render.render_text_search(data)


def _parse_csv(val: Optional[str]) -> Optional[List[str]]:
    """Split comma-separated value into list, or None."""
    if not val:
        return None
    return [v.strip() for v in val.split(",") if v.strip()]


def _emit_sidecar(
    bundle: dict,
    args: argparse.Namespace,
    *,
    mode: str,
    query: str | None = None,
    inputs: dict | None = None,
    passes: list | None = None,
) -> None:
    """Wrap bundle in sidecar schema, emit to stdout or --emit file."""
    import json
    import os
    import tempfile

    sidecar = schema_mod.wrap_bundle(
        bundle,
        mode=mode,
        request_id=bundle.get("request_id", ""),
        cli_version=__version__,
        repo=getattr(args, "repo", ""),
        query=query,
        inputs=inputs,
        debug=getattr(args, "debug_bundle", False)
        or getattr(args, "debug_json", False),
        passes=passes,
    )

    payload = json.dumps(sidecar, indent=2, default=str)

    emit_path = getattr(args, "emit", None)
    if emit_path:
        # Atomic write: write to tmp in same dir, then rename
        target_dir = os.path.dirname(os.path.abspath(emit_path))
        os.makedirs(target_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=target_dir, prefix=".cts-emit-", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.write("\n")
            os.replace(tmp_path, emit_path)
        except BaseException:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        print(f"Sidecar written to {emit_path}", file=sys.stderr)
    else:
        print(payload)


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

    if args.format in ("claude", "sidecar"):
        # Symbol bundle: defs + call site search
        search_data = None
        bundle_mode = getattr(args, "bundle", "default")
        if bundle_mode == "symbol":
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

        debug = getattr(args, "debug_bundle", False) or getattr(
            args, "debug_json", False
        )
        b = bundle_mod.build_symbol_bundle(
            data,
            search_data=search_data,
            repo=args.repo,
            symbol=args.symbol,
            request_id=data.get("_request_id"),
            max_files=getattr(args, "evidence_files", 5),
            context=getattr(args, "context", 30),
            debug=debug,
        )

        if args.format == "sidecar" or getattr(args, "emit", None):
            _emit_sidecar(
                b,
                args,
                mode=bundle_mode,
                query=args.symbol,
                inputs={
                    "symbol": args.symbol,
                    "bundle_mode": bundle_mode,
                },
            )
            if args.format == "sidecar":
                return

        if getattr(args, "debug_json", False):
            render.render_json_with_debug(b)
        else:
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
# Sidecar subcommands
# ---------------------------------------------------------------------------


def cmd_sidecar(args: argparse.Namespace) -> None:
    from cts import sidecar as sidecar_mod

    action = getattr(args, "sidecar_action", None)
    if not action:
        print("Error: sidecar requires a subcommand", file=sys.stderr)
        raise SystemExit(1)

    if action == "validate":
        _sidecar_validate(args, sidecar_mod)
    elif action == "summarize":
        _sidecar_summarize(args, sidecar_mod)
    elif action == "secrets-scan":
        _sidecar_secrets_scan(args, sidecar_mod)


def _sidecar_validate(args: argparse.Namespace, mod: Any) -> None:
    try:
        data = mod.load(args.file)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        raise SystemExit(1)

    errors = mod.validate_envelope(data)
    warnings = mod.validate_stability_contract(data)

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)

    if errors:
        print(
            f"FAIL: {len(errors)} error(s), {len(warnings)} warning(s)",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"OK: valid sidecar (schema v{data.get('bundle_schema_version')})")


def _sidecar_summarize(args: argparse.Namespace, mod: Any) -> None:
    try:
        data = mod.load(args.file)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        raise SystemExit(1)

    fmt = getattr(args, "summary_format", "text")
    print(mod.summarize(data, format=fmt))


def _sidecar_secrets_scan(args: argparse.Namespace, mod: Any) -> None:
    try:
        data = mod.load(args.file)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        raise SystemExit(1)

    findings = mod.secrets_scan(data)

    if not findings:
        print("OK: no secrets detected")
        return

    report = getattr(args, "report", False)
    if report:
        for f in findings:
            print(
                f"  [{f['type']}] {f['location']}: {f['redacted']}",
                file=sys.stderr,
            )

    print(
        f"WARN: {len(findings)} potential secret(s) found",
        file=sys.stderr,
    )

    if getattr(args, "fail", False):
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Corpus subcommands
# ---------------------------------------------------------------------------


def cmd_corpus(args: argparse.Namespace) -> None:
    action = getattr(args, "corpus_action", None)
    if not action:
        print("Error: corpus requires a subcommand", file=sys.stderr)
        raise SystemExit(1)

    if action == "ingest":
        _corpus_ingest(args)
    elif action == "report":
        # Placeholder for Commit 2
        print("Error: corpus report not yet implemented", file=sys.stderr)
        raise SystemExit(1)


def _corpus_ingest(args: argparse.Namespace) -> None:
    import time

    from cts.corpus import (
        extract_passes,
        extract_record,
        load_artifact,
        scan_dir,
        write_corpus,
        write_passes,
    )

    target_dir: str = args.dir
    out_path: str = args.out
    fail_on_invalid: bool = getattr(args, "fail_on_invalid", False)
    max_files: int = getattr(args, "max_files", 0)
    since_days = getattr(args, "since", None)
    include_passes: bool = getattr(args, "include_passes", False)

    # Compute cutoff timestamp
    cutoff = 0.0
    if since_days is not None:
        cutoff = time.time() - (since_days * 86400)

    # Scan for candidate files
    candidates = scan_dir(target_dir, max_files=max_files)
    if not candidates:
        print(f"No JSON files found in {target_dir}", file=sys.stderr)
        raise SystemExit(1)

    # Load, validate, extract
    records = []
    pass_records = []
    stats = {
        "scanned": 0,
        "ingested": 0,
        "invalid": 0,
        "skipped_date": 0,
        "missing_debug": 0,
    }

    for path in candidates:
        stats["scanned"] += 1
        data, errors = load_artifact(path)

        if errors:
            stats["invalid"] += 1
            if fail_on_invalid:
                for e in errors:
                    print(f"error: {path}: {e}", file=sys.stderr)
                raise SystemExit(1)
            continue

        # Date filter
        if cutoff > 0 and data.get("created_at", 0) < cutoff:
            stats["skipped_date"] += 1
            continue

        record = extract_record(data, source_path=path)
        if "_debug" in record.missing_fields:
            stats["missing_debug"] += 1

        records.append(record)
        stats["ingested"] += 1

        if include_passes:
            pass_records.extend(extract_passes(data))

    # Write output
    written = write_corpus(records, out_path)

    if include_passes and pass_records:
        if out_path.endswith(".jsonl"):
            passes_path = out_path[:-6] + "_passes.jsonl"
        else:
            passes_path = out_path + ".passes"
        write_passes(pass_records, passes_path)
        print(
            f"Passes:        {passes_path} ({len(pass_records)} records)",
            file=sys.stderr,
        )

    # Summary
    print(f"Scanned:       {stats['scanned']}", file=sys.stderr)
    print(f"Ingested:      {stats['ingested']}", file=sys.stderr)
    print(f"Invalid:       {stats['invalid']}", file=sys.stderr)
    if stats["skipped_date"]:
        print(f"Skipped (date): {stats['skipped_date']}", file=sys.stderr)
    print(f"Missing debug: {stats['missing_debug']}", file=sys.stderr)
    print(f"Output:        {out_path} ({written} records)", file=sys.stderr)


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

    # sidecar
    p_sidecar = sub.add_parser("sidecar", help="Sidecar artifact utilities")
    sidecar_sub = p_sidecar.add_subparsers(
        dest="sidecar_action", help="Sidecar subcommands"
    )

    # sidecar validate
    p_sc_validate = sidecar_sub.add_parser("validate", help="Validate sidecar envelope")
    p_sc_validate.add_argument("file", help="Path to sidecar JSON file")

    # sidecar summarize
    p_sc_summarize = sidecar_sub.add_parser(
        "summarize", help="Print human-readable summary"
    )
    p_sc_summarize.add_argument("file", help="Path to sidecar JSON file")
    p_sc_summarize.add_argument(
        "--format",
        dest="summary_format",
        choices=["text", "markdown"],
        default="text",
        help="Summary output format (default: text)",
    )

    # sidecar secrets-scan
    p_sc_secrets = sidecar_sub.add_parser(
        "secrets-scan", help="Scan for potential secrets"
    )
    p_sc_secrets.add_argument("file", help="Path to sidecar JSON file")
    p_sc_secrets.add_argument(
        "--fail",
        action="store_true",
        default=False,
        help="Exit nonzero if secrets detected",
    )
    p_sc_secrets.add_argument(
        "--report",
        action="store_true",
        default=False,
        help="Print findings with redacted previews",
    )

    # corpus
    p_corpus = sub.add_parser("corpus", help="Corpus analytics for sidecar artifacts")
    corpus_sub = p_corpus.add_subparsers(
        dest="corpus_action", help="Corpus subcommands"
    )

    # corpus ingest
    p_ci = corpus_sub.add_parser(
        "ingest", help="Ingest sidecar artifacts into corpus JSONL"
    )
    p_ci.add_argument("dir", help="Directory containing sidecar artifacts")
    p_ci.add_argument(
        "--out",
        default="corpus.jsonl",
        help="Output JSONL path (default: corpus.jsonl)",
    )
    p_ci.add_argument(
        "--fail-on-invalid",
        action="store_true",
        default=False,
        help="Exit nonzero on first invalid artifact (default: skip)",
    )
    p_ci.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Max files to scan (0 = unlimited)",
    )
    p_ci.add_argument(
        "--since",
        type=float,
        default=None,
        metavar="DAYS",
        help="Only ingest artifacts created within N days",
    )
    p_ci.add_argument(
        "--include-passes",
        action="store_true",
        default=False,
        help="Also write pass-level JSONL (corpus_passes.jsonl)",
    )

    # corpus report (placeholder for Commit 2)
    corpus_sub.add_parser("report", help="Generate analytics report from corpus JSONL")

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
        "sidecar": cmd_sidecar,
        "corpus": cmd_corpus,
    }
    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()
        raise SystemExit(1)
