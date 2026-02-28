#!/usr/bin/env bash
# policy-lint.sh — Validate security posture of the running toolstack.
#
# Checks:
#   1. ALLOWED_REPOS is not empty (deny-by-default)
#   2. Gateway binds to loopback only
#   3. Docker socket proxy only allows CONTAINERS+EXEC
#   4. API_KEY is set and not default
#   5. Audit logging is configured
#   6. repos.yaml entries match ALLOWED_REPOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PASS=0
FAIL=0
WARN=0

ok()   { PASS=$((PASS + 1)); echo "  [PASS] $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  [FAIL] $1"; }
warn() { WARN=$((WARN + 1)); echo "  [WARN] $1"; }

echo "=== Policy Lint ==="

# ---------------------------------------------------------------
# 1. ALLOWED_REPOS
# ---------------------------------------------------------------
echo ""
echo "--- Repo Allowlist ---"

ALLOWED_REPOS=""
if [ -f "$REPO_DIR/.env" ]; then
    ALLOWED_REPOS=$(grep -E '^ALLOWED_REPOS=' "$REPO_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" || echo "")
fi

if [ -z "$ALLOWED_REPOS" ]; then
    fail "ALLOWED_REPOS is empty in .env (gateway denies all repos)"
elif [ "$ALLOWED_REPOS" = "*" ]; then
    fail "ALLOWED_REPOS=* allows all repos (too broad)"
else
    ok "ALLOWED_REPOS is set: $ALLOWED_REPOS"
fi

# ---------------------------------------------------------------
# 2. Gateway bind address
# ---------------------------------------------------------------
echo ""
echo "--- Network Binding ---"

if [ -f "$REPO_DIR/compose.yaml" ]; then
    BIND=$(grep -A1 'ports:' "$REPO_DIR/compose.yaml" | grep '8088' | head -1 || echo "")
    if echo "$BIND" | grep -q '127.0.0.1'; then
        ok "Gateway bound to 127.0.0.1 (loopback only)"
    elif echo "$BIND" | grep -q '0.0.0.0'; then
        fail "Gateway bound to 0.0.0.0 (exposed to all interfaces)"
    elif [ -n "$BIND" ]; then
        warn "Gateway bind address unclear: $BIND"
    else
        warn "Could not find gateway port binding in compose.yaml"
    fi
fi

# ---------------------------------------------------------------
# 3. Docker socket proxy permissions
# ---------------------------------------------------------------
echo ""
echo "--- Docker Socket Proxy ---"

if [ -f "$REPO_DIR/compose.yaml" ]; then
    # Check that dangerous endpoints are denied
    for ENDPOINT in IMAGES SERVICES SYSTEM NETWORKS VOLUMES AUTH; do
        VAL=$(grep -A20 'dockerproxy:' "$REPO_DIR/compose.yaml" | grep "$ENDPOINT=" | head -1 | sed 's/.*=//;s/[^0-9]//g' || echo "")
        if [ "$VAL" = "0" ]; then
            ok "Proxy denies $ENDPOINT"
        elif [ "$VAL" = "1" ]; then
            fail "Proxy ALLOWS $ENDPOINT (should be 0)"
        else
            warn "Could not verify $ENDPOINT setting"
        fi
    done

    # Check required endpoints
    for ENDPOINT in CONTAINERS EXEC; do
        VAL=$(grep -A20 'dockerproxy:' "$REPO_DIR/compose.yaml" | grep "$ENDPOINT=" | head -1 | sed 's/.*=//;s/[^0-9]//g' || echo "")
        if [ "$VAL" = "1" ]; then
            ok "Proxy allows $ENDPOINT (required)"
        else
            warn "Proxy may not allow $ENDPOINT (needed for gateway)"
        fi
    done
fi

# ---------------------------------------------------------------
# 4. API key
# ---------------------------------------------------------------
echo ""
echo "--- API Key ---"

API_KEY=""
if [ -f "$REPO_DIR/.env" ]; then
    API_KEY=$(grep -E '^API_KEY=' "$REPO_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" || echo "")
fi

if [ -z "$API_KEY" ]; then
    fail "API_KEY not set in .env"
elif [ "$API_KEY" = "change-me-now" ] || [ "$API_KEY" = "change-me" ]; then
    fail "API_KEY is still the default value"
else
    ok "API_KEY is set (not default)"
fi

# ---------------------------------------------------------------
# 5. Audit logging
# ---------------------------------------------------------------
echo ""
echo "--- Audit Logging ---"

AUDIT_PATH=""
if [ -f "$REPO_DIR/.env" ]; then
    AUDIT_PATH=$(grep -E '^AUDIT_LOG_PATH=' "$REPO_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" || echo "")
fi

if [ -n "$AUDIT_PATH" ]; then
    ok "Audit log path configured: $AUDIT_PATH"
else
    warn "AUDIT_LOG_PATH not set in .env (using default /audit/audit.jsonl)"
fi

# Check audit volume in compose
if [ -f "$REPO_DIR/compose.yaml" ]; then
    if grep -q 'gw-audit' "$REPO_DIR/compose.yaml"; then
        ok "Audit volume (gw-audit) defined in compose"
    else
        fail "Audit volume missing from compose.yaml"
    fi
fi

# ---------------------------------------------------------------
# 6. ALLOWED_CONTAINERS
# ---------------------------------------------------------------
echo ""
echo "--- Container Allowlist ---"

ALLOWED_CONTAINERS=""
if [ -f "$REPO_DIR/.env" ]; then
    ALLOWED_CONTAINERS=$(grep -E '^ALLOWED_CONTAINERS=' "$REPO_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" || echo "")
fi

if [ -z "$ALLOWED_CONTAINERS" ]; then
    fail "ALLOWED_CONTAINERS not set in .env"
elif echo "$ALLOWED_CONTAINERS" | grep -q '\*'; then
    fail "ALLOWED_CONTAINERS contains wildcard (too broad)"
else
    ok "ALLOWED_CONTAINERS set: $ALLOWED_CONTAINERS"
fi

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "  Passed:   $PASS"
echo "  Failed:   $FAIL"
echo "  Warnings: $WARN"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Fix FAILs before exposing the gateway to any network."
    exit 1
fi
