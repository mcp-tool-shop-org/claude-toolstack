#!/usr/bin/env bash
# verify.sh — Run all quality gates in one command.
# Exit 0 = ship-ready. Exit 1 = fix something.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

FAIL=0

echo "=== Claude Toolstack — Verify ==="

# 1. Lint
echo ""
echo "--- Ruff lint ---"
if ruff check cts/ gateway/; then
    echo "[OK] Lint clean"
else
    echo "[FAIL] Lint errors"
    FAIL=1
fi

# 2. Format
echo ""
echo "--- Ruff format ---"
if ruff format --check cts/ gateway/; then
    echo "[OK] Format clean"
else
    echo "[FAIL] Format errors"
    FAIL=1
fi

# 3. Unit tests
echo ""
echo "--- Unit tests ---"
if python -m pytest tests/ -q --tb=short; then
    echo "[OK] Tests pass"
else
    echo "[FAIL] Tests failed"
    FAIL=1
fi

# 4. CLI installs and runs
echo ""
echo "--- CLI smoke ---"
pip install -e . --quiet 2>/dev/null
if cts --version && cts --help >/dev/null; then
    echo "[OK] CLI installs and runs"
else
    echo "[FAIL] CLI broken"
    FAIL=1
fi

# 5. Clean wheel build
echo ""
echo "--- Wheel build ---"
if python -m build --wheel --outdir dist/ 2>/dev/null; then
    echo "[OK] Wheel builds"
else
    echo "[FAIL] Wheel build failed"
    FAIL=1
fi

# 6. Gateway container build (if Docker available)
echo ""
echo "--- Gateway container ---"
if command -v docker &>/dev/null; then
    if docker build -t claude-gateway:verify ./gateway 2>/dev/null; then
        echo "[OK] Gateway container builds"
    else
        echo "[FAIL] Gateway container build failed"
        FAIL=1
    fi
else
    echo "[SKIP] Docker not available"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "=== All checks passed ==="
else
    echo "=== FAILED — fix issues above ==="
    exit 1
fi
