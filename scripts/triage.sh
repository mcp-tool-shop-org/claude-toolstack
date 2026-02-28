#!/usr/bin/env bash
# triage.sh — Quick diagnostic dump when something feels slow or wrong.
#
# Usage:
#   ./scripts/triage.sh                    # full triage
#   ./scripts/triage.sh --request-id <id>  # filter audit by request ID
#
# Prints: PSI, top containers by memory, slice usage, audit events, metrics.
set -euo pipefail

REQUEST_ID=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --request-id)
            REQUEST_ID="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: triage.sh [--request-id <id>]" >&2
            exit 1
            ;;
    esac
done

echo "=== Claude Toolstack Triage ==="
echo "Time: $(date -Iseconds)"
if [ -n "$REQUEST_ID" ]; then
    echo "Filter: request_id=$REQUEST_ID"
fi

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
# 4. Audit events (filterable by request ID)
# ---------------------------------------------------------------
echo ""

# Try to find audit log in the gw-audit volume
AUDIT_LOG=""
for candidate in \
    "/var/lib/docker/volumes/claude-toolstack_gw-audit/_data/audit.jsonl" \
    "/audit/audit.jsonl"; do
    if [ -f "$candidate" ]; then
        AUDIT_LOG="$candidate"
        break
    fi
done

if [ -n "$AUDIT_LOG" ]; then
    if [ -n "$REQUEST_ID" ]; then
        echo "--- Audit Events for request_id=$REQUEST_ID ---"
        grep "$REQUEST_ID" "$AUDIT_LOG" 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        ts = e.get('ts', '')
        typ = e.get('type', '?')
        rid = e.get('request_id', '')
        if typ == 'http':
            dur = e.get('duration_sec', 0)
            print(f'  {ts:.0f}  HTTP {e.get(\"method\",\"?\"):6s} {e.get(\"path\",\"?\"):30s} -> {e.get(\"status\",\"?\")}  ({dur:.3f}s)  rid={rid}')
        elif typ == 'docker_exec':
            print(f'  {ts:.0f}  EXEC {e.get(\"container\",\"?\")}  rid={rid}')
        elif typ == 'docker_exec_result':
            print(f'  {ts:.0f}  EXEC -> exit={e.get(\"exit_code\",\"?\")}  rid={rid}')
    except:
        pass
" 2>/dev/null || grep "$REQUEST_ID" "$AUDIT_LOG"
    else
        echo "--- Last 50 Audit Events ---"
        tail -50 "$AUDIT_LOG" | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        ts = e.get('ts', '')
        typ = e.get('type', '?')
        rid = e.get('request_id', '')[:8] if e.get('request_id') else ''
        if typ == 'http':
            dur = e.get('duration_sec', 0)
            print(f'  {ts:.0f}  HTTP {e.get(\"method\",\"?\"):6s} {e.get(\"path\",\"?\"):30s} -> {e.get(\"status\",\"?\")}  ({dur:.3f}s)  rid={rid}...')
        elif typ == 'docker_exec':
            print(f'  {ts:.0f}  EXEC {e.get(\"container\",\"?\")}  rid={rid}...')
        elif typ == 'docker_exec_result':
            print(f'  {ts:.0f}  EXEC -> exit={e.get(\"exit_code\",\"?\")}  rid={rid}...')
    except:
        pass
" 2>/dev/null || tail -50 "$AUDIT_LOG"
    fi
else
    echo "--- Audit Events ---"
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
