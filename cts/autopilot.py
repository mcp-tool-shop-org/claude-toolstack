"""Autopilot: bounded refinement passes for evidence bundles.

When the initial bundle confidence is below threshold, autopilot
plans and executes refinement actions:

Generic actions (all modes):
  - widen_search: increase max_matches
  - add_slices: fetch more context slices
  - try_symbol: look up the query as a symbol via ctags
  - broaden_glob: remove restrictive path globs

Mode-specific actions:
  - force_trace_slices: (error) ensure slices cover trace locations
  - pin_def_slices: (symbol) ensure slices cover definition files
  - expand_callers: (symbol) add slices for uncovered caller files

Each pass produces a new bundle stored in sidecar.passes[].
Stops when confidence is sufficient or budget is exhausted.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from cts.confidence import bundle_confidence

# Default budget limits
DEFAULT_MAX_PASSES = 2
DEFAULT_MAX_SECONDS = 30
DEFAULT_MAX_EXTRA_SLICES = 5


# ---------------------------------------------------------------------------
# Refinement action catalog
# ---------------------------------------------------------------------------

# Each action is a dict with:
#   name: str — action identifier
#   description: str — human-readable label
#   applicable: callable(bundle, confidence_result) -> bool
#   adjust: callable(params) -> params — mutate search params for next pass


def _action_widen_search() -> Dict[str, Any]:
    return {
        "name": "widen_search",
        "description": "Double max_matches to find more candidates",
    }


def _action_add_slices() -> Dict[str, Any]:
    return {
        "name": "add_slices",
        "description": "Increase evidence_files to gather more context",
    }


def _action_try_symbol() -> Dict[str, Any]:
    return {
        "name": "try_symbol",
        "description": "Look up query as a symbol via ctags",
    }


def _action_broaden_glob() -> Dict[str, Any]:
    return {
        "name": "broaden_glob",
        "description": "Remove path glob restrictions",
    }


def _action_force_trace_slices(
    uncovered: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "name": "force_trace_slices",
        "description": "Fetch slices at stack trace locations missing from bundle",
        "trace_targets": uncovered,
    }


def _action_pin_def_slices(
    uncovered_defs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "name": "pin_def_slices",
        "description": "Fetch slices at symbol definition files missing from bundle",
        "def_targets": uncovered_defs,
    }


def _action_expand_callers(
    uncovered_callers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "name": "expand_callers",
        "description": "Fetch slices for caller files missing from bundle",
        "caller_targets": uncovered_callers,
    }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def plan_refinements(
    bundle: Dict[str, Any],
    conf: Dict[str, Any],
    *,
    current_params: Optional[Dict[str, Any]] = None,
    pass_number: int = 1,
) -> List[Dict[str, Any]]:
    """Plan which refinement actions to try next.

    Args:
        bundle: Current evidence bundle.
        conf: Confidence result from bundle_confidence().
        current_params: Current search parameters.
        pass_number: Which refinement pass we're planning for (1-indexed).

    Returns list of action dicts to execute (usually 1-2 actions).
    """
    if conf["sufficient"]:
        return []

    params = current_params or {}
    signals = conf.get("signals", {})
    actions: List[Dict[str, Any]] = []
    mode = bundle.get("mode", "default")

    # --- Mode-specific actions (highest priority) ---

    if mode == "error":
        uncovered = _find_uncovered_trace_targets(bundle)
        if uncovered:
            actions.append(_action_force_trace_slices(uncovered))

    elif mode == "symbol":
        uncov_defs = _find_uncovered_def_files(bundle)
        if uncov_defs:
            actions.append(_action_pin_def_slices(uncov_defs))
        uncov_callers = _find_uncovered_caller_files(bundle)
        if uncov_callers:
            actions.append(_action_expand_callers(uncov_callers))

    # --- Generic actions ---

    # Priority 1: If very few matches, widen search
    sources = bundle.get("ranked_sources", [])
    max_matches = params.get("max_matches", 50)
    if len(sources) < 5 and max_matches <= 50:
        actions.append(_action_widen_search())

    # Priority 2: If low slice coverage, add more slices
    slice_coverage = signals.get("slice_coverage", 0.0)
    if slice_coverage < 0.1:
        actions.append(_action_add_slices())

    # Priority 3: If no definition found, try symbol lookup
    def_found = signals.get("definition_found", 0.0)
    query = bundle.get("query", "")
    if def_found == 0.0 and mode == "default" and query:
        # Only if query looks like a symbol name
        import re

        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", query):
            actions.append(_action_try_symbol())

    # Priority 4: If globs are restricting results on later passes
    globs = params.get("path_globs")
    if globs and pass_number >= 2 and len(sources) < 3:
        actions.append(_action_broaden_glob())

    # Limit to 2 actions per pass to keep it bounded
    return actions[:2]


def _find_uncovered_trace_targets(
    bundle: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Find trace locations in error bundles that lack slice coverage.

    Looks at ranked_sources with in_trace=True and checks whether
    each trace file already has a matching slice. Returns the
    uncovered targets as [{path, line}, ...].
    """
    trace_sources = [
        s for s in bundle.get("ranked_sources", []) if s.get("in_trace")
    ]
    if not trace_sources:
        return []

    slice_paths = {s.get("path", "") for s in bundle.get("slices", [])}
    uncovered = []
    for src in trace_sources:
        path = src.get("path", "")
        if path and path not in slice_paths:
            uncovered.append({"path": path, "line": src.get("line", 1)})

    return uncovered


