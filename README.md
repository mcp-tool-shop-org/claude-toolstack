<p align="center">
  <strong>claude-toolstack</strong>
</p>

<p align="center">
  Docker + Claude Code workstation config for 64-GB Linux hosts.<br>
  cgroup v2 slices &bull; Compose tool farm &bull; FastAPI gateway &bull; no thrash.
</p>

<p align="center">
  <a href="https://github.com/mcp-tool-shop-org/claude-toolstack/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

---

## What This Is

A ready-to-deploy stack that keeps Claude Code productive on large, multi-language repositories without thrashing a 64-GB Linux workstation.

**Core idea:** don't load the repo into Claude. Keep durable indexes near the code in resource-governed containers. Stream only the smallest necessary evidence back to Claude through a thin HTTP gateway.

## Architecture

```
64-GB Linux host (Ubuntu 22.04 / Fedora 38)
├── systemd slices (cgroup v2 governance)
│   ├── claude-gw.slice      — gateway + socket proxy
│   ├── claude-index.slice   — indexing + search
│   ├── claude-lsp.slice     — language servers
│   ├── claude-build.slice   — build/test runners
│   └── claude-vector.slice  — vector DB (optional)
├── Docker Compose stack
│   ├── gateway         — FastAPI, 6 endpoints, 127.0.0.1:8088
│   ├── dockerproxy     — socket proxy (exec-only model)
│   ├── toolstack       — cts CLI inside the stack (cli profile)
│   ├── ctags           — universal-ctags indexer
│   └── build           — generic build runner
└── Claude Code / Claude Desktop
    └── calls gateway → gets bounded evidence
```

## Quick Start

### 1. Bootstrap the host

```bash
sudo ./scripts/bootstrap.sh
```

This installs:
- zram swap (Ubuntu) or verifies swap-on-zram (Fedora)
- Sysctl tuning (swappiness, inotify watches)
- systemd slices with MemoryHigh/Max governance
- Docker daemon config (local log driver)
- claude-toolstack.service (boot management)

### 2. Configure

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. Clone repos

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. Start the stack

```bash
docker compose up -d --build
```

### 5. Verify

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## Gateway API

All endpoints require `x-api-key` header. Gateway binds to `127.0.0.1:8088` only.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/v1/status` | Health + config |
| `POST` | `/v1/search/rg` | Ripgrep with guardrails |
| `POST` | `/v1/file/slice` | Fetch file range (max 800 lines) |
| `POST` | `/v1/index/ctags` | Build ctags index (async) |
| `POST` | `/v1/symbol/ctags` | Query symbol definitions |
| `POST` | `/v1/run/job` | Run allowlisted test/build/lint |
| `GET` | `/v1/metrics` | Prometheus-format counters |

All responses include `X-Request-ID` for end-to-end correlation. Clients can send their own via the `X-Request-ID` header.

## CLI (`cts`)

A zero-dependency Python CLI that wraps all gateway endpoints.

### Install

```bash
pip install -e .
# or: pipx install -e .
```

### Configure

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### Usage

```bash
# Gateway health
cts status

# Search (text output)
cts search "PaymentService" --repo myorg/myrepo --max 50

# Search (evidence bundle for Claude — auto-fetches context slices)
cts search "PaymentService" --repo myorg/myrepo --format claude

# File slice
cts slice --repo myorg/myrepo src/main.ts:120-180

# Symbol lookup
cts symbol PaymentService --repo myorg/myrepo

# Run tests
cts job test --repo myorg/myrepo --preset node

# Stack diagnostics
cts doctor
cts doctor --format json

# Performance knobs
cts perf
cts perf --format json

# Semantic search (default-on when store exists)
cts semantic index --repo myorg/myrepo --root /workspace/repos/myorg/myrepo
cts semantic search "what does auth do?" --repo myorg/myrepo

# All commands support: --format json|text|claude --request-id <id> --debug
```

### Evidence Bundles v2 (`--format claude`)

The `--claude` output mode produces compact, paste-ready evidence packs with structured v2 headers. Four bundle modes are available:

| Mode | Flag | What it does |
|------|------|-------------|
| `default` | `--bundle default` | Search + ranked matches + context slices |
| `error` | `--bundle error` | Stack-trace-aware: extracts files from trace, boosts in ranking |
| `symbol` | `--bundle symbol` | Definitions + call sites from search |
| `change` | `--bundle change` | Git diff + hunk context slices |

```bash
# Default bundle (search + slices)
cts search "PaymentService" --repo myorg/myrepo --format claude

# Error bundle (pass stack trace for trace-aware ranking)
cts search "ConnectionError" --repo myorg/myrepo --format claude \
  --bundle error --error-text "$(cat /tmp/traceback.txt)"

# Symbol bundle (definitions + call sites)
cts symbol PaymentService --repo myorg/myrepo --format claude --bundle symbol

# Path preferences (boost src, demote vendor)
cts search "handler" --repo myorg/myrepo --format claude \
  --prefer-paths src,core --avoid-paths vendor,test

# Git recency scoring (requires local repo access)
cts search "handler" --repo myorg/myrepo --format claude \
  --repo-root /workspace/repos/myorg/myrepo
```

Tuning: `--evidence-files 5` (files to slice), `--context 30` (lines around hit).

### curl examples

```bash
# Search
curl -sS -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"repo":"myorg/myrepo","query":"PaymentService","max_matches":50}' \
  http://127.0.0.1:8088/v1/search/rg | jq

