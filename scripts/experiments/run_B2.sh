#!/usr/bin/env bash
# ──────────────────────────────────────────────────
#  Variant B2 — Semantic + Narrowing (Phase 4.2)
#  Semantic fallback is enabled.
#  Narrowing is enabled (exclude_top_k).
# ──────────────────────────────────────────────────

export CTS_SEMANTIC_ENABLED=1
export CTS_SEMANTIC_CANDIDATE_STRATEGY=exclude_top_k

echo "[Variant B2] Semantic + Narrowing"
echo "  CTS_SEMANTIC_ENABLED=$CTS_SEMANTIC_ENABLED"
echo "  CTS_SEMANTIC_CANDIDATE_STRATEGY=$CTS_SEMANTIC_CANDIDATE_STRATEGY"
echo

# Pass through all arguments to cts
python -m cts "$@"