def _find_uncovered_def_files(
    bundle: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Find symbol definition files that lack slice coverage.

    Looks at symbols[] for files and checks whether each is already
    in slices[]. Returns uncovered defs as [{path, line}, ...].
    """
    symbols = bundle.get("symbols", [])
    if not symbols:
        return []

    slice_paths = {s.get("path", "") for s in bundle.get("slices", [])}
    uncovered = []
    seen: set = set()
    for sym in symbols:
        path = sym.get("file", "")
        if path and path not in slice_paths and path not in seen:
            seen.add(path)
            uncovered.append({"path": path, "line": 1})

    return uncovered


def _find_uncovered_caller_files(
    bundle: Dict[str, Any],
    max_callers: int = 5,
) -> List[Dict[str, Any]]:
    """Find top caller files (from matches) that lack slice coverage.

    Callers are match entries whose path differs from any symbol def file.
    Returns up to max_callers uncovered caller targets.
    """
    symbols = bundle.get("symbols", [])
    def_files = {s.get("file", "") for s in symbols}
    matches = bundle.get("matches", [])
    slice_paths = {s.get("path", "") for s in bundle.get("slices", [])}

    uncovered = []
    seen: set = set()
    for m in matches:
        path = m.get("path", "")
        if (
            path
            and path not in def_files
            and path not in slice_paths
            and path not in seen
        ):
            seen.add(path)
            uncovered.append({"path": path, "line": m.get("line", 1)})
        if len(uncovered) >= max_callers:
            break

    return uncovered


def apply_refinement(
    params: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a refinement action to search parameters.

    Returns new params dict (does not mutate original).
    """
    new_params = dict(params)
    name = action["name"]

    if name == "widen_search":
        new_params["max_matches"] = min(new_params.get("max_matches", 50) * 2, 200)

    elif name == "add_slices":
        new_params["evidence_files"] = min(
            new_params.get("evidence_files", 5) + DEFAULT_MAX_EXTRA_SLICES,
            15,
        )

    elif name == "broaden_glob":
        new_params.pop("path_globs", None)

    elif name == "force_trace_slices":
        # Increase evidence_files by the number of uncovered trace targets,
        # capped at the max extra slices budget. This ensures the rebuild
        # fetches enough slices to cover trace locations.
        targets = action.get("trace_targets", [])
        current = new_params.get("evidence_files", 5)
        extra = min(len(targets), DEFAULT_MAX_EXTRA_SLICES)
        new_params["evidence_files"] = min(current + extra, 15)
        # Store targets so the builder can prioritize them
        new_params["_force_slice_paths"] = [
            t["path"] for t in targets
        ]

    elif name == "pin_def_slices":
        targets = action.get("def_targets", [])
        current = new_params.get("evidence_files", 5)
        extra = min(len(targets), DEFAULT_MAX_EXTRA_SLICES)
        new_params["evidence_files"] = min(current + extra, 15)
        new_params["_force_slice_paths"] = [
            t["path"] for t in targets
        ]

    elif name == "expand_callers":
        targets = action.get("caller_targets", [])
        current = new_params.get("evidence_files", 5)
        extra = min(len(targets), DEFAULT_MAX_EXTRA_SLICES)
        new_params["evidence_files"] = min(current + extra, 15)
        # Append to existing force paths if present (from pin_def_slices)
        existing = new_params.get("_force_slice_paths", [])
        new_params["_force_slice_paths"] = existing + [
            t["path"] for t in targets
        ]

    # try_symbol doesn't change search params — it triggers a
    # separate ctags lookup in execute_refinements

    return new_params


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def execute_refinements(
    initial_bundle: Dict[str, Any],
    *,
    build_fn: Any,
    build_kwargs: Dict[str, Any],
    max_passes: int = DEFAULT_MAX_PASSES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    score_cards: Optional[list] = None,
) -> Dict[str, Any]:
    """Run autopilot refinement loop.

    Args:
        initial_bundle: The first-pass evidence bundle.
        build_fn: Callable that builds a new bundle (e.g. build_default_bundle).
        build_kwargs: Keyword args for build_fn (will be mutated per pass).
        max_passes: Maximum number of refinement passes.
        max_seconds: Wall-clock budget in seconds.
        score_cards: Score cards from the initial bundle (for confidence).

    Returns dict with:
        final_bundle: The best bundle produced.
        passes: List of pass records for sidecar storage.
        confidence: Final confidence result.
        total_passes: Number of passes executed.
    """
    start = time.monotonic()
    current_bundle = initial_bundle
    current_cards = score_cards
    passes: List[Dict[str, Any]] = []
    current_params = dict(build_kwargs)

    for pass_num in range(1, max_passes + 1):
        elapsed = time.monotonic() - start
        if elapsed >= max_seconds:
            break

        # Assess confidence
        conf = bundle_confidence(current_bundle, score_cards=current_cards)
        if conf["sufficient"]:
            break

        # Plan actions
        actions = plan_refinements(
            current_bundle,
            conf,
            current_params=current_params,
            pass_number=pass_num,
        )
        if not actions:
            break

        # Apply actions to params
        for action in actions:
            current_params = apply_refinement(current_params, action)

        # Record the pass — include action metadata for mode-specific actions
        action_records = []
        for a in actions:
            record: Dict[str, Any] = {"name": a["name"]}
            if a["name"] == "force_trace_slices":
                targets = a.get("trace_targets", [])
                record["trace_targets"] = [t["path"] for t in targets]
                record["trace_targets_count"] = len(targets)
            elif a["name"] == "pin_def_slices":
                targets = a.get("def_targets", [])
                record["def_targets"] = [t["path"] for t in targets]
                record["def_targets_count"] = len(targets)
            elif a["name"] == "expand_callers":
                targets = a.get("caller_targets", [])
                record["caller_targets"] = [t["path"] for t in targets]
                record["caller_targets_count"] = len(targets)
            action_records.append(record)

        pass_record: Dict[str, Any] = {
            "pass": pass_num,
            "actions": [a["name"] for a in actions],
            "action_details": action_records,
            "confidence_before": conf["score"],
            "reason": conf["reason"],
            "elapsed_ms": round(elapsed * 1000, 1),
        }

        # Execute the refined search
        try:
            # Build new bundle with adjusted params
            new_kwargs = dict(current_params)
            new_kwargs["debug"] = True  # always explain for confidence
            new_bundle = build_fn(**new_kwargs)

            # Extract score cards from debug
            new_cards = None
            if "_debug" in new_bundle:
                new_cards = new_bundle["_debug"].get("score_cards")

            current_bundle = new_bundle
            current_cards = new_cards
            pass_record["status"] = "ok"
        except Exception as exc:
            pass_record["status"] = "error"
            pass_record["error"] = str(exc)

        passes.append(pass_record)

    # Final confidence
    final_conf = bundle_confidence(current_bundle, score_cards=current_cards)

    return {
        "final_bundle": current_bundle,
        "passes": passes,
        "confidence": final_conf,
        "total_passes": len(passes),
    }
