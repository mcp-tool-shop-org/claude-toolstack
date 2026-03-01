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
  cts corpus patch tuning.json --repos-yaml repos.yaml --format diff
  cts corpus apply tuning.json --repos-yaml repos.yaml [--dry-run]
  cts corpus rollback rollback.json
  cts corpus evaluate before.jsonl after.jsonl --format markdown
  cts corpus baseline corpus.jsonl --out baseline.json --label lexical-only
  cts corpus experiment init --id EXP1 --description "..." --out experiment.json
  cts corpus experiment propose --corpus tuning.json --repos-yaml repos.yaml
  cts corpus experiment list --root experiments/ --format text
  cts corpus experiment show EXP123 --root experiments/
  cts corpus experiment archive --experiment exp.json --result result.json
  cts corpus experiment evaluate --corpus corpus.jsonl --experiment exp.json
  cts corpus experiment trend --root experiments/ --format markdown
  cts semantic index --repo org/repo --root ./repo [--max-files N]
  cts semantic search "what does auth do?" --repo org/repo
  cts semantic status --repo org/repo [--db PATH]
  cts doctor
  cts perf [--format json]

Output modes: --json | --text (default) | --claude | --sidecar
Bundle modes: default | error | symbol | change  (requires --format claude|sidecar)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from cts import __version__
from cts import bundle as bundle_mod
from cts import http
from cts import render
from cts.errors import handle_cli_error
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


