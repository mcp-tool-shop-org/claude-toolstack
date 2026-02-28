#!/usr/bin/env bash
# bootstrap.sh — Install host prerequisites for Claude Toolstack.
# Run once on Ubuntu 22.04+ or Fedora 38+.
# Requires: root (or sudo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Claude Toolstack — Host Bootstrap ==="

# ---------------------------------------------------------------
# 1. Verify cgroup v2
# ---------------------------------------------------------------
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    echo "[OK] cgroup v2 detected"
else
    echo "[FAIL] cgroup v1 detected. cgroup v2 is required."
    echo "       Add 'systemd.unified_cgroup_hierarchy=1' to kernel cmdline and reboot."
    exit 1
fi

if docker info 2>/dev/null | grep -qi "cgroup version.*2"; then
    echo "[OK] Docker using cgroup v2"
else
    echo "[WARN] Docker may not be using cgroup v2. Check: docker info | grep -i cgroup"
fi

# ---------------------------------------------------------------
# 2. Install zram-generator (Ubuntu only; Fedora has swap-on-zram)
# ---------------------------------------------------------------
if command -v apt-get &>/dev/null; then
    if ! dpkg -l | grep -q systemd-zram-generator 2>/dev/null; then
        echo "[INFO] Installing zram-generator..."
        apt-get update -qq && apt-get install -y -qq systemd-zram-generator
    else
        echo "[OK] zram-generator already installed"
    fi
    echo "[INFO] Copying zram config..."
    cp "$REPO_DIR/systemd/zram-generator.conf" /etc/systemd/zram-generator.conf
    systemctl daemon-reload
    systemctl restart systemd-zram-setup@zram0.service 2>/dev/null || true
elif command -v dnf &>/dev/null; then
    echo "[INFO] Fedora detected — swap-on-zram is typically enabled by default."
    echo "       Verify: swapon --show"
fi

# ---------------------------------------------------------------
# 3. Sysctl tuning
# ---------------------------------------------------------------
echo "[INFO] Installing sysctl configs..."
cp "$REPO_DIR/systemd/99-claude-dev.conf" /etc/sysctl.d/99-claude-dev.conf
cp "$REPO_DIR/systemd/99-inotify-large-repos.conf" /etc/sysctl.d/99-inotify-large-repos.conf
sysctl --system >/dev/null 2>&1
echo "[OK] sysctl applied"

# ---------------------------------------------------------------
# 4. systemd slices
# ---------------------------------------------------------------
echo "[INFO] Installing systemd slices..."
for slice in claude-index.slice claude-lsp.slice claude-build.slice claude-vector.slice; do
    cp "$REPO_DIR/systemd/$slice" "/etc/systemd/system/$slice"
done
systemctl daemon-reload
echo "[OK] Slices installed"

# Verify slices are loadable
for slice in claude-index claude-lsp claude-build claude-vector; do
    if systemctl cat "${slice}.slice" &>/dev/null; then
        echo "  [OK] ${slice}.slice"
    else
        echo "  [FAIL] ${slice}.slice not found"
    fi
done

# ---------------------------------------------------------------
# 5. Docker daemon config (log driver)
# ---------------------------------------------------------------
DAEMON_JSON="/etc/docker/daemon.json"
if [ -f "$DAEMON_JSON" ]; then
    echo "[WARN] $DAEMON_JSON already exists — review manually."
    echo "       Recommended: set log-driver to 'local'."
else
    echo "[INFO] Installing Docker daemon config..."
    cp "$REPO_DIR/systemd/daemon.json" "$DAEMON_JSON"
    systemctl restart docker 2>/dev/null || echo "[WARN] Could not restart Docker."
fi

# ---------------------------------------------------------------
# 6. systemd-oomd
# ---------------------------------------------------------------
if systemctl is-enabled systemd-oomd &>/dev/null; then
    echo "[OK] systemd-oomd is enabled"
else
    echo "[WARN] systemd-oomd is not enabled."
    echo "       Consider: systemctl enable --now systemd-oomd"
fi

# ---------------------------------------------------------------
# 7. Create workspace directory
# ---------------------------------------------------------------
mkdir -p /workspace/repos
echo "[OK] /workspace/repos created"

# ---------------------------------------------------------------
# 8. Install compose service unit
# ---------------------------------------------------------------
echo "[INFO] Installing claude-toolstack.service..."
cp "$REPO_DIR/systemd/claude-toolstack.service" /etc/systemd/system/claude-toolstack.service
systemctl daemon-reload
echo "[OK] Service installed. Enable with: systemctl enable --now claude-toolstack.service"

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo ""
echo "=== Bootstrap complete ==="
echo "Swap:    $(swapon --show --noheadings | head -1 || echo 'none')"
echo "Slices:  claude-index, claude-lsp, claude-build, claude-vector"
echo ""
echo "Next steps:"
echo "  1. Copy .env.example to .env and set API_KEY"
echo "  2. Clone repos into /workspace/repos/<org>/<repo>"
echo "  3. docker compose up -d --build"
echo "  4. ./scripts/smoke-test.sh"
