"""Batch experiment runner for Phase 4.1 A/B.

Runs ripgrep locally (no gateway needed), feeds results through the full
bundle -> confidence -> autopilot -> sidecar pipeline, emits artifacts.

Usage:
    python scripts/experiments/batch_run.py --variant A
    python scripts/experiments/batch_run.py --variant B

Variant A: CTS_SEMANTIC_ENABLED=0 (lexical only)
Variant B: CTS_SEMANTIC_ENABLED=1 (semantic fallback active)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure cts is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cts import bundle as bundle_mod
from cts import schema as schema_mod
from cts.autopilot import execute_refinements
from cts.confidence import bundle_confidence

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

QUERIES_PATH = Path(__file__).parent / "queries.json"
ARTIFACTS_ROOT = Path(__file__).resolve().parents[2] / "artifacts"

DEFAULT_EXCLUDES = [
    "node_modules/",
    ".git/",
    "__pycache__/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    "*.min.js",
    "*.map",
    "package-lock.json",
    "yarn.lock",
]

MAX_MATCHES = 200
AUTOPILOT_PASSES = 3
AUTOPILOT_MAX_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Local ripgrep (replaces gateway /v1/search/rg)
# ---------------------------------------------------------------------------


def _find_rg() -> List[str]:
    """Find ripgrep binary. Prefers standalone rg, falls back to claude --ripgrep."""
    import shutil

    if shutil.which("rg"):
        return ["rg"]
    # Claude Code bundles ripgrep behind --ripgrep flag
    claude_path = (
        Path.home()
        / "AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"
    )
    if claude_path.exists():
        for exe in sorted(claude_path.glob("claude-code/*/claude.exe"), reverse=True):
            return [str(exe), "--ripgrep"]
    return ["rg"]  # last resort, will error if not found


_RG_CMD: Optional[List[str]] = None


def _get_rg() -> List[str]:
    global _RG_CMD
    if _RG_CMD is None:
        _RG_CMD = _find_rg()
    return _RG_CMD


def run_rg(
    query: str, repo_root: str, max_matches: int = MAX_MATCHES
) -> Dict[str, Any]:
    """Run ripgrep locally and return gateway-compatible search_data."""
    cmd = list(_get_rg()) + ["--json", "--max-count", str(max_matches)]
    for ex in DEFAULT_EXCLUDES:
        if ex.endswith("/"):
            cmd += ["--glob", f"!{ex}**"]
        else:
            cmd += ["--glob", f"!{ex}"]
    cmd.append(query)
    cmd.append(repo_root)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        output = result.stdout or ""
    except subprocess.TimeoutExpired:
        output = ""
    except FileNotFoundError:
        print("Error: ripgrep (rg) not found in PATH", file=sys.stderr)
        sys.exit(1)

    matches: List[Dict[str, Any]] = []
    root_path = Path(repo_root).resolve()
    for line in output.splitlines():
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "match":
            continue
        data = evt.get("data", {})
        raw_path = data.get("path", {}).get("text", "")
        line_no = data.get("line_number")
        submatches = data.get("submatches", [])
        snippet = data.get("lines", {}).get("text", "").rstrip("\n")
        if len(snippet) > 500:
            snippet = snippet[:500] + "\u2026"

        # Make path relative to repo root
        try:
            rel_path = str(Path(raw_path).resolve().relative_to(root_path))
            rel_path = rel_path.replace("\\", "/")
        except ValueError:
            rel_path = raw_path.replace("\\", "/")

        matches.append(
            {
                "path": rel_path,
                "line": line_no,
                "snippet": snippet,
                "submatches": [
                    {"start": sm.get("start"), "end": sm.get("end")}
                    for sm in submatches
                ],
            }
        )

    return {
        "repo": "",  # filled by caller
        "query": query,
        "count": len(matches),
        "matches": matches,
        "truncated": len(matches) >= max_matches,
    }


# ---------------------------------------------------------------------------
# Sidecar emission
# ---------------------------------------------------------------------------


def emit_sidecar(
    bundle: Dict[str, Any],
    path: Path,
    *,
    repo: str,
    query: str,
    autopilot_passes: Optional[list] = None,
) -> None:
    """Write a sidecar artifact JSON file using the canonical schema."""
    sidecar = schema_mod.wrap_bundle(
        bundle,
        mode=bundle.get("mode", "default"),
        request_id=bundle.get("request_id", str(uuid.uuid4())),
        repo=repo,
        query=query,
        debug=True,
        passes=autopilot_passes,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Semantic store path resolution (mirrors cli.py)
# ---------------------------------------------------------------------------


def _default_db_path(repo: str) -> str:
    slug = repo.replace("/", "_").replace("\\", "_")
    return os.path.join("gw-cache", slug, "semantic.sqlite3")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Batch A/B experiment runner")
    parser.add_argument(
        "--variant",
        choices=["A", "B"],
        required=True,
        help="A=lexical-only, B=lexical+semantic",
    )
    parser.add_argument(
        "--queries",
        default=str(QUERIES_PATH),
        help="Path to queries JSON file",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Max artifacts to produce (default: 80)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Override output directory (default: artifacts/<variant>)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print queries without running",
    )
    args = parser.parse_args()

    # Load queries
    with open(args.queries, encoding="utf-8") as f:
        data = json.load(f)
    queries = data["queries"]

    # Determine output dir and semantic flag
    if args.variant == "A":
        out_dir = Path(args.out_dir) if args.out_dir else ARTIFACTS_ROOT / "A_lexical"
        semantic_enabled = False
        label = "Lexical-only"
    else:
        out_dir = Path(args.out_dir) if args.out_dir else ARTIFACTS_ROOT / "B_semantic"
        semantic_enabled = True
        label = "Lexical + Semantic"

    print(f"[Variant {args.variant}] {label}")
    print(f"  Output: {out_dir}")
    print(f"  Queries: {len(queries)}")
    print(f"  Semantic: {'ON' if semantic_enabled else 'OFF'}")
    print(f"  Limit: {args.limit}")
    print()

    if args.dry_run:
        for i, q in enumerate(queries[: args.limit], 1):
            print(f"  {i:3d}. [{q['repo'].split('/')[-1]}] {q['query']}")
        return

    # Change to repo root so gw-cache/ is resolvable
    os.chdir(Path(__file__).resolve().parents[2])

    total_start = time.time()
    produced = 0
    skipped = 0

    for i, q in enumerate(queries[: args.limit], 1):
        repo = q["repo"]
        root = q["root"]
        query = q["query"]
        repo_short = repo.split("/")[-1]

        # Run ripgrep
        t0 = time.time()
        search_data = run_rg(query, root)
        search_data["repo"] = repo
        search_data["_request_id"] = str(uuid.uuid4())
        rg_ms = (time.time() - t0) * 1000

        if search_data["count"] == 0:
            skipped += 1
            print(
                f'  {i:3d}/{args.limit} [{repo_short}] "{query}" -> 0 matches, skipped'
            )
            continue

        # Build bundle
        b = bundle_mod.build_default_bundle(
            search_data,
            repo=repo,
            request_id=search_data["_request_id"],
            max_files=15,
            context=3,
            repo_root=root,
            debug=True,
            explain_top=10,
        )

        # Autopilot
        autopilot_passes = None
        initial_cards = None
        if "_debug" in b:
            initial_cards = b["_debug"].get("score_cards")

        initial_conf = bundle_confidence(b, score_cards=initial_cards)

        build_kwargs: Dict[str, Any] = {
            "search_data": search_data,
            "repo": repo,
            "request_id": search_data["_request_id"],
            "max_files": 15,
            "context": 3,
            "repo_root": root,
            "explain_top": 10,
        }

        # Semantic gate (only in Variant B)
        if semantic_enabled:
            sem_db = _default_db_path(repo)
            if os.path.exists(sem_db):
                build_kwargs["semantic_store_path"] = sem_db

        if not initial_conf["sufficient"]:
            result = execute_refinements(
                b,
                build_fn=bundle_mod.build_default_bundle,
                build_kwargs=build_kwargs,
                max_passes=AUTOPILOT_PASSES,
                max_seconds=AUTOPILOT_MAX_SECONDS,
                score_cards=initial_cards,
            )
            b = result["final_bundle"]
            autopilot_passes = result["passes"]

        # Emit
        artifact_name = f"{i:03d}_{repo_short}_{query.replace(' ', '_')[:40]}.json"
        artifact_path = out_dir / artifact_name
        emit_sidecar(
            b,
            artifact_path,
            repo=repo,
            query=query,
            autopilot_passes=autopilot_passes,
        )
        produced += 1

        # Summary line
        final_conf = bundle_confidence(b)
        conf_score = final_conf.get("score", 0)
        n_passes = len(autopilot_passes) if autopilot_passes else 0
        sem_tag = ""
        if semantic_enabled and autopilot_passes:
            for p in autopilot_passes:
                for a in p.get("actions", []):
                    name = a.get("name") if isinstance(a, dict) else a
                    if name == "semantic_fallback":
                        sem_tag = " [SEM]"
                        break

        print(
            f'  {i:3d}/{args.limit} [{repo_short}] "{query}" '
            f"-> {search_data['count']} matches, "
            f"conf={conf_score:.2f}, "
            f"passes={n_passes}"
            f"{sem_tag} "
            f"({rg_ms:.0f}ms rg)"
        )

    elapsed = time.time() - total_start
    print()
    print(f"Done: {produced} artifacts, {skipped} skipped, {elapsed:.1f}s total")
    print(f"Output: {out_dir}")


if __name__ == "__main__":
    main()
