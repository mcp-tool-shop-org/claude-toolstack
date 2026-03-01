#!/usr/bin/env bash
# ──────────────────────────────────────────────────
#  Variant A — Lexical Only (control arm)
#  Semantic fallback is disabled.
#  Narrowing is irrelevant (semantic is off).
# ──────────────────────────────────────────────────

export CTS_SEMANTIC_ENABLED=0
export CTS_SEMANTIC_CANDIDATE_STRATEGY=none

echo "[Variant A] Lexical-only mode"
echo "  CTS_SEMANTIC_ENABLED=$CTS_SEMANTIC_ENABLED"
echo

# Pass through all arguments to cts
python -m cts "$@"