# File slice
curl -sS -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"repo":"myorg/myrepo","path":"src/main.ts","start":120,"end":160}' \
  http://127.0.0.1:8088/v1/file/slice | jq

# Run tests
curl -sS -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"repo":"myorg/myrepo","job":"test","preset":"node"}' \
  http://127.0.0.1:8088/v1/run/job | jq
```

## Resource Governance

systemd slices enforce MemoryHigh (throttle) and MemoryMax (hard cap) per service group:

| Slice | MemoryHigh | MemoryMax | Purpose |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 GB | 4 GB | Gateway + socket proxy |
| `claude-index` | 6 GB | 10 GB | Indexers, search |
| `claude-lsp` | 8 GB | 16 GB | Language servers |
| `claude-build` | 10 GB | 18 GB | Build/test runners |
| `claude-vector` | 8 GB | 16 GB | Vector DB (optional) |

These are medium-repo defaults. Edit the slice files in `systemd/` for your workload.

OS + headroom: 10-14 GB always reserved for filesystem cache, desktop, SSH.

## Security

### Threat Model

**What we protect against:**
- Gateway abuse (unauthorized access, resource exhaustion)
- Path traversal (escaping repo root via `../` or symlinks)
- Docker socket escalation (raw socket = root-equivalent)
- Output flooding (unbounded search/build results consuming memory)

**Security layers:**

| Layer | Mechanism |
|-------|-----------|
| Auth | API key (`x-api-key` header), configurable |
| Network | Gateway binds `127.0.0.1` only |
| Docker | Socket proxy (Tecnativa), only `CONTAINERS+EXEC` |
| Repos | Allowlist/denylist with glob support |
| Paths | `realpath` jail, null byte rejection |
| Commands | Preset allowlist only, no arbitrary exec |
| Output | 512 KB cap, line truncation |
| Rate limit | Token bucket per key+ip |
| Audit | JSONL log, key hashed, rotated |
| Containers | Named allowlist, no wildcards |
| Resources | cgroup v2 slices, per-container mem/cpu limits |

### What the gateway cannot do

- Execute arbitrary commands (preset allowlist only)
- Access repos outside `/workspace/repos` (path jail)
- Touch Docker images, volumes, networks, or system (proxy blocks)
- Return unbounded output (512 KB hard cap)
- Accept connections from non-localhost (bind address)
- Collect or send telemetry — **no telemetry, no phone-home, no analytics**

## Directory Structure

```
claude-toolstack/
├── compose.yaml           # Docker Compose stack (exec-only model)
├── .env.example           # Configuration template
├── pyproject.toml         # CLI packaging (cts)
├── repos.yaml             # Declarative repo registry
├── cts/                   # CLI client (zero deps for core)
│   ├── cli.py             # argparse commands (doctor, perf, search, ...)
│   ├── errors.py          # Structured error shape (CtsError)
│   ├── http.py            # gateway HTTP client
│   ├── render.py          # json/text/claude renderers (v1+v2)
│   ├── bundle.py          # v2 bundle orchestrator (4 modes)
│   ├── ranking.py         # path scoring, trace extraction, recency
│   ├── config.py          # env + defaults
│   └── semantic/          # Embedding-based search (optional dep)
│       ├── store.py       # SQLite vector store
│       ├── search.py      # cosine similarity + narrowing
│       ├── candidates.py  # candidate selection strategies
│       └── config.py      # semantic knobs
├── tests/                 # 890+ unit tests (pytest)
├── gateway/
│   ├── main.py            # FastAPI gateway
│   ├── Dockerfile         # python:3.12-slim + ripgrep + tini
│   └── requirements.txt   # 6 dependencies
├── nginx/
│   └── gateway.conf       # Reverse proxy (optional)
├── systemd/
│   ├── claude-gw.slice    # gateway + dockerproxy (2G/4G)
│   ├── claude-index.slice # indexers + search (6G/10G)
│   ├── claude-lsp.slice   # language servers (8G/16G)
│   ├── claude-build.slice # build/test runners (10G/18G)
│   ├── claude-vector.slice
│   ├── claude-toolstack.service
│   └── ...                # zram, sysctl, daemon.json
├── scripts/
│   ├── bootstrap.sh       # Host setup (run once)
│   ├── verify.sh          # All quality gates in one command
│   ├── cts-docker         # Run cts inside Docker stack
│   ├── smoke-test.sh      # Validation suite
│   └── ...                # health, add-repo, policy-lint, triage
└── docs/
    └── tuning.md          # Slice tuning guide
```

## Claude Code Integration

### Local Linux

Claude Code runs directly on the host. Configure gateway as an MCP server or call it via HTTP from task scripts.

### Remote (macOS/Windows)

Use Claude Desktop's Code tab with an SSH environment pointing to your Linux host. The tool farm runs on the host; the GUI stays on your laptop.

## Tuning

See [docs/tuning.md](docs/tuning.md) for:
- Slice sizing by repo size (small/medium/large)
- PSI monitoring and thrash detection
- Adding language servers (clangd, rust-analyzer, tsserver)
- Vector store options (SQLite+FAISS, Weaviate, Milvus)
- Job preset customization

## No-Thrash Validation

After deployment, confirm:

1. **PSI full near zero**: `watch -n 1 'cat /proc/pressure/memory'`
2. **Containers hit MemoryHigh before Max**: check slice status
3. **SSH stays responsive**: during indexing and builds
4. **Containment works**: shrink one service's limit, run heavy task, confirm only that container dies

---

Built by <a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a>
