"""Confidence model for evidence bundles.

Scores how "sufficient" a bundle is for the requesting agent.
Used by autopilot to decide whether to refine.

Heuristics (all additive, clamped to 0..1):
  - top_score_weight: best match score contributes confidence
  - definition_found: a probable definition was found
  - source_diversity: multiple distinct files in ranked sources
  - slice_coverage: we fetched context slices
  - low_match_penalty: very few matches drags confidence down
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Thresholds
MIN_MATCHES_FOR_CONFIDENCE = 3
MIN_TOP_SCORE = 0.5
DIVERSE_FILE_COUNT = 3
SUFFICIENT_THRESHOLD = 0.6


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
        # Trace files found is a strong signal
        notes = bundle.get("notes", [])
        for n in notes:
            if "trace detected" in n.lower():
                mode_bonus = 0.15
                break
    elif mode == "symbol":
        syms = bundle.get("symbols", [])
        if syms:
            mode_bonus = 0.15
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
