@echo off
REM ──────────────────────────────────────────────────
REM  Variant B2 — Semantic + Narrowing (Phase 4.2)
REM  Semantic fallback is enabled.
REM  Narrowing is enabled (exclude_top_k).
REM ──────────────────────────────────────────────────

set CTS_SEMANTIC_ENABLED=1
set CTS_SEMANTIC_CANDIDATE_STRATEGY=exclude_top_k

echo [Variant B2] Semantic + Narrowing
echo   CTS_SEMANTIC_ENABLED=%CTS_SEMANTIC_ENABLED%
echo   CTS_SEMANTIC_CANDIDATE_STRATEGY=%CTS_SEMANTIC_CANDIDATE_STRATEGY%
echo.

REM Pass through all arguments to cts
python -m cts %*
