"""Extract normalized metrics from sidecar artifacts.

Defensive parsing: missing fields are tracked in ``missing_fields``
rather than raising exceptions.  This ensures partial ingestion is
always possible even as the sidecar schema evolves.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from cts.confidence import bundle_confidence
from cts.corpus.model import CorpusRecord, PassRecord

# Sections whose byte sizes are measured in the final bundle
_SECTION_KEYS = [
    "ranked_sources",
    "matches",
    "slices",
    "symbols",
    "diff",
    "suggested_commands",
    "notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_bytes(obj: Any) -> int:
    """Compute the JSON-encoded byte size of *obj*."""
    try:
        return len(json.dumps(obj, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _extract_section_bytes(final: Dict[str, Any]) -> Dict[str, int]:
    """Compute byte sizes for each section in the final bundle."""
    result: Dict[str, int] = {}
    for key in _SECTION_KEYS:
        if key in final:
            result[key] = _json_bytes(final[key])
    return result


def _extract_truncation_flags(final: Dict[str, Any]) -> Dict[str, bool]:
    """Extract truncation flags from the final bundle."""
    flags: Dict[str, bool] = {}
    if "truncated" in final:
        flags["truncated"] = bool(final["truncated"])
    return flags


def _extract_timings(final: Dict[str, Any]) -> Dict[str, float]:
    """Extract timing data from ``_debug.timings`` if present."""
    debug = final.get("_debug")
    if not isinstance(debug, dict):
        return {}
    timings = debug.get("timings")
    if not isinstance(timings, dict):
        return {}
    result: Dict[str, float] = {}
    for key, val in timings.items():
        if isinstance(val, (int, float)):
            result[key] = float(val)
    return result


def _extract_actions(passes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build an ordered action list from all pass records.

    Includes trigger reasons and summary counts but never raw file
    content or diff text (safe for corpus output).
    """
    actions: List[Dict[str, Any]] = []
    _COUNT_KEYS = (
        "trace_targets_count",
        "def_targets_count",
        "caller_targets_count",
        "changed_targets_count",
        "ident_count",
    )

    for p in passes:
        details = p.get("action_details", [])
        action_names = p.get("actions", [])

        if details:
            for detail in details:
                record: Dict[str, Any] = {"name": detail.get("name", "")}
                if "trigger_reason" in detail:
                    record["trigger_reason"] = detail["trigger_reason"]
                for key in _COUNT_KEYS:
                    if key in detail:
                        record[key] = detail[key]
                actions.append(record)
        elif action_names:
            # Fallback: just action names without details
            for name in action_names:
                actions.append({"name": name})

    return actions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_record(
    sidecar: Dict[str, Any],
    *,
    source_path: str = "",
) -> CorpusRecord:
    """Extract a normalized :class:`CorpusRecord` from a sidecar dict.

    Missing or unparseable fields are tracked in ``missing_fields``
    rather than raising.  This allows partial ingestion of artifacts
    whose ``_debug`` section is absent or whose schema is slightly
    ahead of the extractor.
    """
    missing: List[str] = []

    # --- Identity ---
    schema_version = sidecar.get("bundle_schema_version", 0)
    if not schema_version:
        missing.append("bundle_schema_version")

    repo = sidecar.get("repo", "")
    if not repo:
        missing.append("repo")

    mode = sidecar.get("mode", "")
    if not mode:
        missing.append("mode")

    created_at = sidecar.get("created_at", 0.0)
    if not created_at:
        missing.append("created_at")

    request_id = sidecar.get("request_id", "")
    if not request_id:
        missing.append("request_id")

    # --- Passes ---
    passes = sidecar.get("passes", [])
    passes_count = len(passes)

    # --- Final bundle ---
    final = sidecar.get("final", {})
    if not final:
        missing.append("final")

    # --- Confidence ---
    confidence_pass1: Optional[float] = None
    confidence_final: Optional[float] = None
    confidence_delta: Optional[float] = None

    # Score cards from _debug (if available)
    score_cards = None
    debug_data = final.get("_debug") if final else None
    if isinstance(debug_data, dict):
        score_cards = debug_data.get("score_cards")
    else:
        missing.append("_debug")

    if passes:
        # confidence_pass1 from first pass record
        c1 = passes[0].get("confidence_before")
        if isinstance(c1, (int, float)):
            confidence_pass1 = float(c1)
        else:
            missing.append("confidence_pass1")

    # confidence_final: recompute from final bundle
    if final:
        try:
            conf = bundle_confidence(final, score_cards=score_cards)
            confidence_final = conf["score"]
        except Exception:
            missing.append("confidence_final")

    # When no passes ran, initial IS final
    if confidence_pass1 is None and confidence_final is not None:
        confidence_pass1 = confidence_final

    if confidence_pass1 is not None and confidence_final is not None:
        confidence_delta = round(confidence_final - confidence_pass1, 4)

    # --- Actions ---
    actions = _extract_actions(passes)

    # --- Size metrics ---
    bundle_bytes_final = _json_bytes(final) if final else 0
    section_bytes = _extract_section_bytes(final)

    # --- Truncation ---
    truncation_flags = _extract_truncation_flags(final)

    # --- Timings ---
    timings_ms = _extract_timings(final)
    if not timings_ms and isinstance(debug_data, dict):
        # _debug present but no timings sub-key
        missing.append("timings")

    # --- Semantic augmentation (Phase 4) ---
    semantic_invoked = False
    semantic_time_ms: Optional[float] = None
    semantic_hit_count = 0
    semantic_action_fired = False
    semantic_lift: Optional[float] = None

    # Check for semantic data in _debug.semantic or final.semantic
    semantic_data = None
    if isinstance(debug_data, dict):
        semantic_data = debug_data.get("semantic")
    if semantic_data is None and final:
        semantic_data = final.get("semantic")

    if isinstance(semantic_data, dict):
        semantic_invoked = bool(semantic_data.get("invoked", False))
        st = semantic_data.get("time_ms")
        if isinstance(st, (int, float)):
            semantic_time_ms = float(st)
        semantic_hit_count = int(semantic_data.get("hit_count", 0))

    # Check if autopilot fired the semantic_fallback action
    for a in actions:
        if a.get("name") == "semantic_fallback":
            semantic_action_fired = True
            break

    # Compute semantic lift: confidence delta attributable to the pass
    # that included semantic_fallback
    if semantic_action_fired and passes:
        for idx, p in enumerate(passes):
            p_actions = p.get("actions", [])
            if "semantic_fallback" in p_actions:
                conf_before = p.get("confidence_before")
                # Use next pass's confidence_before, or final confidence
                if idx + 1 < len(passes):
                    conf_after = passes[idx + 1].get("confidence_before")
                elif confidence_final is not None:
                    conf_after = confidence_final
                else:
                    conf_after = None
                if isinstance(conf_before, (int, float)) and isinstance(
                    conf_after, (int, float)
                ):
                    semantic_lift = round(conf_after - conf_before, 4)
                break

    # --- Candidate narrowing (Phase 4.2) ---
    semantic_candidate_strategy = ""
    semantic_candidate_files = 0
    semantic_candidate_chunks = 0
    semantic_candidate_fallback_used = False

    # Look in _debug.semantic_candidates or _debug.semantic.candidate_selection
    cand_data = None
    if isinstance(debug_data, dict):
        cand_data = debug_data.get("semantic_candidates")
    if cand_data is None and isinstance(semantic_data, dict):
        cand_data = semantic_data.get("candidate_selection")

    if isinstance(cand_data, dict):
        semantic_candidate_strategy = str(
            cand_data.get("strategy", "")
        )
        semantic_candidate_files = int(
            cand_data.get("candidate_files", 0)
        )
        semantic_candidate_chunks = int(
            cand_data.get("candidate_chunks_considered", 0)
        )
        semantic_candidate_fallback_used = bool(
            cand_data.get("fallback_used", False)
        )

    return CorpusRecord(
        schema_version=schema_version,
        repo=repo,
        mode=mode,
        created_at=created_at,
        request_id=request_id,
        source_path=source_path,
        passes_count=passes_count,
        confidence_pass1=confidence_pass1,
        confidence_final=confidence_final,
        confidence_delta=confidence_delta,
        actions=actions,
        bundle_bytes_final=bundle_bytes_final,
        section_bytes=section_bytes,
        truncation_flags=truncation_flags,
        timings_ms=timings_ms,
        missing_fields=missing,
        semantic_invoked=semantic_invoked,
        semantic_time_ms=semantic_time_ms,
        semantic_hit_count=semantic_hit_count,
        semantic_action_fired=semantic_action_fired,
        semantic_lift=semantic_lift,
        semantic_candidate_strategy=semantic_candidate_strategy,
        semantic_candidate_files=semantic_candidate_files,
        semantic_candidate_chunks=semantic_candidate_chunks,
        semantic_candidate_fallback_used=semantic_candidate_fallback_used,
    )


