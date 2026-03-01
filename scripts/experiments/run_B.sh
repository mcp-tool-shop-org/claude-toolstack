#!/usr/bin/env bash
# ──────────────────────────────────────────────────
#  Variant B — Lexical + Semantic Fallback
#  Semantic fallback is enabled.
#  Narrowing stays OFF (Phase 4.1 control).
# ──────────────────────────────────────────────────

export CTS_SEMANTIC_ENABLED=1
export CTS_SEMANTIC_CANDIDATE_STRATEGY=none

echo "[Variant B] Lexical + Semantic fallback"
echo "  CTS_SEMANTIC_ENABLED=$CTS_SEMANTIC_ENABLED"
echo "  CTS_SEMANTIC_CANDIDATE_STRATEGY=$CTS_SEMANTIC_CANDIDATE_STRATEGY"
echo

# Pass through all arguments to cts
python -m cts "$@"
