"""Confidence model for evidence bundles.

Scores how "sufficient" a bundle is for the requesting agent.
Used by autopilot to decide whether to refine.

Heuristics (all additive, clamped to 0..1):
  - top_score_weight: best match score contributes confidence
  - definition_found: a probable definition was found
  - source_diversity: multiple distinct files in ranked sources
  - slice_coverage: we fetched context slices
  - low_match_penalty: very few matches drags confidence down
  - mode_bonus: mode-specific coverage reward

Mode-specific bonuses (Signal 6):
  - error: trace presence + trace coverage (slices for trace files)
  - symbol: symbol defs found + def coverage + caller coverage
  - change: changed file slice coverage
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Thresholds
MIN_MATCHES_FOR_CONFIDENCE = 3
MIN_TOP_SCORE = 0.5
DIVERSE_FILE_COUNT = 3
SUFFICIENT_THRESHOLD = 0.6


def _error_mode_bonus(
    bundle: Dict[str, Any],
    slices: list,
) -> float:
    """Compute error-mode bonus: trace presence + trace slice coverage.

    Up to 0.15:
      - 0.05 for trace detection (from notes)
      - 0.10 scaled by % of trace files covered by slices
    """
    bonus = 0.0
    notes = bundle.get("notes", [])
    has_trace = any("trace detected" in n.lower() for n in notes)
    if has_trace:
        bonus += 0.05

    # Trace coverage: in_trace sources covered by slices
    trace_sources = [
        s for s in bundle.get("ranked_sources", []) if s.get("in_trace")
    ]
    if trace_sources:
        slice_paths = {s.get("path", "") for s in slices}
        covered = sum(
            1 for ts in trace_sources if ts.get("path", "") in slice_paths
        )
        coverage = covered / len(trace_sources)
        bonus += 0.10 * coverage

    return bonus


def _symbol_mode_bonus(
    bundle: Dict[str, Any],
    slices: list,
) -> float:
    """Compute symbol-mode bonus: defs + def coverage + caller coverage.

    Up to 0.15:
      - 0.05 for having symbol definitions
      - 0.05 scaled by % of def files covered by slices
      - 0.05 scaled by % of top caller files covered by slices
    """
    bonus = 0.0
    symbols = bundle.get("symbols", [])
    if symbols:
        bonus += 0.05

    slice_paths = {s.get("path", "") for s in slices}

    # Def file coverage
    def_files = {sym.get("file", "") for sym in symbols if sym.get("file")}
    if def_files:
        covered = sum(1 for df in def_files if df in slice_paths)
        bonus += 0.05 * (covered / len(def_files))

    # Caller coverage: match files that aren't def files
    matches = bundle.get("matches", [])
    caller_files: list = []
    seen: set = set()
    for m in matches:
        p = m.get("path", "")
        if p and p not in def_files and p not in seen:
            seen.add(p)
            caller_files.append(p)
    if caller_files:
        top_callers = caller_files[:5]
        covered = sum(1 for cf in top_callers if cf in slice_paths)
        bonus += 0.05 * (covered / len(top_callers))

    return bonus


def _change_mode_bonus(
    bundle: Dict[str, Any],
    slices: list,
) -> float:
    """Compute change-mode bonus: changed file slice coverage.

    Up to 0.15:
      - 0.05 for having a diff
      - 0.10 scaled by % of changed files covered by slices
    """
    bonus = 0.0
    diff = bundle.get("diff", "")
    if diff:
        bonus += 0.05

    sources = bundle.get("ranked_sources", [])
    if sources:
        slice_paths = {s.get("path", "") for s in slices}
        covered = sum(
            1 for src in sources if src.get("path", "") in slice_paths
        )
        bonus += 0.10 * (covered / len(sources))

    return bonus


def bundle_confidence(
    bundle: Dict[str, Any],
    *,
    score_cards: Optional[list] = None,
) -> Dict[str, Any]:
    """Compute confidence score for a bundle.

    Args:
        bundle: The evidence bundle dict.
        score_cards: Optional score cards from explain mode for richer signal.

    Returns dict with:
        score: float (0..1) — overall confidence
        sufficient: bool — score >= threshold
        signals: dict of individual signal contributions
        reason: str — human-readable explanation
    """
    signals: Dict[str, float] = {}

    sources = bundle.get("ranked_sources", [])
    matches = bundle.get("matches", [])
    slices = bundle.get("slices", [])
    mode = bundle.get("mode", "default")

    # --- Signal 1: Top match quality ---
    top_score = 0.0
    if sources:
        top_score = max(s.get("score", 0.0) for s in sources)
    # Normalize: scores above 1.5 are excellent
    top_weight = min(top_score / 1.5, 1.0) * 0.3
    signals["top_score_weight"] = round(top_weight, 3)

    # --- Signal 2: Definition found ---
    def_found = 0.0
    if score_cards:
        for card in score_cards[:10]:
            features = card.get("features", {})
            if features.get("is_prob_def") or features.get("is_def_file"):
                def_found = 0.2
                break
    signals["definition_found"] = def_found

    # --- Signal 3: Source file diversity ---
    unique_files = set()
    for s in sources:
        p = s.get("path", "")
        if p:
            unique_files.add(p)
    diversity = min(len(unique_files) / DIVERSE_FILE_COUNT, 1.0) * 0.2
    signals["source_diversity"] = round(diversity, 3)

    # --- Signal 4: Slice coverage ---
    slice_weight = min(len(slices) / 3, 1.0) * 0.15
    signals["slice_coverage"] = round(slice_weight, 3)

    # --- Signal 5: Match count penalty ---
    match_penalty = 0.0
    if len(matches) < MIN_MATCHES_FOR_CONFIDENCE:
        # 0 matches → full penalty, 1-2 → partial
        match_penalty = -0.15 * (1.0 - len(matches) / MIN_MATCHES_FOR_CONFIDENCE)
    signals["low_match_penalty"] = round(match_penalty, 3)

    # --- Signal 6: Mode-specific bonus ---
    mode_bonus = 0.0
    if mode == "error":
        mode_bonus = _error_mode_bonus(bundle, slices)
    elif mode == "symbol":
        mode_bonus = _symbol_mode_bonus(bundle, slices)
    elif mode == "change":
        mode_bonus = _change_mode_bonus(bundle, slices)
    signals["mode_bonus"] = round(mode_bonus, 3)

    # --- Aggregate ---
    raw = sum(signals.values())
    score = max(0.0, min(1.0, raw))
    sufficient = score >= SUFFICIENT_THRESHOLD

    # Reason
    if sufficient:
        threshold = SUFFICIENT_THRESHOLD
        reason = f"Confidence {score:.2f} >= {threshold} — bundle likely sufficient"
    else:
        weak = [k for k, v in signals.items() if v <= 0.0 and k != "mode_bonus"]
        if not weak:
            weak = [k for k, v in signals.items() if v < 0.1]
        reason = (
            f"Confidence {score:.2f} < {SUFFICIENT_THRESHOLD} — "
            f"weak signals: {', '.join(weak)}"
        )

    return {
        "score": round(score, 3),
        "sufficient": sufficient,
        "signals": signals,
        "reason": reason,
    }
