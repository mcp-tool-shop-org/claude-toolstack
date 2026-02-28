#!/usr/bin/env bash
# triage.sh — Quick diagnostic dump when something feels slow or wrong.
#
# Prints: PSI, top containers by memory, slice usage, last 50 audit events, metrics.
set -euo pipefail

echo "=== Claude Toolstack Triage ==="
echo "Time: $(date -Iseconds)"

# ---------------------------------------------------------------
# 1. PSI summaries
# ---------------------------------------------------------------
echo ""
echo "--- Memory Pressure (PSI) ---"
if [ -f /proc/pressure/memory ]; then
    cat /proc/pressure/memory
else
    echo "  PSI not available"
fi

echo ""
echo "--- I/O Pressure (PSI) ---"
if [ -f /proc/pressure/io ]; then
    cat /proc/pressure/io
else
    echo "  PSI not available"
fi

# ---------------------------------------------------------------
# 2. Top containers by memory
# ---------------------------------------------------------------
echo ""
echo "--- Top Containers (by memory) ---"
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" 2>/dev/null \
    | head -15 || echo "  Docker not available"

# ---------------------------------------------------------------
# 3. Slice usage
# ---------------------------------------------------------------
echo ""
echo "--- Slice Memory Usage ---"
for slice in claude-index claude-lsp claude-build claude-vector; do
    SLICE_DIR="/sys/fs/cgroup/${slice}.slice"
    if [ -d "$SLICE_DIR" ]; then
        CUR=$(cat "$SLICE_DIR/memory.current" 2>/dev/null || echo "?")
        HIGH=$(cat "$SLICE_DIR/memory.high" 2>/dev/null || echo "?")
        MAX=$(cat "$SLICE_DIR/memory.max" 2>/dev/null || echo "?")
        # Convert to human-readable
        CUR_H=$(numfmt --to=iec "$CUR" 2>/dev/null || echo "$CUR")
        echo "  ${slice}: current=${CUR_H}  high=${HIGH}  max=${MAX}"
    else
        echo "  ${slice}: not active"
    fi
done

# ---------------------------------------------------------------
# 4. Last 50 audit events
# ---------------------------------------------------------------
echo ""
echo "--- Last 50 Audit Events ---"

# Try to find audit log in the gw-audit volume
AUDIT_LOG=""
# Check common locations
for candidate in \
    "/var/lib/docker/volumes/claude-toolstack_gw-audit/_data/audit.jsonl" \
    "/audit/audit.jsonl"; do
    if [ -f "$candidate" ]; then
        AUDIT_LOG="$candidate"
        break
    fi
done

if [ -n "$AUDIT_LOG" ]; then
    tail -50 "$AUDIT_LOG" | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        ts = e.get('ts', '')
        typ = e.get('type', '?')
        if typ == 'http':
            print(f\"  {ts:.0f}  HTTP {e.get('method','?'):6s} {e.get('path','?'):30s} → {e.get('status','?')}  ({e.get('duration_sec',0):.3f}s)\")
        elif typ == 'docker_exec':
            print(f\"  {ts:.0f}  EXEC {e.get('container','?')}\")
        elif typ == 'docker_exec_result':
            print(f\"  {ts:.0f}  EXEC → exit={e.get('exit_code','?')}\")
    except:
        pass
" 2>/dev/null || tail -50 "$AUDIT_LOG"
else
    echo "  Audit log not found. Check AUDIT_LOG_PATH or docker volume."
fi

# ---------------------------------------------------------------
# 5. Gateway metrics (if reachable)
# ---------------------------------------------------------------
echo ""
echo "--- Gateway Metrics ---"
KEY="${API_KEY:-}"
if [ -z "$KEY" ] && [ -f "$(dirname "$(dirname "$0")")/.env" ]; then
    KEY=$(grep -E '^API_KEY=' "$(dirname "$(dirname "$0")")/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" 2>/dev/null || echo "")
fi

if [ -n "$KEY" ]; then
    curl -sS -H "x-api-key: $KEY" "http://127.0.0.1:8088/v1/metrics" 2>/dev/null \
        | grep -v '^#' | sed 's/^/  /' \
        || echo "  Gateway not reachable"
else
    echo "  API_KEY not set — cannot query metrics"
fi

echo ""
echo "=== Triage complete ==="
