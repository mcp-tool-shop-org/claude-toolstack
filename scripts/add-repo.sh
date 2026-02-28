#!/usr/bin/env bash
# add-repo.sh — Clone and onboard a repo into the toolstack.
#
# Usage:
#   ./scripts/add-repo.sh org/repo [git_url]
#
# If git_url is omitted, looks it up in repos.yaml.
# After cloning, runs a quick smoke test against the gateway.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
REPOS_ROOT="/workspace/repos"

REPO_ID="${1:-}"
GIT_URL="${2:-}"

if [ -z "$REPO_ID" ]; then
    echo "Usage: $0 org/repo [git_url]"
    echo ""
    echo "Examples:"
    echo "  $0 myorg/frontend https://github.com/myorg/frontend.git"
    echo "  $0 myorg/frontend   # looks up URL in repos.yaml"
    exit 1
fi

# Validate repo ID format
if ! echo "$REPO_ID" | grep -qP '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'; then
    echo "[ERROR] Invalid repo ID format: $REPO_ID"
    echo "        Expected: org/repo (e.g., myorg/myrepo)"
    exit 1
fi

ORG=$(echo "$REPO_ID" | cut -d/ -f1)
REPO=$(echo "$REPO_ID" | cut -d/ -f2)
DEST="$REPOS_ROOT/$ORG/$REPO"

# ---------------------------------------------------------------
# 1. Resolve git URL
# ---------------------------------------------------------------
if [ -z "$GIT_URL" ]; then
    # Try repos.yaml
    if [ -f "$REPO_DIR/repos.yaml" ]; then
        # Simple YAML extraction (no deps required)
        GIT_URL=$(python3 -c "
import yaml, sys
with open('$REPO_DIR/repos.yaml') as f:
    data = yaml.safe_load(f)
repos = data.get('repos') or {}
entry = repos.get('$REPO_ID') or {}
url = entry.get('url', '')
print(url)
" 2>/dev/null || echo "")
    fi

    if [ -z "$GIT_URL" ]; then
        echo "[ERROR] No git URL provided and not found in repos.yaml."
        echo "        Usage: $0 $REPO_ID https://github.com/$REPO_ID.git"
        exit 1
    fi
    echo "[INFO] URL from repos.yaml: $GIT_URL"
fi

# ---------------------------------------------------------------
# 2. Clone
# ---------------------------------------------------------------
if [ -d "$DEST" ]; then
    echo "[INFO] Repo already exists at $DEST"
    echo "       Pulling latest..."
    cd "$DEST" && git pull --ff-only 2>&1 | head -5
else
    echo "[INFO] Cloning $GIT_URL → $DEST"
    mkdir -p "$REPOS_ROOT/$ORG"
    git clone --depth=1 "$GIT_URL" "$DEST" 2>&1 | tail -3
fi

# ---------------------------------------------------------------
# 3. Permissions (read-only for gateway bind mount)
# ---------------------------------------------------------------
echo "[INFO] Setting permissions (readable by all)..."
chmod -R a+rX "$DEST" 2>/dev/null || true

# ---------------------------------------------------------------
# 4. Verify gateway can see it
# ---------------------------------------------------------------
echo "[INFO] Verifying gateway access..."

KEY="${API_KEY:-}"
if [ -z "$KEY" ] && [ -f "$REPO_DIR/.env" ]; then
    KEY=$(grep -E '^API_KEY=' "$REPO_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
fi

if [ -z "$KEY" ]; then
    echo "[WARN] API_KEY not set — skipping gateway verification."
    echo "       Set API_KEY env var or add it to .env"
else
    BASE="http://127.0.0.1:8088"

    # Check status
    CODE=$(curl -sS -o /dev/null -w "%{http_code}" -H "x-api-key: $KEY" "$BASE/v1/status" 2>/dev/null || echo "000")
    if [ "$CODE" != "200" ]; then
        echo "[WARN] Gateway not reachable (HTTP $CODE). Is docker compose up?"
    else
        # Try a quick search
        SEARCH_CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
            -H "x-api-key: $KEY" -H "content-type: application/json" \
            -d "{\"repo\":\"$REPO_ID\",\"query\":\".\",\"max_matches\":1}" \
            "$BASE/v1/search/rg" 2>/dev/null || echo "000")

        if [ "$SEARCH_CODE" = "200" ]; then
            echo "[OK] Gateway can search $REPO_ID"
        elif [ "$SEARCH_CODE" = "403" ]; then
            echo "[WARN] Repo $REPO_ID is not in ALLOWED_REPOS."
            echo "       Add it to .env: ALLOWED_REPOS=...,${ORG}/*"
        else
            echo "[WARN] Search returned HTTP $SEARCH_CODE"
        fi
    fi
fi

# ---------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------
echo ""
echo "=== Repo onboarded ==="
echo "  ID:   $REPO_ID"
echo "  Path: $DEST"
echo "  Size: $(du -sh "$DEST" 2>/dev/null | cut -f1 || echo '?')"
echo ""
echo "Next steps:"
echo "  1. Ensure ALLOWED_REPOS in .env includes ${ORG}/* or $REPO_ID"
echo "  2. Restart gateway: docker compose restart gateway"
echo "  3. Build ctags: curl -X POST -H 'x-api-key: \$KEY' -H 'content-type: application/json' \\"
echo "       -d '{\"repo\":\"$REPO_ID\"}' http://127.0.0.1:8088/v1/index/ctags"
