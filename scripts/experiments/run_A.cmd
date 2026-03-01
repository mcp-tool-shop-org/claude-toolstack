@echo off
REM ──────────────────────────────────────────────────
REM  Variant A — Lexical Only (control arm)
REM  Semantic fallback is disabled.
REM  Narrowing is irrelevant (semantic is off).
REM ──────────────────────────────────────────────────

set CTS_SEMANTIC_ENABLED=0
set CTS_SEMANTIC_CANDIDATE_STRATEGY=none

echo [Variant A] Lexical-only mode
echo   CTS_SEMANTIC_ENABLED=%CTS_SEMANTIC_ENABLED%
echo.

REM Pass through all arguments to cts
python -m cts %*