def extract_passes(
    sidecar: Dict[str, Any],
) -> List[PassRecord]:
    """Extract per-pass records from a sidecar artifact.

    Returns one :class:`PassRecord` per refinement pass.  Sensitive
    content (file paths in target lists) is stripped — only counts
    and trigger reasons are kept.
    """
    request_id = sidecar.get("request_id", "")
    passes = sidecar.get("passes", [])
    records: List[PassRecord] = []

    _COUNT_KEYS = (
        "trace_targets_count",
        "def_targets_count",
        "caller_targets_count",
        "changed_targets_count",
        "ident_count",
    )

    for i, p in enumerate(passes):
        action_names = p.get("actions", [])
        details = p.get("action_details", [])

        # Sanitize action details (counts + reasons only)
        clean_details: List[Dict[str, Any]] = []
        for d in details:
            clean: Dict[str, Any] = {"name": d.get("name", "")}
            if "trigger_reason" in d:
                clean["trigger_reason"] = d["trigger_reason"]
            for key in _COUNT_KEYS:
                if key in d:
                    clean[key] = d[key]
            clean_details.append(clean)

        records.append(
            PassRecord(
                request_id=request_id,
                pass_index=i,
                confidence=p.get("confidence_before"),
                actions_this_pass=action_names,
                action_details=clean_details,
                status=p.get("status", ""),
                elapsed_ms=p.get("elapsed_ms", 0.0),
            )
        )

    return records
