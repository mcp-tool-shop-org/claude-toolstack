---
title: Reference
description: Gateway API, resource governance, security model, environment variables, and directory structure.
sidebar:
  order: 3
---

This page is the technical reference for Claude ToolStack. It covers every gateway endpoint, the resource governance model, the complete security posture, all environment variables, and the project directory layout.

## Gateway API

All endpoints require the `x-api-key` header. The gateway binds to `127.0.0.1:8088` only — it is never exposed to the network.

### `GET /v1/status`

Returns gateway health and configuration, including version, active limits, and the list of allowed repos.

### `POST /v1/search/rg`

Ripgrep search with guardrails. Accepts a JSON body:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `repo` | string | required | `org/repo` identifier |
| `query` | string | required | Search pattern |
| `max_matches` | number | 200 | Bounds result count |
| `fixed_string` | boolean | false | Literal match (no regex) |
| `case_sensitive` | boolean | false | Case sensitivity |
| `path_globs` | string[] | — | File pattern filters |
| `extra_excludes` | string[] | — | Additional paths to skip |

Returns an array of matches, each with file path, line number, and content. Default excludes: `.git/`, `node_modules/`, `dist/`, `build/`, `target/`, `.next/`, `.turbo/`, `.cache/`, `vendor/`.

### `POST /v1/file/slice`

Fetch a range of lines from a file. Maximum 800 lines per request.

| Field | Type | Purpose |
|-------|------|---------|
| `repo` | string | `org/repo` identifier |
| `path` | string | File path relative to repo root |
| `start` | number | First line (1-indexed) |
| `end` | number | Last line (inclusive) |

### `POST /v1/index/ctags`

Trigger a ctags index build for a repo. This is asynchronous with a 600-second timeout. Returns exit code, stdout, and stderr.

### `POST /v1/symbol/ctags`

Query symbol definitions from the ctags index. Returns an array of objects with `name`, `file`, `excmd`, and `kind` fields.

### `POST /v1/run/job`

Run an allowlisted build, test, or lint preset. Only preset commands execute — there is no arbitrary exec.

| Field | Type | Purpose |
|-------|------|---------|
| `repo` | string | `org/repo` identifier |
| `job` | string | `test`, `build`, or `lint` |
| `preset` | string | `node`, `python`, `rust`, or `go` |

### `GET /v1/metrics`

Prometheus-format counters for monitoring. Tracks total requests, rate-limit 429s, docker exec calls and errors, truncations, and per-endpoint totals.

### Response conventions

All responses include an `X-Request-ID` header for end-to-end correlation. When a response exceeds the 512 KB cap, the gateway truncates it and sets `truncated: true` in the JSON body.

## Resource governance

systemd cgroup v2 slices enforce per-category memory budgets. Each slice has a `MemoryHigh` (soft limit — triggers reclaim pressure) and `MemoryMax` (hard limit — OOM kills the offending process, not your session).

| Slice | MemoryHigh | MemoryMax | Purpose |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 GB | 4 GB | Gateway + socket proxy |
| `claude-index` | 6 GB | 10 GB | Indexers + search |
| `claude-lsp` | 8 GB | 16 GB | Language servers |
| `claude-build` | 10 GB | 18 GB | Build/test runners |
| `claude-vector` | 8 GB | 16 GB | Vector DB (optional) |

### How governance works

1. Docker containers are assigned to slices via `systemd.slice` in the Compose config
2. When a container approaches `MemoryHigh`, the kernel applies reclaim pressure — the process slows but stays alive
3. If the container hits `MemoryMax`, the kernel OOM-kills it — only the offending container dies, not your SSH session or other tools
4. zram swap (LZ4-compressed) provides additional headroom for compressible data like build artifacts

### Tuning for different hosts

The defaults assume 64 GB RAM. For smaller or larger hosts, adjust the slice files in `/etc/systemd/system/`:

```ini
# Example: reduce index slice for a 32 GB host
[Slice]
MemoryHigh=3G
MemoryMax=5G
```

After editing, reload systemd: `sudo systemctl daemon-reload`

## Security model

ToolStack uses defense-in-depth with multiple independent layers.

### Path jail

All file access goes through `realpath` validation:

- Repo paths must resolve to `/workspace/repos/<org>/<repo>`
- Null bytes are rejected
- Symlinks that escape the jail are rejected
- Allow/deny lists use glob patterns from the `ALLOWED_REPOS` and `DENIED_REPOS` variables
- Deny rules always take precedence

### Docker socket proxy

The Docker socket is never exposed directly to any container. A Tecnativa proxy sits between the gateway and the Docker daemon, filtering API calls:

**Allowed:** Container inspect, container exec (how the gateway delegates work to tool containers)

**Denied:** Image pull/push/build, volume create/remove, network create/remove, system info, and 14 other higher-risk endpoints. The proxy operates on an explicit allowlist — anything not listed is denied by default.

### Rate limiting

Token-bucket rate limiting per `(api_key, ip)` pair. Default: 60 requests per minute with a burst of 10. Exceeding the limit returns HTTP 429 with a `Retry-After` header.

### Audit logging

All gateway requests are logged to a JSONL audit file with:

- Timestamp, endpoint, method, status code
- API key hash (SHA-256, never the raw key)
- Request ID for correlation
- Response size and truncation status

Audit logs rotate automatically by size (100 MB default) and age (30 days).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | (required) | Gateway authentication key |
| `ALLOWED_REPOS` | `""` (deny all) | Comma-separated glob patterns for repo access |
| `DENIED_REPOS` | `""` | Explicit deny patterns (checked first) |
| `MAX_MATCHES` | `200` | Maximum ripgrep matches per search |
| `MAX_RESPONSE_BYTES` | `524288` (512 KB) | Hard cap on response payload size |
| `MAX_FILE_SLICE` | `800` | Maximum lines per file slice |
| `CTAGS_TIMEOUT` | `600` | Ctags index build timeout (seconds) |
| `RATE_LIMIT_RPM` | `60` | Requests per minute per key+ip |
| `RATE_LIMIT_BURST` | `10` | Burst allowance above steady rate |
| `SEMANTIC_MODEL` | `all-MiniLM-L6-v2` | Embedding model for semantic search |
| `BIND_HOST` | `127.0.0.1` | Gateway bind address |
| `BIND_PORT` | `8088` | Gateway port |
| `LOG_LEVEL` | `info` | Logging verbosity |

## Directory structure

```
claude-toolstack/
  compose.yaml            # Docker Compose — all services + profiles
  .env.example            # Template for environment configuration
  Dockerfile.*            # Per-container build files
  gateway/
    main.py               # FastAPI app — route definitions
    search.py             # Ripgrep search with guardrails
    file_slice.py         # Bounded file access
    ctags.py              # Index build + symbol query
    job_runner.py         # Allowlisted preset execution
    security.py           # Path jail, rate limiting, audit
    truncate.py           # 512 KB response cap
    evidence/             # Bundle assembly (default, error, symbol, change)
    semantic/             # Embedding index + search
  cli/
    cts.py                # CLI entry point
    commands/             # One module per command (search, slice, symbol, etc.)
    formatters/           # Output formatters (text, json, claude)
  scripts/
    bootstrap.sh          # One-time host setup
    smoke-test.sh         # Endpoint verification
    health.sh             # Broader health checks
  slices/                 # systemd slice unit files
  site/                   # Astro landing page + handbook
```