def cmd_doctor(args: argparse.Namespace) -> None:
    """Run diagnostic checks and report stack health."""
    import shutil
    import subprocess

    fmt = getattr(args, "format", "text")
    checks: list = []

    # --- Check 1: Repo root ---
    repo_yaml = os.path.exists("repos.yaml")
    git_dir = os.path.exists(".git")
    if repo_yaml and git_dir:
        checks.append(("Repo root", "PASS", "repos.yaml and .git found"))
    elif repo_yaml:
        checks.append(("Repo root", "PASS", "repos.yaml found (no .git)"))
    elif git_dir:
        checks.append(("Repo root", "WARN", ".git found but repos.yaml missing"))
    else:
        checks.append(
            (
                "Repo root",
                "FAIL",
                "No repos.yaml found — run from project root",
            )
        )

    # --- Check 2: Ripgrep ---
    rg_path = shutil.which("rg")
    if rg_path:
        try:
            ver = subprocess.run(
                ["rg", "--version"], capture_output=True, text=True, timeout=5
            )
            version_line = (
                ver.stdout.strip().split("\n")[0] if ver.stdout else "unknown"
            )
            checks.append(("Ripgrep", "PASS", f"{version_line}"))
        except Exception:
            checks.append(
                (
                    "Ripgrep",
                    "WARN",
                    f"Found at {rg_path}, version check failed",
                )
            )
    else:
        checks.append(("Ripgrep", "FAIL", "rg not found in PATH — install ripgrep"))

    # --- Check 3: ctags (optional) ---
    ctags_path = shutil.which("ctags") or shutil.which("universal-ctags")
    if ctags_path:
        checks.append(("ctags", "PASS", f"Found at {ctags_path}"))
    else:
        checks.append(("ctags", "WARN", "Not found (symbol lookup unavailable)"))

    # --- Check 4: Python deps (semantic) ---
    try:
        import numpy

        checks.append(("numpy", "PASS", f"v{numpy.__version__}"))
    except ImportError:
        checks.append(("numpy", "WARN", "Not installed (needed for semantic search)"))

    try:
        import sentence_transformers

        checks.append(
            (
                "sentence-transformers",
                "PASS",
                f"v{sentence_transformers.__version__}",
            )
        )
    except ImportError:
        checks.append(
            (
                "sentence-transformers",
                "WARN",
                "Not installed (needed for semantic search)",
            )
        )

    # --- Check 5: Gateway ---
    try:
        from cts.config import gateway_url

        gw = gateway_url()
        http.get("/v1/status", timeout=5)
        checks.append(("Gateway", "PASS", f"Reachable at {gw}"))
    except SystemExit:
        checks.append(
            (
                "Gateway",
                "WARN",
                "Not reachable (API key not set or gateway down)",
            )
        )
    except Exception as e:
        checks.append(("Gateway", "WARN", f"Connection error: {e}"))

    # --- Check 6: Semantic stores ---
    cache_dir = "gw-cache"
    store_found = False
    if os.path.isdir(cache_dir):
        for entry in sorted(os.listdir(cache_dir)):
            db_path = os.path.join(cache_dir, entry, "semantic.sqlite3")
            if os.path.exists(db_path):
                store_found = True
                try:
                    from cts.semantic.store import SemanticStore

                    store = SemanticStore(db_path)
                    status = store.get_status()
                    store.close()
                    label = f"Semantic [{entry}]"
                    last = status.get("last_indexed_at", "never")
                    msg = (
                        f"{status['chunks']} chunks, "
                        f"{status['embeddings']} embeddings, "
                        f"model={status.get('model', '?')}, "
                        f"last={last}"
                    )
                    checks.append((label, "PASS", msg))
                except Exception as e:
                    checks.append(
                        (
                            f"Semantic [{entry}]",
                            "WARN",
                            f"Error reading: {e}",
                        )
                    )
    if not store_found:
        checks.append(
            (
                "Semantic stores",
                "WARN",
                "No indexed repos found in gw-cache/",
            )
        )

    # --- Check 7: Docker environment ---
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host:
        checks.append(("Docker host", "PASS", f"DOCKER_HOST={docker_host}"))
    else:
        checks.append(
            (
                "Docker host",
                "WARN",
                "DOCKER_HOST not set (Docker features unavailable)",
            )
        )

    # --- Check 8: Docker proxy reachability ---
    _docker_ok = False
    if docker_host:
        try:
            import urllib.request

            ping_url = docker_host.replace("tcp://", "http://")
            ping_url = ping_url.rstrip("/") + "/_ping"
            req = urllib.request.Request(ping_url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()  # drain response
                if resp.status == 200:
                    checks.append(
                        (
                            "Docker proxy",
                            "PASS",
                            f"Reachable at {docker_host}",
                        )
                    )
                    _docker_ok = True
                else:
                    checks.append(
                        (
                            "Docker proxy",
                            "WARN",
                            f"Unexpected status {resp.status}",
                        )
                    )
        except Exception as e:
            checks.append(
                (
                    "Docker proxy",
                    "WARN",
                    f"Not reachable: {e}",
                )
            )

    # --- Check 9: Expected tool containers ---
    if _docker_ok:
        try:
            import urllib.request

            api_url = docker_host.replace("tcp://", "http://")
            api_url = api_url.rstrip("/") + "/containers/json"
            req = urllib.request.Request(api_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                containers = json.loads(resp.read().decode())
            running = set()
            for c in containers:
                for name in c.get("Names", []):
                    running.add(name.lstrip("/"))

            expected = os.environ.get(
                "ALLOWED_CONTAINERS", "claude-ctags,claude-build"
            ).split(",")
            for cname in expected:
                cname = cname.strip()
                if not cname:
                    continue
                if cname in running:
                    checks.append(
                        (
                            f"Container [{cname}]",
                            "PASS",
                            "Running",
                        )
                    )
                else:
                    checks.append(
                        (
                            f"Container [{cname}]",
                            "WARN",
                            "Not running",
                        )
                    )
        except Exception as e:
            checks.append(
                (
                    "Containers",
                    "WARN",
                    f"Could not list containers: {e}",
                )
            )

    # --- Output ---
    if fmt == "json":
        result = [{"check": c[0], "status": c[1], "detail": c[2]} for c in checks]
        print(json.dumps(result, indent=2))
    else:
        for name, status, detail in checks:
            marker = {"PASS": "+", "WARN": "~", "FAIL": "!"}[status]
            print(f"  [{marker}] {name}: {detail}")

        fails = sum(1 for _, s, _ in checks if s == "FAIL")
        warns = sum(1 for _, s, _ in checks if s == "WARN")
        if fails:
            print(f"\n  {fails} check(s) failed, {warns} warning(s).")
            raise SystemExit(1)
        elif warns:
            print(f"\n  All checks passed, {warns} warning(s).")
        else:
            print("\n  All checks passed.")


def cmd_perf(args: argparse.Namespace) -> None:
    """Display current performance knobs with sources."""
    from cts.semantic import DEFAULTS
    from cts.semantic.config import load_config

    fmt = getattr(args, "format", "text")
    cfg = load_config()

    knobs: list = []

    def _add(
        name: str,
        env_var: str,
        current: object,
        default: object,
        tip: str = "",
    ) -> None:
        source = "env" if os.environ.get(env_var) is not None else "default"
        knobs.append(
            {
                "name": name,
                "env_var": env_var,
                "value": current,
                "default": default,
                "source": source,
                "tip": tip,
            }
        )

    # --- Semantic knobs ---
    sem_env = os.environ.get("CTS_SEMANTIC_ENABLED")
    sem_val = sem_env if sem_env is not None else "auto"
    _add(
        "semantic_enabled",
        "CTS_SEMANTIC_ENABLED",
        sem_val,
        "auto",
        "auto = on when semantic store exists for repo",
    )
    _add(
        "chunk_lines",
        "CTS_SEMANTIC_CHUNK_LINES",
        cfg.chunk_lines,
        DEFAULTS["chunk_lines"],
        "Lines per chunk for indexing",
    )
    _add(
        "overlap_lines",
        "CTS_SEMANTIC_OVERLAP_LINES",
        cfg.overlap_lines,
        DEFAULTS["overlap_lines"],
        "Overlap between chunks",
    )
    _add(
        "topk_chunks",
        "CTS_SEMANTIC_TOPK",
        cfg.topk_chunks,
        DEFAULTS["topk_chunks"],
        "Top-K chunks returned by semantic search",
    )
    _add(
        "max_slices",
        "CTS_SEMANTIC_MAX_SLICES",
        cfg.max_slices,
        DEFAULTS["max_slices"],
        "Max semantic slices added to bundle",
    )
    _add(
        "max_seconds",
        "CTS_SEMANTIC_MAX_SECONDS",
        cfg.max_seconds,
        DEFAULTS["max_seconds"],
        "Time budget for semantic retrieval (seconds)",
    )
    _add(
        "model_name",
        "CTS_SEMANTIC_MODEL",
        cfg.model_name,
        "sentence-transformers/all-MiniLM-L6-v2",
        "Embedding model",
    )
    _add(
        "device",
        "CTS_SEMANTIC_DEVICE",
        cfg.device,
        "auto",
        "Compute device: auto | cpu | cuda",
    )

    # --- Narrowing knobs ---
    _add(
        "candidate_strategy",
        "CTS_SEMANTIC_CANDIDATE_STRATEGY",
        cfg.candidate_strategy,
        "exclude_top_k",
        "Semantic candidate narrowing strategy",
    )
    _add(
        "candidate_exclude_top_k",
        "CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K",
        cfg.candidate_exclude_top_k,
        10,
        "Lexical top-K files to skip for semantic",
    )
    _add(
        "candidate_max_files",
        "CTS_SEMANTIC_CANDIDATE_MAX_FILES",
        cfg.candidate_max_files,
        200,
        "Max candidate files for semantic search",
    )
    _add(
        "candidate_max_chunks",
        "CTS_SEMANTIC_CANDIDATE_MAX_CHUNKS",
        cfg.candidate_max_chunks,
        20000,
        "Max chunks to load for semantic search",
    )

    # --- Confidence + Autopilot ---
    _add(
        "confidence_gate",
        "CTS_SEMANTIC_CONFIDENCE_GATE",
        cfg.confidence_gate,
        DEFAULTS["confidence_gate"],
        "Confidence threshold for autopilot sufficiency",
    )
    _add(
        "max_file_bytes",
        "CTS_SEMANTIC_MAX_FILE_BYTES",
        cfg.max_file_bytes,
        DEFAULTS["max_file_bytes"],
        "Skip files larger than this during indexing",
    )

    # --- Output ---
    if fmt == "json":
        print(json.dumps(knobs, indent=2, default=str))
    else:
        max_name = max(len(k["name"]) for k in knobs)
        max_val = max(len(str(k["value"])) for k in knobs)
        for k in knobs:
            marker = "*" if k["source"] == "env" else " "
            print(
                f"  {marker} {k['name']:<{max_name}}  "
                f"= {str(k['value']):<{max_val}}  "
                f"({k['env_var']})"
            )
            if k["tip"]:
                print(f"    {'':>{max_name}}    {k['tip']}")
        print("\n  * = set via environment variable")


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
                bk: Dict[str, Any] = {
                    "search_data": data,
                    "repo": args.repo,
                    "request_id": data.get("_request_id"),
                    "max_files": args.evidence_files,
                    "context": args.context,
                    "prefer_paths": prefer,
                    "avoid_paths": avoid,
                    "repo_root": repo_root,
                    "explain_top": explain_top,
                }

                # Semantic fallback gate (default-on when store exists)
                sem_flag = os.environ.get("CTS_SEMANTIC_ENABLED", "").lower()
                sem_disabled = sem_flag in ("0", "false")
                if not sem_disabled:
                    sem_db = _default_db_path(args.repo)
                    if os.path.exists(sem_db):
                        bk["semantic_store_path"] = sem_db

                result = execute_refinements(
                    b,
                    build_fn=bundle_mod.build_default_bundle,
                    build_kwargs=bk,
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
        _corpus_report(args)
    elif action == "patch":
        _corpus_patch(args)
    elif action == "apply":
        _corpus_apply(args)
    elif action == "rollback":
        _corpus_rollback(args)
    elif action == "evaluate":
        _corpus_evaluate(args)
    elif action == "experiment":
        _corpus_experiment(args)
    elif action == "baseline":
        _corpus_baseline(args)


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


def _corpus_report(args: argparse.Namespace) -> None:
    from cts.corpus.report import generate_report, load_corpus

    corpus_file: str = args.corpus_file
    fmt: str = getattr(args, "report_format", "markdown")
    out_path: str | None = getattr(args, "out", None)
    mode_filter: str | None = getattr(args, "mode", None)
    repo_filter: str | None = getattr(args, "repo", None)
    action_filter: str | None = getattr(args, "action", None)

    try:
        records = load_corpus(corpus_file)
    except FileNotFoundError:
        print(f"Error: file not found: {corpus_file}", file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSONL — {exc}", file=sys.stderr)
        raise SystemExit(1)

    report = generate_report(
        records,
        format=fmt,
        mode_filter=mode_filter,
        repo_filter=repo_filter,
        action_filter=action_filter,
    )

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report written to {out_path}", file=sys.stderr)
    else:
        print(report)

    # Emit tuning recommendations (optional)
    emit_tuning = getattr(args, "emit_tuning", None)
    if emit_tuning:
        from cts.corpus.report import _aggregate
        from cts.corpus.tuning_schema import generate_tuning

        agg = _aggregate(
            records,
            mode_filter=mode_filter,
            repo_filter=repo_filter,
            action_filter=action_filter,
        )
        filters_used = {}
        if mode_filter:
            filters_used["mode"] = mode_filter
        if repo_filter:
            filters_used["repo"] = repo_filter
        if action_filter:
            filters_used["action"] = action_filter

        envelope = generate_tuning(
            agg,
            source_corpus=corpus_file,
            filters=filters_used,
        )
        payload = json.dumps(envelope.to_dict(), indent=2, default=str)
        with open(emit_tuning, "w", encoding="utf-8") as f:
            f.write(payload)
            f.write("\n")
        n = len(envelope.recommendations)
        print(
            f"Tuning: {emit_tuning} ({n} recommendation(s))",
            file=sys.stderr,
        )


def _corpus_patch(args: argparse.Namespace) -> None:
    from cts.corpus.patch import (
        generate_patch_plan,
        load_repos_yaml,
        load_tuning,
        render_plan_diff,
        render_plan_json,
        render_plan_text,
    )

    tuning_path: str = args.tuning
    repos_yaml_path: str = args.repos_yaml
    fmt: str = getattr(args, "patch_format", "text")
    out_path: str | None = getattr(args, "out", None)

    try:
        tuning = load_tuning(tuning_path)
    except FileNotFoundError:
        print(f"Error: file not found: {tuning_path}", file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        repos_yaml = load_repos_yaml(repos_yaml_path)
    except FileNotFoundError:
        print(
            f"Error: file not found: {repos_yaml_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    items = generate_patch_plan(tuning, repos_yaml)

    if fmt == "json":
        output = render_plan_json(items)
    elif fmt == "diff":
        output = render_plan_diff(repos_yaml, items, yaml_path=repos_yaml_path)
    else:
        output = render_plan_text(items)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        active = len([i for i in items if not i.skipped])
        print(
            f"Patch plan: {out_path} ({active} active patch(es))",
            file=sys.stderr,
        )
    else:
        print(output)


def _corpus_apply(args: argparse.Namespace) -> None:
    from cts.corpus.apply import (
        apply_patch_plan,
        write_rollback,
    )
    from cts.corpus.patch import (
        generate_patch_plan,
        load_repos_yaml,
        load_tuning,
    )

    tuning_path: str = args.tuning
    repos_yaml_path: str = args.repos_yaml
    allow_high_risk: bool = getattr(args, "allow_high_risk", False)
    dry_run: bool = getattr(args, "dry_run", False)
    rollback_path: str | None = getattr(args, "rollback_out", None)

    try:
        tuning = load_tuning(tuning_path)
    except FileNotFoundError:
        print(f"Error: file not found: {tuning_path}", file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        repos_yaml = load_repos_yaml(repos_yaml_path)
    except FileNotFoundError:
        print(
            f"Error: file not found: {repos_yaml_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    items = generate_patch_plan(tuning, repos_yaml)
    result = apply_patch_plan(
        repos_yaml,
        items,
        repos_yaml_path=repos_yaml_path,
        allow_high_risk=allow_high_risk,
        dry_run=dry_run,
    )

    # Report
    if result["blocked"]:
        for reason in result["blocked"]:
            print(f"BLOCKED: {reason}", file=sys.stderr)
        raise SystemExit(1)

    applied = result["applied"]
    skipped = result["skipped"]
    prefix = "[DRY RUN] " if dry_run else ""

    print(
        f"{prefix}Applied:  {len(applied)} patch(es)",
        file=sys.stderr,
    )
    print(
        f"{prefix}Skipped:  {len(skipped)} patch(es)",
        file=sys.stderr,
    )

    if result.get("backup_path"):
        print(
            f"Backup:   {result['backup_path']}",
            file=sys.stderr,
        )

    # Write rollback artifact
    if result.get("rollback") and rollback_path:
        write_rollback(result["rollback"], rollback_path)
        print(
            f"Rollback: {rollback_path}",
            file=sys.stderr,
        )
    elif result.get("rollback") and not rollback_path:
        # Default rollback path
        default_rb = "rollback.json"
        write_rollback(result["rollback"], default_rb)
        print(
            f"Rollback: {default_rb}",
            file=sys.stderr,
        )


def _corpus_rollback(args: argparse.Namespace) -> None:
    from cts.corpus.apply import rollback_from_record

    rollback_path: str = args.rollback_file

    try:
        with open(rollback_path, encoding="utf-8") as f:
            record = json.load(f)
    except FileNotFoundError:
        print(
            f"Error: file not found: {rollback_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)

    success = rollback_from_record(record)
    if success:
        repos_path = record.get("repos_yaml_path", "repos.yaml")
        print(f"Rolled back {repos_path} from backup", file=sys.stderr)
    else:
        print("Error: rollback failed — backup not found", file=sys.stderr)
        raise SystemExit(1)


def _corpus_evaluate(args: argparse.Namespace) -> None:
    from cts.corpus.evaluate import (
        evaluate,
        render_evaluation_json,
        render_evaluation_markdown,
        render_evaluation_text,
    )
    from cts.corpus.report import load_corpus

    before_path: str = args.before
    after_path: str = args.after
    fmt: str = getattr(args, "eval_format", "text")
    out_path: str | None = getattr(args, "out", None)

    try:
        before_records = load_corpus(before_path)
    except FileNotFoundError:
        print(
            f"Error: file not found: {before_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        after_records = load_corpus(after_path)
    except FileNotFoundError:
        print(
            f"Error: file not found: {after_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    result = evaluate(before_records, after_records)

    if fmt == "json":
        output = render_evaluation_json(result)
    elif fmt == "markdown":
        output = render_evaluation_markdown(result)
    else:
        output = render_evaluation_text(result)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        verdict = result["comparison"]["verdict"]
        print(
            f"Evaluation: {out_path} (verdict: {verdict})",
            file=sys.stderr,
        )
    else:
        print(output)


# ---------------------------------------------------------------------------
# Experiment subcommands
# ---------------------------------------------------------------------------


def _corpus_baseline(args: argparse.Namespace) -> None:
    from cts.corpus.baseline import (
        capture_baseline,
        render_baseline_json,
        render_baseline_markdown,
        render_baseline_text,
    )

    corpus_path: str = args.corpus
    label: str = getattr(args, "label", "lexical-only")
    fmt: str = getattr(args, "baseline_format", "json")
    out_path: Optional[str] = getattr(args, "out", None)
    mode_filter: Optional[str] = getattr(args, "mode_filter", None)
    repo_filter: Optional[str] = getattr(args, "repo_filter", None)
    since_days: Optional[float] = getattr(args, "since", None)

    baseline = capture_baseline(
        corpus_path,
        label=label,
        mode_filter=mode_filter,
        repo_filter=repo_filter,
        since_days=since_days,
    )

    if fmt == "json":
        output = render_baseline_json(baseline)
    elif fmt == "markdown":
        output = render_baseline_markdown(baseline)
    else:
        output = render_baseline_text(baseline)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        n = baseline.get("corpus_records", 0)
        lbl = baseline.get("label", "?")
        print(
            f"Baseline: {out_path} ({n} records, label={lbl})",
            file=sys.stderr,
        )
    else:
        print(output)


def _corpus_experiment(args: argparse.Namespace) -> None:
    exp_action = getattr(args, "experiment_action", None)
    if not exp_action:
        print(
            "Error: experiment requires a subcommand",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if exp_action == "init":
        _experiment_init(args)
    elif exp_action == "propose":
        _experiment_propose(args)
    elif exp_action == "evaluate":
        _experiment_evaluate(args)
    elif exp_action == "archive":
        _experiment_archive(args)
    elif exp_action == "list":
        _experiment_list(args)
    elif exp_action == "show":
        _experiment_show(args)
    elif exp_action == "trend":
        _experiment_trend(args)


def _experiment_init(args: argparse.Namespace) -> None:
    from cts.corpus.experiment_schema import create_experiment

    exp_id: str = getattr(args, "exp_id", "")
    description: str = getattr(args, "description", "")
    hypothesis: str = getattr(args, "hypothesis", "")
    variants_str: str = getattr(args, "variants", "A,B")
    primary_kpi: str = getattr(args, "primary_kpi", "confidence_final_mean")
    constraints: list = getattr(args, "constraint", []) or []
    out_path: str = getattr(args, "out", "experiment.json")

    variant_names = [v.strip() for v in variants_str.split(",") if v.strip()]

    envelope = create_experiment(
        id=exp_id,
        description=description,
        hypothesis=hypothesis,
        variant_names=variant_names,
        primary_kpi=primary_kpi,
        constraints=constraints,
    )

    payload = json.dumps(envelope.to_dict(), indent=2, default=str)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(payload)
        f.write("\n")

    n = len(envelope.variants)
    print(
        f"Experiment: {out_path} (id={envelope.id}, {n} variant(s))",
        file=sys.stderr,
    )


def _experiment_propose(args: argparse.Namespace) -> None:
    from cts.corpus.experiment_schema import create_experiment
    from cts.corpus.patch import load_repos_yaml, load_tuning
    from cts.corpus.variants import propose_experiment

    corpus_path: str = args.corpus
    repos_yaml_path: str = args.repos_yaml
    experiment_path: str | None = getattr(args, "experiment", None)
    out_dir: str = getattr(args, "out_dir", ".")
    strategies_raw: list | None = getattr(args, "strategy", None)

    # Load tuning (from corpus report --emit-tuning output)
    try:
        tuning = load_tuning(corpus_path)
    except FileNotFoundError:
        print(f"Error: file not found: {corpus_path}", file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)

    # Load repos.yaml
    try:
        repos_yaml = load_repos_yaml(repos_yaml_path)
    except FileNotFoundError:
        print(
            f"Error: file not found: {repos_yaml_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Load or create experiment envelope
    if experiment_path:
        try:
            with open(experiment_path, encoding="utf-8") as f:
                exp_data = json.load(f)
        except FileNotFoundError:
            print(
                f"Error: file not found: {experiment_path}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        except json.JSONDecodeError as exc:
            print(f"Error: invalid JSON — {exc}", file=sys.stderr)
            raise SystemExit(1)

        from cts.corpus.experiment_schema import (
            ExperimentEnvelope,
            VariantSpec,
        )

        variants = [
            VariantSpec(name=v.get("name", f"V{i}"))
            for i, v in enumerate(exp_data.get("variants", []))
        ]
        experiment = ExperimentEnvelope(
            id=exp_data.get("id", ""),
            description=exp_data.get("description", ""),
            hypothesis=exp_data.get("hypothesis", ""),
            variants=variants,
        )
    else:
        experiment = create_experiment()

    # Parse strategy overrides (e.g. "A=aggressive")
    strategies = None
    if strategies_raw:
        strategies = {}
        for s in strategies_raw:
            if "=" in s:
                vname, sname = s.split("=", 1)
                strategies[vname.strip()] = sname.strip()

    summary = propose_experiment(
        tuning,
        experiment,
        repos_yaml,
        out_dir=out_dir,
        strategies=strategies,
    )

    # Report
    print(
        f"Experiment proposed: {summary['experiment_path']}",
        file=sys.stderr,
    )
    for v in summary["variants"]:
        print(
            f"  {v['name']} ({v['strategy']}): "
            f"{v['recommendation_count']} rec(s), "
            f"{v['active_patches']} active patch(es)",
            file=sys.stderr,
        )


def _experiment_list(args: argparse.Namespace) -> None:
    from cts.corpus.registry import (
        filter_entries,
        render_list_json,
        render_list_markdown,
        render_list_text,
        scan_registry,
    )

    root: str = getattr(args, "registry_root", "experiments")
    fmt: str = getattr(args, "list_format", "text")
    winner_filter: str | None = getattr(args, "winner", None)
    verdict_filter: str | None = getattr(args, "verdict", None)
    since_days: float | None = getattr(args, "since_days", None)
    pkpi_filter: str | None = getattr(args, "primary_kpi_filter", None)
    contains: str | None = getattr(args, "contains", None)

    entries = scan_registry(root)
    entries = filter_entries(
        entries,
        winner=winner_filter,
        verdict=verdict_filter,
        since_days=since_days,
        primary_kpi=pkpi_filter,
        contains=contains,
    )

    if fmt == "json":
        print(render_list_json(entries))
    elif fmt == "markdown":
        print(render_list_markdown(entries))
    else:
        print(render_list_text(entries))


def _experiment_show(args: argparse.Namespace) -> None:
    from cts.corpus.registry import (
        find_experiment_dir,
        render_show_text,
        show_experiment,
    )

    exp_id: str = args.exp_id
    root: str = getattr(args, "registry_root", "experiments")

    exp_dir = find_experiment_dir(exp_id, root=root)
    if exp_dir is None:
        print(
            f"Error: experiment not found: {exp_id}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    detail = show_experiment(exp_dir)
    if detail is None:
        print(
            f"Error: could not load experiment: {exp_id}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(render_show_text(detail))


def _experiment_archive(args: argparse.Namespace) -> None:
    from cts.corpus.archive import archive_experiment

    experiment_path: str = args.experiment
    result_path: str | None = getattr(args, "result", None)
    result_md_path: str | None = getattr(args, "result_md", None)
    variant_dir: str | None = getattr(args, "variant_dir", None)
    repos_yaml_path: str | None = getattr(args, "repos_yaml", None)
    registry_root: str = getattr(args, "registry_root", "experiments")

    # Validate experiment exists
    if not os.path.exists(experiment_path):
        print(
            f"Error: file not found: {experiment_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    summary = archive_experiment(
        experiment_path=experiment_path,
        result_path=result_path,
        result_md_path=result_md_path,
        variant_dir=variant_dir,
        repos_yaml_path=repos_yaml_path,
        registry_root=registry_root,
    )

    status = summary["status"]
    exp_id = summary["exp_id"]
    if status == "already_archived":
        print(
            f"Already archived: {exp_id} ({summary['message']})",
            file=sys.stderr,
        )
    else:
        print(
            f"Archived: {exp_id} → {summary['exp_dir']}",
            file=sys.stderr,
        )
        if summary.get("run_dir"):
            print(
                f"  Run: {summary['run_id']} → {summary['run_dir']}",
                file=sys.stderr,
            )


def _experiment_evaluate(args: argparse.Namespace) -> None:
    from cts.corpus.experiment_eval import (
        evaluate_experiment,
        render_experiment_result_json,
        render_experiment_result_markdown,
        render_experiment_result_text,
    )
    from cts.corpus.report import load_corpus

    corpus_path: str = args.corpus
    experiment_path: str = args.experiment
    fmt: str = getattr(args, "exp_eval_format", "text")
    out_path: str | None = getattr(args, "out", None)

    # Load corpus
    try:
        records = load_corpus(corpus_path)
    except FileNotFoundError:
        print(f"Error: file not found: {corpus_path}", file=sys.stderr)
        raise SystemExit(1)

    # Load experiment envelope
    try:
        with open(experiment_path, encoding="utf-8") as f:
            experiment = json.load(f)
    except FileNotFoundError:
        print(
            f"Error: file not found: {experiment_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        raise SystemExit(1)

    result = evaluate_experiment(records, experiment)

    if fmt == "json":
        output = render_experiment_result_json(result)
    elif fmt == "markdown":
        output = render_experiment_result_markdown(result)
    else:
        output = render_experiment_result_text(result)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        verdict = result.get("verdict", "?")
        winner = result.get("winner") or "none"
        print(
            f"Experiment eval: {out_path} (verdict: {verdict}, winner: {winner})",
            file=sys.stderr,
        )
    else:
        print(output)


def _experiment_trend(args: argparse.Namespace) -> None:
    from cts.corpus.trends import (
        generate_dashboard,
        render_dashboard_json,
        render_dashboard_markdown,
        render_dashboard_text,
    )

    root = getattr(args, "registry_root", "experiments")
    fmt = getattr(args, "trend_format", "text")
    out_path = getattr(args, "out", None)
    window_days: Optional[float] = getattr(args, "window", None)
    primary_kpi: Optional[str] = getattr(args, "primary_kpi", None)
    group_by: Optional[str] = getattr(args, "group_by", None)

    dashboard = generate_dashboard(
        root,
        window_days=window_days,
        primary_kpi=primary_kpi,
        group_by=group_by,
    )

    if fmt == "json":
        output = render_dashboard_json(dashboard)
    elif fmt == "markdown":
        output = render_dashboard_markdown(dashboard)
    else:
        output = render_dashboard_text(dashboard)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        total = dashboard.get("total_experiments", 0)
        with_results = dashboard.get("with_results", 0)
        print(
            f"Dashboard: {out_path} ({total} experiments, {with_results} with results)",
            file=sys.stderr,
        )
    else:
        print(output)


# ---------------------------------------------------------------------------
# Semantic CLI
# ---------------------------------------------------------------------------


def _default_db_path(repo: Optional[str]) -> str:
    """Compute default SQLite path for a repo."""
    if repo:
        slug = repo.replace("/", "_").replace("\\", "_")
        return os.path.join("gw-cache", slug, "semantic.sqlite3")
    return "semantic.sqlite3"


def cmd_semantic(args: argparse.Namespace) -> None:
    action = getattr(args, "semantic_action", None)
    if not action:
        print(
            "Error: semantic requires a subcommand (index, status, rebuild)",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if action == "index":
        _semantic_index(args)
    elif action == "status":
        _semantic_status(args)
    elif action == "search":
        _semantic_search(args)
    elif action == "rebuild":
        _semantic_rebuild(args)


def _semantic_index(args: argparse.Namespace) -> None:
    from cts.semantic.config import load_config
    from cts.semantic.embedder import create_embedder
    from cts.semantic.indexer import index_repo
    from cts.semantic.store import SemanticStore

    repo: str = args.repo
    root: str = args.root
    db_path = args.db or _default_db_path(repo)
    device = getattr(args, "device", "auto")
    mock = getattr(args, "mock", False)
    max_files = getattr(args, "max_files", 0)
    max_chunks = getattr(args, "max_chunks", 0)
    max_seconds = getattr(args, "max_seconds", 0)

    config = load_config(device=device)
    store = SemanticStore(db_path)
    embedder = create_embedder(device=device, mock=mock)

    def progress(stage: str, current: int, total: int) -> None:
        if stage == "chunking_done":
            print(
                f"Chunked {current} files → {total} chunks",
                file=sys.stderr,
            )
        elif stage == "embedding" and total > 0:
            print(
                f"  Embedded {current}/{total} chunks",
                file=sys.stderr,
            )

    result = index_repo(
        root,
        repo,
        store,
        embedder,
        config,
        max_files=max_files,
        max_chunks=max_chunks,
        max_seconds=max_seconds,
        progress_fn=progress,
    )

    store.close()

    print(
        f"Indexed {repo}: "
        f"{result.files_scanned} files, "
        f"{result.chunks_total} chunks, "
        f"{result.chunks_embedded} embedded "
        f"({result.elapsed_seconds:.1f}s)"
    )
    if result.errors:
        for err in result.errors:
            print(f"  Error: {err}", file=sys.stderr)


def _semantic_status(args: argparse.Namespace) -> None:
    from cts.semantic.store import SemanticStore

    repo = getattr(args, "repo", None)
    db_path = args.db or _default_db_path(repo)

    if not os.path.exists(db_path):
        print(f"No semantic index at {db_path}")
        return

    store = SemanticStore(db_path)
    status = store.get_status()
    store.close()

    print(f"Semantic index: {db_path}")
    print(f"  Schema version: {status['schema_version']}")
    print(f"  Model:          {status['model']}")
    print(f"  Dimension:      {status['dim']}")
    print(f"  Chunks:         {status['chunks']}")
    print(f"  Embeddings:     {status['embeddings']}")
    print(f"  Last indexed:   {status['last_indexed_at']}")


def _semantic_search(args: argparse.Namespace) -> None:
    import time as _time

    from cts.semantic.embedder import create_embedder
    from cts.semantic.search import cosine_search
    from cts.semantic.store import SemanticStore

    repo = args.repo
    query: str = args.query
    db_path = args.db or _default_db_path(repo)
    topk = getattr(args, "topk", 8)
    fmt = getattr(args, "search_format", "text")
    device = getattr(args, "device", "auto")
    mock = getattr(args, "mock", False)

    if not os.path.exists(db_path):
        print(
            f"Error: no semantic index at {db_path}. "
            f"Run: cts semantic index --repo {repo} --root <dir>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    store = SemanticStore(db_path)
    dim_str = store.get_meta("dim")
    if not dim_str:
        print(
            "Error: no embeddings in store. Run index first.",
            file=sys.stderr,
        )
        store.close()
        raise SystemExit(1)

    dim = int(dim_str)

    # Embed the query
    embedder = create_embedder(device=device, mock=mock)
    t0 = _time.time()
    query_vecs = embedder.embed_texts([query])
    query_vec = query_vecs[0]

    # Retrieve candidates
    candidates = store.get_all_embeddings()
    hits = cosine_search(query_vec, candidates, dim, topk=topk)
    elapsed_ms = (_time.time() - t0) * 1000
    store.close()

    # Build result
    result = {
        "query": query,
        "repo": repo,
        "semantic_invoked": True,
        "semantic_model": embedder.model_name,
        "semantic_topk": topk,
        "semantic_time_ms": round(elapsed_ms, 1),
        "semantic_hits": [
            {
                "path": h.path,
                "start_line": h.start_line,
                "end_line": h.end_line,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }

    if fmt == "json":
        print(json.dumps(result, indent=2))
    elif fmt == "markdown":
        _render_semantic_hits_markdown(result)
    else:
        _render_semantic_hits_text(result)


def _render_semantic_hits_text(result: dict) -> None:
    print(f"Semantic search: {result['query']}")
    print(f"  Model: {result['semantic_model']}")
    print(
        f"  Time: {result['semantic_time_ms']:.0f}ms, "
        f"hits: {len(result['semantic_hits'])}"
    )
    print()
    for i, hit in enumerate(result["semantic_hits"], 1):
        print(
            f"  {i}. {hit['path']}:{hit['start_line']}-"
            f"{hit['end_line']} (score={hit['score']:.4f})"
        )


def _render_semantic_hits_markdown(result: dict) -> None:
    print("## Semantic Hits")
    print()
    print(f"**Query:** {result['query']}")
    print(
        f"**Model:** {result['semantic_model']} | "
        f"**Time:** {result['semantic_time_ms']:.0f}ms"
    )
    print()
    if result["semantic_hits"]:
        print("| # | Path | Lines | Score |")
        print("|---|------|-------|------:|")
        for i, hit in enumerate(result["semantic_hits"], 1):
            lines = f"{hit['start_line']}-{hit['end_line']}"
            print(f"| {i} | `{hit['path']}` | {lines} | {hit['score']:.4f} |")
    else:
        print("No semantic hits found.")
    print()


def _semantic_rebuild(args: argparse.Namespace) -> None:
    from cts.semantic.config import load_config
    from cts.semantic.embedder import create_embedder
    from cts.semantic.indexer import index_repo
    from cts.semantic.store import SemanticStore

    repo: str = args.repo
    root: str = args.root
    db_path = args.db or _default_db_path(repo)
    device = getattr(args, "device", "auto")
    mock = getattr(args, "mock", False)

    config = load_config(device=device)
    store = SemanticStore(db_path)
    store.rebuild()

    embedder = create_embedder(device=device, mock=mock)

    result = index_repo(root, repo, store, embedder, config)
    store.close()

    print(
        f"Rebuilt {repo}: "
        f"{result.files_scanned} files, "
        f"{result.chunks_total} chunks, "
        f"{result.chunks_embedded} embedded "
        f"({result.elapsed_seconds:.1f}s)"
    )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cts",
        description="Claude Toolstack CLI — bounded code intelligence client",
    )
    parser.add_argument("--version", action="version", version=f"cts {__version__}")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Show full tracebacks on error",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # status
    p_status = sub.add_parser("status", help="Gateway health + config")
    _add_common_args(p_status)

    # doctor
    p_doctor = sub.add_parser("doctor", help="Check stack health and configuration")
    p_doctor.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )

    # perf
    p_perf = sub.add_parser("perf", help="Show performance knobs and tuning tips")
    p_perf.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )

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

    # corpus report
    p_cr = corpus_sub.add_parser(
        "report", help="Generate analytics report from corpus JSONL"
    )
    p_cr.add_argument("corpus_file", help="Path to corpus JSONL file")
    p_cr.add_argument(
        "--format",
        dest="report_format",
        choices=["text", "markdown", "json"],
        default="markdown",
        help="Report output format (default: markdown)",
    )
    p_cr.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write report to file (default: stdout)",
    )
    p_cr.add_argument(
        "--mode",
        default=None,
        help="Filter to this mode (e.g. symbol, error)",
    )
    p_cr.add_argument(
        "--repo",
        default=None,
        help="Filter to this repo (e.g. org/repo)",
    )
    p_cr.add_argument(
        "--action",
        default=None,
        help="Filter to artifacts containing this action",
    )
    p_cr.add_argument(
        "--emit-tuning",
        default=None,
        metavar="PATH",
        help="Emit machine-readable tuning recommendations JSON",
    )

    # corpus patch
    p_cp = corpus_sub.add_parser(
        "patch",
        help="Generate patch plan from tuning recommendations",
    )
    p_cp.add_argument(
        "tuning",
        help="Path to tuning recommendations JSON",
    )
    p_cp.add_argument(
        "--repos-yaml",
        default="repos.yaml",
        help="Path to repos.yaml (default: repos.yaml)",
    )
    p_cp.add_argument(
        "--format",
        dest="patch_format",
        choices=["text", "json", "diff"],
        default="text",
        help="Patch plan output format (default: text)",
    )
    p_cp.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write patch plan to file (default: stdout)",
    )

    # corpus apply
    p_ca = corpus_sub.add_parser(
        "apply",
        help="Apply tuning recommendations to repos.yaml",
    )
    p_ca.add_argument(
        "tuning",
        help="Path to tuning recommendations JSON",
    )
    p_ca.add_argument(
        "--repos-yaml",
        default="repos.yaml",
        help="Path to repos.yaml (default: repos.yaml)",
    )
    p_ca.add_argument(
        "--allow-high-risk",
        action="store_true",
        default=False,
        help="Apply high-risk patches (default: blocked)",
    )
    p_ca.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute changes but don't write to disk",
    )
    p_ca.add_argument(
        "--rollback-out",
        default=None,
        metavar="PATH",
        help="Write rollback record to PATH (default: rollback.json)",
    )

    # corpus rollback
    p_crb = corpus_sub.add_parser(
        "rollback",
        help="Rollback a previous apply from rollback record",
    )
    p_crb.add_argument(
        "rollback_file",
        help="Path to rollback.json from a previous apply",
    )

    # corpus evaluate
    p_ce = corpus_sub.add_parser(
        "evaluate",
        help="Compare before/after corpora to evaluate tuning impact",
    )
    p_ce.add_argument(
        "before",
        help="Path to baseline corpus JSONL (before tuning)",
    )
    p_ce.add_argument(
        "after",
        help="Path to updated corpus JSONL (after tuning)",
    )
    p_ce.add_argument(
        "--format",
        dest="eval_format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Evaluation report format (default: text)",
    )
    p_ce.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write evaluation to file (default: stdout)",
    )

    # corpus baseline
    p_cb = corpus_sub.add_parser(
        "baseline",
        help="Capture KPI baseline snapshot from corpus",
    )
    p_cb.add_argument(
        "corpus",
        help="Path to corpus JSONL file",
    )
    p_cb.add_argument(
        "--label",
        default="lexical-only",
        help="Human-readable label (default: lexical-only)",
    )
    p_cb.add_argument(
        "--format",
        dest="baseline_format",
        choices=["json", "text", "markdown"],
        default="json",
        help="Output format (default: json)",
    )
    p_cb.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write baseline to file (default: stdout)",
    )
    p_cb.add_argument(
        "--mode",
        dest="mode_filter",
        default=None,
        help="Filter by mode",
    )
    p_cb.add_argument(
        "--repo",
        dest="repo_filter",
        default=None,
        help="Filter by repo",
    )
    p_cb.add_argument(
        "--since",
        type=float,
        default=None,
        metavar="DAYS",
        help="Only include records from last N days",
    )

    # corpus experiment
    p_cexp = corpus_sub.add_parser(
        "experiment",
        help="A/B tuning experiments",
    )
    exp_sub = p_cexp.add_subparsers(
        dest="experiment_action",
        help="Experiment subcommands",
    )

    # corpus experiment init
    p_ei = exp_sub.add_parser(
        "init",
        help="Create a new experiment envelope",
    )
    p_ei.add_argument(
        "--id",
        dest="exp_id",
        default="",
        help="Experiment ID (auto-generated if omitted)",
    )
    p_ei.add_argument(
        "--description",
        default="",
        help="What the experiment tests",
    )
    p_ei.add_argument(
        "--hypothesis",
        default="",
        help="Expected outcome",
    )
    p_ei.add_argument(
        "--variants",
        default="A,B",
        help="Comma-separated variant names (default: A,B)",
    )
    p_ei.add_argument(
        "--primary-kpi",
        default="confidence_final_mean",
        help="KPI that decides the winner (default: confidence_final_mean)",
    )
    p_ei.add_argument(
        "--constraint",
        action="append",
        default=None,
        help="Constraint (e.g. 'truncation_rate<=+0.02'), repeatable",
    )
    p_ei.add_argument(
        "--out",
        default="experiment.json",
        help="Output path (default: experiment.json)",
    )

    # corpus experiment propose
    p_ep = exp_sub.add_parser(
        "propose",
        help="Generate per-variant tuning and patch artifacts",
    )
    p_ep.add_argument(
        "--corpus",
        required=True,
        help="Path to tuning JSON (from --emit-tuning)",
    )
    p_ep.add_argument(
        "--repos-yaml",
        default="repos.yaml",
        help="Path to repos.yaml (default: repos.yaml)",
    )
    p_ep.add_argument(
        "--experiment",
        default=None,
        help="Path to experiment envelope JSON (optional; auto-creates if omitted)",
    )
    p_ep.add_argument(
        "--out-dir",
        default=".",
        help="Output directory for variant artifacts (default: .)",
    )
    p_ep.add_argument(
        "--strategy",
        action="append",
        default=None,
        help="Strategy override: 'A=aggressive' (repeatable)",
    )

    # corpus experiment list
    p_el = exp_sub.add_parser(
        "list",
        help="List experiments from the registry",
    )
    p_el.add_argument(
        "--root",
        dest="registry_root",
        default="experiments",
        help="Registry root directory (default: experiments/)",
    )
    p_el.add_argument(
        "--format",
        dest="list_format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    p_el.add_argument(
        "--winner",
        default=None,
        help="Filter by winner variant name",
    )
    p_el.add_argument(
        "--verdict",
        default=None,
        help="Filter by verdict (winner/tie/no_data)",
    )
    p_el.add_argument(
        "--since-days",
        type=float,
        default=None,
        help="Only show experiments from last N days",
    )
    p_el.add_argument(
        "--primary-kpi",
        dest="primary_kpi_filter",
        default=None,
        help="Filter by primary KPI name",
    )
    p_el.add_argument(
        "--contains",
        default=None,
        help="Search description/hypothesis/id for text",
    )

    # corpus experiment show
    p_es = exp_sub.add_parser(
        "show",
        help="Show details of a specific experiment",
    )
    p_es.add_argument(
        "exp_id",
        help="Experiment ID to show",
    )
    p_es.add_argument(
        "--root",
        dest="registry_root",
        default="experiments",
        help="Registry root directory (default: experiments/)",
    )

    # corpus experiment archive
    p_ea = exp_sub.add_parser(
        "archive",
        help="Archive experiment run into the registry",
    )
    p_ea.add_argument(
        "--experiment",
        required=True,
        help="Path to experiment envelope JSON",
    )
    p_ea.add_argument(
        "--result",
        default=None,
        help="Path to result.json (optional)",
    )
    p_ea.add_argument(
        "--result-md",
        default=None,
        help="Path to result.md (optional)",
    )
    p_ea.add_argument(
        "--variant-dir",
        default=None,
        help="Directory with variant artifacts (tuning, diffs)",
    )
    p_ea.add_argument(
        "--repos-yaml",
        default=None,
        help="Path to repos.yaml (optional, for hash tracking)",
    )
    p_ea.add_argument(
        "--root",
        dest="registry_root",
        default="experiments",
        help="Registry root directory (default: experiments/)",
    )

    # corpus experiment evaluate
    p_ee = exp_sub.add_parser(
        "evaluate",
        help="Evaluate experiment: assign runs to variants and pick winner",
    )
    p_ee.add_argument(
        "--corpus",
        required=True,
        help="Path to corpus JSONL (with variant-tagged records)",
    )
    p_ee.add_argument(
        "--experiment",
        required=True,
        help="Path to experiment envelope JSON",
    )
    p_ee.add_argument(
        "--format",
        dest="exp_eval_format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    p_ee.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write result to file (default: stdout)",
    )

    # corpus experiment trend
    p_et = exp_sub.add_parser(
        "trend",
        help="Generate trend dashboard from experiment registry",
    )
    p_et.add_argument(
        "--root",
        dest="registry_root",
        default="experiments",
        help="Registry root directory (default: experiments/)",
    )
    p_et.add_argument(
        "--format",
        dest="trend_format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    p_et.add_argument(
        "--window",
        type=float,
        default=None,
        metavar="DAYS",
        help="Only include experiments from last N days",
    )
    p_et.add_argument(
        "--primary-kpi",
        default=None,
        help="Filter by primary KPI name",
    )
    p_et.add_argument(
        "--group-by",
        default=None,
        choices=["strategy", "mode", "winner"],
        help="Grouping strategy (future use)",
    )
    p_et.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write dashboard to file (default: stdout)",
    )

    # -------------------------------------------------------------------
    # semantic
    # -------------------------------------------------------------------
    p_sem = sub.add_parser("semantic", help="Semantic search")
    sem_sub = p_sem.add_subparsers(dest="semantic_action", help="Semantic subcommands")

    # semantic index
    p_si = sem_sub.add_parser(
        "index",
        help="Build semantic index for a repository",
    )
    p_si.add_argument(
        "--repo",
        required=True,
        help="Repository identifier (e.g., org/repo)",
    )
    p_si.add_argument(
        "--root",
        required=True,
        help="Repository root directory to scan",
    )
    p_si.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="SQLite database path (default: auto)",
    )
    p_si.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Max files to index (0 = unlimited)",
    )
    p_si.add_argument(
        "--max-chunks",
        type=int,
        default=0,
        help="Max chunks to embed (0 = unlimited)",
    )
    p_si.add_argument(
        "--max-seconds",
        type=float,
        default=0,
        help="Time budget in seconds (0 = unlimited)",
    )
    p_si.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device (default: auto)",
    )
    p_si.add_argument(
        "--mock",
        action="store_true",
        help="Use mock embedder (for testing, no GPU)",
    )

    # semantic status
    p_ss = sem_sub.add_parser(
        "status",
        help="Show semantic index status",
    )
    p_ss.add_argument(
        "--repo",
        default=None,
        help="Repository identifier (for default db path)",
    )
    p_ss.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="SQLite database path",
    )

    # semantic search
    p_ssrch = sem_sub.add_parser(
        "search",
        help="Search semantic index by natural language query",
    )
    p_ssrch.add_argument(
        "query",
        help="Natural language search query",
    )
    p_ssrch.add_argument(
        "--repo",
        required=True,
        help="Repository identifier",
    )
    p_ssrch.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="SQLite database path (default: auto)",
    )
    p_ssrch.add_argument(
        "--topk",
        type=int,
        default=8,
        help="Number of top results (default: 8)",
    )
    p_ssrch.add_argument(
        "--format",
        dest="search_format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    p_ssrch.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device (default: auto)",
    )
    p_ssrch.add_argument(
        "--mock",
        action="store_true",
        help="Use mock embedder (for testing, no GPU)",
    )

    # semantic rebuild
    p_sr = sem_sub.add_parser(
        "rebuild",
        help="Drop and rebuild semantic index",
    )
    p_sr.add_argument(
        "--repo",
        required=True,
        help="Repository identifier",
    )
    p_sr.add_argument(
        "--root",
        required=True,
        help="Repository root directory",
    )
    p_sr.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="SQLite database path (default: auto)",
    )
    p_sr.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device (default: auto)",
    )
    p_sr.add_argument(
        "--mock",
        action="store_true",
        help="Use mock embedder (for testing, no GPU)",
    )

    return parser


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        raise SystemExit(0)

    commands = {
        "status": cmd_status,
        "doctor": cmd_doctor,
        "perf": cmd_perf,
        "search": cmd_search,
        "slice": cmd_slice,
        "symbol": cmd_symbol,
        "index": cmd_index,
        "job": cmd_job,
        "sidecar": cmd_sidecar,
        "corpus": cmd_corpus,
        "semantic": cmd_semantic,
    }
    fn = commands.get(args.command)
    if not fn:
        parser.print_help()
        raise SystemExit(1)

    debug = getattr(args, "debug", False)
    try:
        fn(args)
    except SystemExit:
        raise  # Let SystemExit pass through (exit codes from subcommands)
    except BaseException as exc:
        code = handle_cli_error(exc, debug=debug)
        raise SystemExit(code)
