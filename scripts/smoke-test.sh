#!/usr/bin/env bash
# smoke-test.sh — Validate gateway is running with security rails active.
# Usage: ./scripts/smoke-test.sh [API_KEY] [REPO_ID]
set -euo pipefail

KEY="${1:-${API_KEY:-change-me-now}}"
REPO="${2:-}"
BASE="http://127.0.0.1:8088"
PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); echo "  [PASS] $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  [FAIL] $1"; }

header() { echo ""; echo "--- $1 ---"; }

# ---------------------------------------------------------------
header "1. Status endpoint"
# ---------------------------------------------------------------
STATUS=$(curl -sS -w "\n%{http_code}" -H "x-api-key: $KEY" "$BASE/v1/status" 2>&1)
CODE=$(echo "$STATUS" | tail -1)
BODY=$(echo "$STATUS" | sed '$d')
if [ "$CODE" = "200" ]; then
    ok "GET /v1/status returned 200"
    echo "     $(echo "$BODY" | python3 -m json.tool 2>/dev/null | head -5)..."
else
    fail "GET /v1/status returned $CODE"
    echo "     $BODY"
fi

# ---------------------------------------------------------------
header "2. Auth enforcement"
# ---------------------------------------------------------------
CODE=$(curl -sS -o /dev/null -w "%{http_code}" -H "x-api-key: wrong-key" "$BASE/v1/status")
if [ "$CODE" = "401" ]; then
    ok "Bad API key rejected (401)"
else
    fail "Bad API key returned $CODE (expected 401)"
fi

CODE=$(curl -sS -o /dev/null -w "%{http_code}" "$BASE/v1/status")
if [ "$CODE" = "401" ]; then
    ok "Missing API key rejected (401)"
else
    fail "Missing API key returned $CODE (expected 401)"
fi

# ---------------------------------------------------------------
header "3. Repo allowlist enforcement"
# ---------------------------------------------------------------
CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "x-api-key: $KEY" -H "content-type: application/json" \
    -d '{"repo":"evil/../../../etc","query":"passwd"}' \
    "$BASE/v1/search/rg")
if [ "$CODE" = "400" ] || [ "$CODE" = "403" ]; then
    ok "Path traversal blocked ($CODE)"
else
    fail "Path traversal returned $CODE (expected 400 or 403)"
fi

CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "x-api-key: $KEY" -H "content-type: application/json" \
    -d '{"repo":"nonexistent/repo","query":"test"}' \
    "$BASE/v1/search/rg")
if [ "$CODE" = "403" ] || [ "$CODE" = "404" ]; then
    ok "Nonexistent repo rejected ($CODE)"
else
    fail "Nonexistent repo returned $CODE (expected 403 or 404)"
fi

# ---------------------------------------------------------------
header "4. Rate limiting"
# ---------------------------------------------------------------
echo "  Sending burst of requests..."
RATE_429=false
for i in $(seq 1 20); do
    CODE=$(curl -sS -o /dev/null -w "%{http_code}" -H "x-api-key: $KEY" "$BASE/v1/status")
    if [ "$CODE" = "429" ]; then
        RATE_429=true
        break
    fi
done
if $RATE_429; then
    ok "Rate limiter triggered 429 after burst"
else
    fail "Rate limiter did not trigger 429 (check RATE_LIMIT_RPS/BURST)"
fi

# ---------------------------------------------------------------
# Optional: live repo tests (only if REPO is provided)
# ---------------------------------------------------------------
if [ -n "$REPO" ]; then
    header "5. Search (repo: $REPO)"
    RESP=$(curl -sS -w "\n%{http_code}" \
        -H "x-api-key: $KEY" -H "content-type: application/json" \
        -d "{\"repo\":\"$REPO\",\"query\":\"function\",\"max_matches\":5}" \
        "$BASE/v1/search/rg")
    CODE=$(echo "$RESP" | tail -1)
    if [ "$CODE" = "200" ]; then
        ok "Search returned 200"
        echo "$RESP" | sed '$d' | python3 -m json.tool 2>/dev/null | head -10
    else
        fail "Search returned $CODE"
    fi

    header "6. File slice (repo: $REPO)"
    # Find a file to slice
    FILE=$(curl -sS -H "x-api-key: $KEY" -H "content-type: application/json" \
        -d "{\"repo\":\"$REPO\",\"query\":\".\",\"max_matches\":1}" \
        "$BASE/v1/search/rg" | python3 -c "import sys,json; print(json.load(sys.stdin)['matches'][0]['path'])" 2>/dev/null || echo "")
    if [ -n "$FILE" ]; then
        CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
            -H "x-api-key: $KEY" -H "content-type: application/json" \
            -d "{\"repo\":\"$REPO\",\"path\":\"$FILE\",\"start\":1,\"end\":20}" \
            "$BASE/v1/file/slice")
        if [ "$CODE" = "200" ]; then
            ok "File slice returned 200 for $FILE"
        else
            fail "File slice returned $CODE"
        fi
    else
        echo "  [SKIP] Could not find a file to slice"
    fi

    header "7. Ctags index (repo: $REPO)"
    RESP=$(curl -sS -w "\n%{http_code}" \
        -H "x-api-key: $KEY" -H "content-type: application/json" \
        -d "{\"repo\":\"$REPO\"}" \
        "$BASE/v1/index/ctags")
    CODE=$(echo "$RESP" | tail -1)
    if [ "$CODE" = "200" ]; then
        ok "Ctags index returned 200"
    else
        fail "Ctags index returned $CODE"
        echo "  $(echo "$RESP" | sed '$d')"
    fi
else
    echo ""
    echo "[INFO] Skipping live repo tests. Pass a repo ID as second arg:"
    echo "       ./scripts/smoke-test.sh \$API_KEY myorg/myrepo"
fi

# ---------------------------------------------------------------
header "Summary"
# ---------------------------------------------------------------
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
