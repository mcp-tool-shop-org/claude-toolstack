#!/usr/bin/env bash
# health.sh — Quick health check for the Claude Toolstack.
# Reports: cgroup v2, swap, PSI, slices, containers, gateway.
set -euo pipefail

echo "=== Claude Toolstack Health Check ==="
echo "Time: $(date -Iseconds)"
echo ""

# ---------------------------------------------------------------
# 1. cgroup v2
# ---------------------------------------------------------------
echo "--- cgroup ---"
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    echo "  cgroup v2: YES"
else
    echo "  cgroup v2: NO (v1 active — slices won't work correctly)"
fi

# ---------------------------------------------------------------
# 2. Swap
# ---------------------------------------------------------------
echo ""
echo "--- Swap ---"
swapon --show 2>/dev/null || echo "  No swap configured"

# ---------------------------------------------------------------
# 3. PSI (memory pressure)
# ---------------------------------------------------------------
echo ""
echo "--- Memory Pressure (PSI) ---"
if [ -f /proc/pressure/memory ]; then
    cat /proc/pressure/memory
    # Check for sustained full pressure
    FULL_AVG10=$(awk '/^full/ {print $2}' /proc/pressure/memory | sed 's/avg10=//')
    if [ "$(echo "$FULL_AVG10 > 10.0" | bc -l 2>/dev/null)" = "1" ] 2>/dev/null; then
        echo "  WARNING: Sustained memory pressure detected (full avg10=${FULL_AVG10}%)"
    else
        echo "  OK: No significant memory pressure"
    fi
else
    echo "  PSI not available (kernel too old?)"
fi

# ---------------------------------------------------------------
# 4. systemd slices
# ---------------------------------------------------------------
echo ""
echo "--- systemd Slices ---"
for slice in claude-index claude-lsp claude-build claude-vector; do
    if systemctl is-active "${slice}.slice" &>/dev/null; then
        MEM_CUR=$(cat "/sys/fs/cgroup/${slice}.slice/memory.current" 2>/dev/null || echo "?")
        MEM_HIGH=$(cat "/sys/fs/cgroup/${slice}.slice/memory.high" 2>/dev/null || echo "?")
        echo "  ${slice}: active (current=${MEM_CUR}, high=${MEM_HIGH})"
    else
        echo "  ${slice}: inactive"
    fi
done

# ---------------------------------------------------------------
# 5. Docker containers
# ---------------------------------------------------------------
echo ""
echo "--- Containers ---"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}" 2>/dev/null || echo "  Docker not available or no containers running"

# ---------------------------------------------------------------
# 6. Gateway
# ---------------------------------------------------------------
echo ""
echo "--- Gateway ---"
if curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:8088/v1/status" -H "x-api-key: ${API_KEY:-test}" 2>/dev/null | grep -q "200"; then
    echo "  Gateway: UP (200 on /v1/status)"
else
    echo "  Gateway: DOWN or auth misconfigured"
fi

# ---------------------------------------------------------------
# 7. systemd-oomd
# ---------------------------------------------------------------
echo ""
echo "--- systemd-oomd ---"
if systemctl is-active systemd-oomd &>/dev/null; then
    echo "  systemd-oomd: active"
    oomctl dump 2>/dev/null | head -10 || true
else
    echo "  systemd-oomd: not running"
fi

echo ""
echo "=== Health check complete ==="
