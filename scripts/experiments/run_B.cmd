@echo off
REM ──────────────────────────────────────────────────
REM  Variant B — Lexical + Semantic Fallback
REM  Semantic fallback is enabled.
REM  Narrowing stays OFF (Phase 4.1 control).
REM ──────────────────────────────────────────────────

set CTS_SEMANTIC_ENABLED=1
set CTS_SEMANTIC_CANDIDATE_STRATEGY=none

echo [Variant B] Lexical + Semantic fallback
echo   CTS_SEMANTIC_ENABLED=%CTS_SEMANTIC_ENABLED%
echo   CTS_SEMANTIC_CANDIDATE_STRATEGY=%CTS_SEMANTIC_CANDIDATE_STRATEGY%
echo.

REM Pass through all arguments to cts
python -m cts %*
