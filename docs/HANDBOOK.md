# Claude ToolStack Handbook

> The complete operational reference for Claude ToolStack.
> For quick-start instructions, see [README.md](../README.md).
> For slice sizing tables, see [tuning.md](tuning.md).

---

## 1. Introduction

### 1.1 What Claude ToolStack Is

Claude ToolStack is a Linux-first execution environment for Claude Code. It keeps Claude productive on large, multi-language repositories without thrashing a 64-GB workstation.

The architecture has three layers:

1. **Gateway** — A FastAPI server (6 endpoints, port 8088) that accepts bounded queries and returns bounded evidence.
2. **Tool Farm** — Long-running Docker containers (ctags indexer, build runner, optional LSPs) that the gateway delegates to via `docker exec`.
3. **Resource Governance** — systemd cgroup v2 slices that enforce per-category memory budgets so your SSH session stays responsive during builds.

The core contract: durable indexes live near the code in resource-governed containers. Only the smallest necessary evidence streams back to Claude through the gateway.

### 1.2 What It Is Not

- **Not a cloud service.** The gateway binds to `127.0.0.1` only. There is no hosted version, no account system, no SaaS.
- **Not a general Docker orchestrator.** The Docker model is exec-only — no container creation, no image pulling, no volume manipulation at runtime.
- **Not a standalone search engine.** Semantic search augments lexical results when store exists, but the system is designed to serve Claude, not replace ripgrep.
- **Not a replacement for IDE tooling.** Language servers run in optional containers, but the primary consumer is Claude Code, not your editor.

### 1.3 Core Principles

| Principle | What It Means |
|-----------|---------------|
| **Bounded outputs** | Every gateway response is capped at 512 KB. Every file slice is capped at 800 lines. No unbounded streams. |
| **Measured improvements** | New features ship with A/B evidence. Semantic search, candidate narrowing, and autopilot were all validated against KPI baselines before becoming defaults. |
| **Exec-only Docker** | The gateway never creates containers, pulls images, or manipulates volumes. It execs into pre-existing long-running containers. |
| **Resource governance** | systemd slices enforce MemoryHigh (throttle) and MemoryMax (hard kill) per service category. The OS + desktop always has 10–14 GB reserved. |
| **Safe by default** | No telemetry. No phone-home. No analytics. No arbitrary command execution. Preset allowlist only. Path jail to `/workspace/repos`. |

---

## 2. Architecture Overview

### 2.1 High-Level Diagram

```
64-GB Linux host (Ubuntu 22.04 / Fedora 38)
├── systemd slices (cgroup v2 governance)
│   ├── claude-gw.slice      — gateway + socket proxy         (2G/4G)
│   ├── claude-index.slice   — indexers + search               (6G/10G)
│   ├── claude-lsp.slice     — language servers                 (8G/16G)
│   ├── claude-build.slice   — build/test runners              (10G/18G)
│   └── claude-vector.slice  — vector DB (optional)            (8G/16G)
├── Docker Compose stack
│   ├── gateway         — FastAPI, 6 endpoints, 127.0.0.1:8088
│   ├── dockerproxy     — socket proxy (exec-only model)
│   ├── toolstack       — cts CLI inside the stack (cli profile)
│   ├── ctags           — universal-ctags indexer
│   ├── build           — generic build runner
│   ├── [lsp profile]   — clangd, rust-analyzer, tsserver, pylsp, gopls
│   └── [remote profile]— nginx reverse proxy (TLS/mTLS)
└── Claude Code / Claude Desktop
    └── calls gateway → gets bounded evidence
```

### 2.2 Execution Flow

A typical query flows through these stages:

1. **User query** — Claude sends a search request to the gateway via HTTP or the `cts` CLI.
2. **Gateway search** — Ripgrep runs against the repo with guardrails (max matches, excludes, timeout).
3. **Ranking** — Results are scored using path preference, structural signals (ctags definitions, export detection), git recency, and optional stack trace boosting.
4. **Autopilot refinement** — If `--autopilot N` is enabled, the system iterates up to N passes (default 2, max 30 seconds), refining the query if confidence is below the gate threshold (0.45).
5. **Semantic fallback** — When a semantic store exists and the initial results are high-hit/low-quality or sparse, embedding-based search augments the lexical results with up to 4 additional slices.
6. **Evidence bundle** — The final ranked sources, context slices, and metadata are assembled into a v2 bundle (JSON or rendered text) and returned within the 512 KB cap.

### 2.3 Tool Farm Model

The tool farm uses a **long-running container** pattern:

- Containers start with `docker compose up -d` and stay running.
- The gateway uses `docker exec` to invoke tools inside containers (e.g., `ctags --output-format=json` inside `claude-ctags`).
- No container is created, destroyed, or pulled at request time.
- The Docker socket is never exposed directly — all access goes through `tecnativa/docker-socket-proxy` with 6 explicit allows and 14 explicit denials.

This model is simpler, faster, and more predictable than ephemeral container spawning. Container state (ctags indexes, build caches) persists across requests.

---

## 3. Installation

### 3.1 Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Linux kernel | 5.x with cgroup v2 | Ubuntu 22.04+ / Fedora 38+ |
| Docker Engine | 24.0+ | Latest stable |
| systemd | 250+ | Latest |
| RAM | 16 GB | 64 GB |
| Python | 3.10+ | 3.12 |
| ripgrep (`rg`) | 13.0+ | Latest |

cgroup v2 is **required**. Verify with:

```bash
cat /sys/fs/cgroup/cgroup.controllers
# Should list: cpuset cpu io memory hugetlb pids
```

### 3.2 Quick Start

```bash
# 1. Clone
git clone https://github.com/mcp-tool-shop-org/claude-toolstack
cd claude-toolstack

# 2. Bootstrap host (one-time, requires root)
sudo ./scripts/bootstrap.sh

# 3. Configure
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS

# 4. Clone your repos
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo

# 5. Start the stack
docker compose up -d --build

# 6. Verify
cts doctor
```

### 3.3 What `bootstrap.sh` Does

The bootstrap script runs once as root and configures:

1. **cgroup v2 verification** — checks `/sys/fs/cgroup/cgroup.controllers` exists.
2. **zram swap** — installs `zram-generator` (Ubuntu) or verifies swap-on-zram (Fedora) for memory pressure resilience.
3. **Sysctl tuning** — installs `99-claude-dev.conf` (kernel parameters) and `99-inotify-large-repos.conf` (expanded inotify watches for large repos).
4. **systemd slices** — copies 5 slice files (`claude-gw`, `claude-index`, `claude-lsp`, `claude-build`, `claude-vector`) to `/etc/systemd/system/`.
5. **Docker daemon config** — sets log driver to `local` (bounded, efficient) via `daemon.json`.
6. **systemd-oomd** — ensures the out-of-memory daemon is enabled for PSI-based early intervention.
7. **Workspace directory** — creates `/workspace/repos` if missing.
8. **Boot service** — installs `claude-toolstack.service` for automatic startup.

### 3.4 Verifying Setup with `cts doctor`

```bash
cts doctor
```

The doctor command runs sequential health checks:

| Check | What It Tests | Marker |
|-------|---------------|--------|
| Repo root | repos.yaml + `.git` present | `[+]` pass / `[!]` fail |
| Ripgrep | `shutil.which("rg")` + version string | `[+]` / `[!]` |
| Python deps | numpy, sentence-transformers (optional) | `[+]` / `[~]` warn |
| Gateway | `GET /v1/status` connectivity | `[+]` / `[!]` |
| Semantic stores | scans `gw-cache/*/semantic.sqlite3` | `[+]` / `[~]` |
| Docker host | `DOCKER_HOST` env variable set | `[+]` / `[~]` |
| Docker proxy | `GET /_ping` on proxy | `[+]` / `[!]` |
| Tool containers | expected containers running | `[+]` / `[~]` |

Exit code 1 on any `[!]` failure. `--format json` for programmatic consumption.

---

## 4. Core Commands

### 4.1 Search

```bash
# Basic text search
cts search "PaymentService" --repo myorg/myrepo --max 50

# Evidence bundle for Claude
cts search "PaymentService" --repo myorg/myrepo --format claude

# With autopilot (up to 2 refinement passes, 30s budget)
cts search "auth handler" --repo myorg/myrepo --format claude --autopilot 2
```

**Key flags:**

| Flag | Default | Purpose |
|------|---------|---------|
| `--max` | 200 | Maximum ripgrep matches |
| `--format` | text | Output: `text`, `json`, `claude`, `sidecar` |
| `--bundle` | default | Bundle mode: `default`, `error`, `symbol`, `change` |
| `--evidence-files` | 5 | Files to include context slices for |
| `--context` | 30 | Lines of context around each match |
| `--fixed-string` | off | Literal match (no regex) |
| `--case-sensitive` | off | Case-sensitive matching |
| `--prefer-paths` | — | Comma-separated path segments to boost (e.g., `src,core`) |
| `--avoid-paths` | — | Comma-separated path segments to demote (e.g., `vendor,test`) |
| `--repo-root` | — | Local repo path for git recency scoring |
| `--debug-bundle` | off | Include `_debug` telemetry in bundle |
| `--explain-top` | 10 | Number of score cards in debug output |

### 4.2 Error Debugging

The `error` bundle mode is stack-trace-aware. Pass the error text and the system extracts file paths from the trace, boosts those files in ranking, and assembles slices around the crash site:

```bash
cts search "ConnectionError" --repo myorg/myrepo --format claude \
  --bundle error --error-text "$(cat /tmp/traceback.txt)"
```

The trace extractor recognizes Python tracebacks, Node.js stacks, Go panics, and Java/C# exception chains. Files mentioned in the trace receive a `+2.0` ranking boost.

### 4.3 Symbol Lookup

The `symbol` bundle finds definitions + call sites:

```bash
# Direct symbol query
cts symbol PaymentService --repo myorg/myrepo

# Symbol bundle with call site context
cts symbol PaymentService --repo myorg/myrepo --format claude --bundle symbol
```

This uses ctags for definitions and ripgrep for call sites, then ranks by structural signals (definition vs export vs caller proximity).

### 4.4 Semantic Indexing and Search

Semantic search uses embedding-based similarity to find conceptually related code, not just lexical matches.

```bash
# Index a repo (builds SQLite vector store)
cts semantic index --repo myorg/myrepo --root /workspace/repos/myorg/myrepo

# Semantic search
cts semantic search "what does the auth middleware do?" --repo myorg/myrepo
```

Semantic search is **default-on** when a store exists for the repo. It activates as a fallback when lexical results are high-hit/low-quality or sparse.

**Model:** `sentence-transformers/all-MiniLM-L6-v2` (default). Override with `CTS_SEMANTIC_MODEL`.

### 4.5 File Slicing

Fetch a specific range of lines from a file:

```bash
cts slice --repo myorg/myrepo src/main.ts:120-180
```

Maximum 800 lines per slice. Used internally by bundles to fetch context around matches.

### 4.6 Job Runner

Run allowlisted build/test/lint presets:

```bash
cts job test --repo myorg/myrepo --preset node
cts job build --repo myorg/myrepo --preset python
cts job lint --repo myorg/myrepo --preset node
```

Only preset commands execute. No arbitrary exec.

### 4.7 Performance Inspection

```bash
cts perf
```

Displays all tunable knobs with current values, their environment variables, and whether each value comes from env override or default:

```
Name                        Value         Env Var                              Source
─────────────────────────── ───────────── ──────────────────────────────────── ──────
chunk_lines                 180           CTS_SEMANTIC_CHUNK_LINES             default
overlap_lines               30            CTS_SEMANTIC_OVERLAP_LINES           default
topk_chunks                 8             CTS_SEMANTIC_TOPK                    default
max_slices                  4             CTS_SEMANTIC_MAX_SLICES              default
max_seconds                 4.0           CTS_SEMANTIC_MAX_SECONDS             default
candidate_strategy          exclude_top_k CTS_SEMANTIC_CANDIDATE_STRATEGY      default
candidate_exclude_top_k     10            CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K default
...
```

Values overridden via environment get a `*` marker. `--format json` for scripts.

---

## 5. Performance Model

### 5.1 Autopilot

Autopilot iteratively refines search results when initial quality is insufficient:

```bash
cts search "auth" --repo myorg/myrepo --format claude --autopilot 2
```

**How it works:**

1. Run the initial search and score confidence.
2. If confidence < `CTS_SEMANTIC_CONFIDENCE_GATE` (default: 0.45), refine the query and search again.
3. Repeat up to `--autopilot N` passes (default: 2), or until `--autopilot-max-seconds` (default: 30s) expires.
4. Each pass may add up to `--autopilot-max-extra-slices` (default: 5) additional context slices.
5. Low-lift detection: if the first pass already covers the query well, subsequent passes are skipped.

**Confidence scoring factors:**

| Signal | Weight | Description |
|--------|--------|-------------|
| Top score | variable | Best match score contribution |
| Definition found | bonus | Probable definition in results |
| Source diversity | bonus | Distinct files >= 3 |
| Slice coverage | bonus | Context slices fetched |
| Low match penalty | penalty | Fewer than 3 matches reduces confidence |
| Mode-specific | 0–0.15 | Trace coverage (error), def/caller (symbol), hunk overlap (change) |

Confidence thresholds: `MIN_MATCHES_FOR_CONFIDENCE = 3`, `MIN_TOP_SCORE = 0.5`, `DIVERSE_FILE_COUNT = 3`, `SUFFICIENT_THRESHOLD = 0.6`.

### 5.2 Semantic Fallback

Semantic search activates automatically when a vector store exists for the repo. It is not a replacement for lexical search — it's a bounded fallback that fires under specific conditions:

**Branch A — Sparse lexical results:** Too few matches from ripgrep. Semantic adds embeddings-based matches that lexical search missed entirely.

**Branch B — High-hit, low-quality:** Many matches but low confidence (lots of vendor files, test fixtures, etc.). Semantic search surfaces conceptually relevant code that isn't a lexical match.

**Constraints:**
- Maximum 4 additional semantic slices per query (`CTS_SEMANTIC_MAX_SLICES`)
- 4-second wall-clock budget (`CTS_SEMANTIC_MAX_SECONDS`)
- Uses cosine similarity over L2-normalized embeddings
- Pure Python dot products; numpy vectorized fallback when available

**A/B validation:** Semantic augmentation was validated across 80 test cases. Results: 80/80 IMPROVED, 4/7 KPIs improved, 0 regressed.

### 5.3 Candidate Narrowing

Narrowing reduces the search space for semantic queries by excluding files that lexical search already ranked highly:

**Strategy: `exclude_top_k` (default)**

1. Take the top K lexically-ranked files (default K=10, env: `CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K`).
2. Exclude those files from semantic search.
3. Search the remaining files for semantically similar chunks.
4. This forces semantic search to surface *new* information, not redundant matches.

**Fallback:** If narrowing yields zero results, falls back to `global_tight` — searches all files with a tighter top-K constraint.

**Strategy: `none`** — No narrowing. Searches all files. Use this if semantic results seem too sparse.

**Configuration:**

| Env Var | Default | Purpose |
|---------|---------|---------|
| `CTS_SEMANTIC_CANDIDATE_STRATEGY` | `exclude_top_k` | Narrowing strategy |
| `CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K` | 10 | Files to exclude |
| `CTS_SEMANTIC_CANDIDATE_MAX_FILES` | 200 | Max files to consider |
| `CTS_SEMANTIC_CANDIDATE_MAX_CHUNKS` | 20,000 | Max chunks to consider |
| `CTS_SEMANTIC_CANDIDATE_FALLBACK` | `global_tight` | Fallback when narrowing yields nothing |

**A/B validation:** Narrowing was validated separately. No regressions observed vs unfiltered semantic search, with measurable latency improvement.

### 5.4 Query Embedding Cache

Query embeddings are cached with `@functools.lru_cache(maxsize=64)`. Repeated identical queries (common in autopilot iterations) skip the embedding model entirely.

Cache hit heuristic in debug output: `query_embed_cached: true` when embedding time < 1ms.

---

## 6. Docker Execution Model

### 6.1 Exec-Only Design

The gateway uses Docker exclusively through `docker exec`:

```
Gateway → docker exec claude-ctags ctags --output-format=json ...
Gateway → docker exec claude-build npm test ...
```

**What the gateway can do:**
- Execute commands inside pre-existing named containers
- Read container logs
- List containers and inspect status
- Health-check via `/_ping`

**What the gateway cannot do:**
- Create, start, stop, or remove containers
- Pull, push, or build images
- Create, mount, or remove volumes
- Create or modify networks
- Access swarm, secrets, configs, plugins, or system endpoints

### 6.2 Docker Socket Proxy Configuration

The `tecnativa/docker-socket-proxy` filters Docker API access:

**Allowed (6):**

| Capability | Purpose |
|------------|---------|
| `CONTAINERS=1` | `GET /containers`, `/containers/{id}/json` |
| `EXEC=1` | `POST /containers/{id}/exec`, `POST /exec/{id}/start` |
| `POST=1` | Required for exec create/start |
| `LOGS=1` | `GET /containers/{id}/logs` |
| `INFO=1` | `GET /info` (used by `cts doctor`) |
| `PING=1` | `GET /_ping` (health checks) |

**Denied (14):**

`IMAGES`, `BUILD`, `NETWORKS`, `SERVICES`, `TASKS`, `SWARM`, `NODES`, `VOLUMES`, `PLUGINS`, `EVENTS`, `SYSTEM`, `AUTH`, `SECRETS`, `CONFIGS`

All set to `0` explicitly. Any request to these endpoints returns 403 at the proxy level, before reaching the Docker daemon.

### 6.3 Tool Containers

| Container | Image | Memory | CPUs | Slice | Purpose |
|-----------|-------|--------|------|-------|---------|
| `claude-gateway` | Built from `./gateway` | 1 GB | 2 | `claude-gw` | FastAPI gateway |
| `claude-dockerproxy` | `tecnativa/docker-socket-proxy` | — | — | `claude-gw` | Socket proxy |
| `claude-ctags` | `universalctags/ctags` | 4 GB | 4 | `claude-index` | Symbol indexer |
| `claude-build` | `ubuntu:24.04` | 12 GB | 8 | `claude-build` | Build/test runner |
| `claude-toolstack` | Built from `.` | — | — | `claude-gw` | CLI inside stack |

**Optional LSP containers** (enable with `--profile lsp`):

| Container | Memory | CPUs | Slice |
|-----------|--------|------|-------|
| `claude-clangd` | 10 GB | 6 | `claude-lsp` |
| `claude-rust-analyzer` | 6 GB | 4 | `claude-lsp` |
| `claude-tsserver` | 4 GB | 4 | `claude-lsp` |
| `claude-pylsp` | 4 GB | 4 | `claude-lsp` |
| `claude-gopls` | 4 GB | 4 | `claude-lsp` |

**Optional reverse proxy** (enable with `--profile remote`):

| Container | Port | Memory | Purpose |
|-----------|------|--------|---------|
| `nginx` | `0.0.0.0:8443` | 256 MB | TLS/mTLS termination for remote access |

### 6.4 Repo Mount Model

| Host Path | Container Path | Access | Who |
|-----------|---------------|--------|-----|
| `/workspace/repos` | `/repos` | read-only | gateway, ctags |
| `/workspace/repos` | `/workspace` | read-write | build runner |
| `gw-cache` volume | `/cache` | read-write | gateway |
| local `audit/` | `/audit` | read-write | gateway |

The build runner gets read-write access because `npm install`, `pip install`, and build tools need to write to the repo directory. The gateway and ctags only need to read source code.

---

## 7. Linux Resource Governance

### 7.1 systemd Slices

Each service category runs under a dedicated systemd slice with cgroup v2 resource limits:

| Slice | MemoryHigh | MemoryMax | MemorySwapMax | IOWeight |
|-------|-----------|-----------|---------------|----------|
| `claude-gw.slice` | 2 GB | 4 GB | 1 GB | 90 |
| `claude-index.slice` | 6 GB | 10 GB | 4 GB | 50 |
| `claude-lsp.slice` | 8 GB | 16 GB | 8 GB | 60 |
| `claude-build.slice` | 10 GB | 18 GB | 8 GB | 70 |
| `claude-vector.slice` | 8 GB | 16 GB | 4 GB | 80 |

These are **medium-repo defaults** (50k–300k lines). See [tuning.md](tuning.md) for small and large profiles.

Docker containers are assigned to slices via `cgroup_parent` in `compose.yaml`:

```yaml
services:
  gateway:
    cgroup_parent: "claude-gw.slice"
  ctags:
    cgroup_parent: "claude-index.slice"
  build:
    cgroup_parent: "claude-build.slice"
```

### 7.2 MemoryHigh vs MemoryMax

These two thresholds work together:

- **MemoryHigh** is the *throttle point*. When a slice exceeds MemoryHigh, the kernel reclaims pages aggressively (swaps, drops caches) but does *not* kill processes. This causes slowdown, not crashes.
- **MemoryMax** is the *hard cap*. When a slice exceeds MemoryMax, the OOM killer terminates processes in the slice.

**Why MemoryHigh is primary:** In practice, services should oscillate around MemoryHigh. If they consistently hit MemoryMax, either the workload is too large for the slice or the slice needs resizing. MemoryHigh provides graceful degradation; MemoryMax is the emergency backstop.

The gap between MemoryHigh and MemoryMax (typically 2×) gives the kernel room to reclaim before killing. Too small a gap means the system jumps from "fine" to "OOM" with no warning.

### 7.3 IOWeight

IOWeight controls I/O bandwidth priority (1–10000 scale, default 100):

| Slice | IOWeight | Rationale |
|-------|----------|-----------|
| `claude-gw` | 90 | Gateway is latency-sensitive — I/O priority keeps responses fast |
| `claude-index` | 50 | Indexing is bulk I/O but not latency-critical |
| `claude-lsp` | 60 | LSPs need responsive I/O for code navigation |
| `claude-build` | 70 | Builds are I/O-heavy but can tolerate some queuing |
| `claude-vector` | 80 | Embedding lookups benefit from fast reads |

Lower IOWeight means the slice yields I/O bandwidth to higher-priority slices during contention. When there's no contention, all slices get full bandwidth.

### 7.4 zram and oomd

**zram swap** compresses memory pages in-place using LZ4, effectively expanding usable memory by 2–3× for compressible data. This is particularly useful for build artifacts, indexer state, and language server caches.

The bootstrap script installs `zram-generator` (Ubuntu) or verifies the existing swap-on-zram (Fedora).

**systemd-oomd** is a userspace OOM daemon that uses PSI (Pressure Stall Information) signals to proactively intervene before the kernel OOM killer fires. It's less aggressive and more predictable than the kernel OOM killer.

Both are recommended but optional. The slices enforce hard limits regardless.

---

## 8. Security Model

### 8.1 Repo Allowlist

Access is controlled via `ALLOWED_REPOS` and `DENIED_REPOS` in `.env`:

```bash
ALLOWED_REPOS=myorg/*          # Allow all repos in myorg
DENIED_REPOS=myorg/secrets     # Explicitly deny one repo
```

Deny rules are checked first. Glob patterns supported. If `ALLOWED_REPOS` is empty, all repos are denied by default.

### 8.2 Path Jail

Every file path is validated through a `realpath` jail:

1. The requested path is resolved to its absolute canonical form (following symlinks).
2. The resolved path must start with `REPO_ROOT` (`/repos` by default).
3. Null bytes (`\x00`) in paths are rejected outright.
4. `../` traversal is neutralized by `realpath` resolution.

A symlink inside `/repos` that points outside `/repos` is blocked because `realpath` resolves to the true target, which fails the prefix check.

### 8.3 Preset-Only Commands

The job runner only executes commands from a predefined allowlist. Presets are defined in `repos.yaml`:

```yaml
repos:
  myorg/myrepo:
    preset: node
```

The gateway resolves presets to fixed command templates. There is no shell interpolation, no user-supplied command strings, no `--exec` flag.

Allowed containers are also whitelisted via `ALLOWED_CONTAINERS` (default: `claude-ctags,claude-build`).

### 8.4 Output Caps

| Cap | Value | Purpose |
|-----|-------|---------|
| Max response size | 512 KB (`MAX_RESPONSE_BYTES`) | Prevents memory exhaustion from large search results |
| Max search matches | 200 (`MAX_MATCHES`) | Bounds ripgrep output |
| Max file slice | 800 lines | Prevents full-file dumps |
| Line truncation | applied | Long lines are cut before transmission |
| Request timeout | 20s (`REQUEST_TIMEOUT_SEC`) | Prevents runaway operations |
| Snippet truncation | 200 chars (`MAX_SNIPPET_LEN`) | Bounds inline snippets in bundles |

When a response would exceed 512 KB, the gateway truncates it and sets `truncated: true` in the response.

### 8.5 Audit Logs and Request IDs

Every gateway request is logged to JSONL:

```json
{
  "type": "request",
  "ip": "127.0.0.1",
  "key": "sha256:a1b2c3...",
  "method": "POST",
  "path": "/v1/search/rg",
  "status": 200,
  "duration_sec": 0.342,
  "ts": "2026-03-01T12:00:00Z",
  "request_id": "req_abc123"
}
```

API keys are **hashed** (SHA-256) in logs — the plaintext key is never persisted.

All responses include an `X-Request-ID` header for end-to-end correlation. Clients can send their own ID via the same header.

Log rotation: `AUDIT_LOG_MAX_MB=50` per file, `AUDIT_LOG_BACKUPS=5` backup files.

### 8.6 Docker API Restrictions

See [Section 6.2](#62-docker-socket-proxy-configuration) for the full allowlist/denylist. The key security properties:

- **No image access** — can't pull malicious images or inspect image contents.
- **No volume manipulation** — can't mount host paths beyond what Compose defines.
- **No network control** — can't create networks, bridge to host, or exfiltrate data.
- **No system access** — can't read Docker daemon config, system info beyond `/_ping` and `/info`.

### 8.7 Threat Model Summary

**Gateway compromise scenario:** If an attacker gains control of the gateway process, they can:
- Read source code in allowed repos (read-only)
- Execute preset commands in tool containers
- Read/write the cache volume and audit logs

**What the attacker cannot do:**
- Access repos outside the allowlist
- Execute arbitrary commands (preset-only)
- Escalate to host via Docker (socket proxy blocks)
- Access the host filesystem outside mounts
- Reach the network (gateway is localhost-only)
- Disable audit logging (append-only at the application layer)

---

## 9. Tuning Guide

### 9.1 If Search Is Slow

| Symptom | Fix | How |
|---------|-----|-----|
| Ripgrep is slow on large repos | Increase RG threads | Set `RG_THREADS=8` in `.env` |
| Search queues behind builds | Increase concurrency | Set `RG_CONCURRENCY=4` in `.env` |
| Index slice is OOM-killed | Increase slice memory | Edit `claude-index.slice`: `MemoryHigh=10G`, `MemoryMax=16G` |
| Too many matches slow ranking | Reduce max matches | Set `MAX_MATCHES=100` in `.env` |
| Excludes not filtering enough | Add path exclusions | Add to default excludes in gateway or use `--extra-excludes` |

### 9.2 If Semantic Search Is Slow

| Symptom | Fix | How |
|---------|-----|-----|
| Embedding model loads slowly | Use CPU with warm cache | Queries after first use the LRU cache (64 entries) |
| Too many chunks to search | Enable narrowing | `CTS_SEMANTIC_CANDIDATE_STRATEGY=exclude_top_k` (default) |
| Narrowing too aggressive | Increase exclude count | `CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K=20` |
| Too many slices slow response | Reduce max slices | `CTS_SEMANTIC_MAX_SLICES=2` |
| Total time too high | Reduce time budget | `CTS_SEMANTIC_MAX_SECONDS=2.0` |
| Debug shows high `semantic_time_ms` | Check model/device | Try `CTS_SEMANTIC_DEVICE=cpu` (GPU overhead isn't worth it for small models) |

### 9.3 If Builds Thrash

| Symptom | Fix | How |
|---------|-----|-----|
| SSH unresponsive during builds | Lower IOWeight | Edit `claude-build.slice`: `IOWeight=40` |
| Build OOMs with MemoryMax kill | Increase slice or reduce parallelism | `MemoryHigh=14G, MemoryMax=24G` or reduce `--jobs` in build preset |
| Desktop freezes | Reserve more headroom | Reduce all slice MemoryMax values by 2 GB each |
| PSI `full avg10 > 5.0` | System is overcommitted | Reduce the largest slice's MemoryHigh first |

### 9.4 If Bundles Truncate

| Symptom | Fix | How |
|---------|-----|-----|
| Bundle shows `truncated: true` | Increase max response | `MAX_RESPONSE_BYTES=1048576` (1 MB) in `.env` |
| Too many slices | Reduce evidence files | `--evidence-files 3` |
| Context too wide | Reduce context lines | `--context 15` |
| Use focused bundle mode | Switch to symbol or change | `--bundle symbol` or `--bundle change` produce smaller, more targeted output |

---

## 10. Troubleshooting

### 10.1 `cts doctor` Failures Explained

| Check | Failure | Fix |
|-------|---------|-----|
| Repo root | `[!] Repo root not found` | Ensure `/workspace/repos` exists and repos.yaml is present |
| Ripgrep | `[!] rg not found` | Install ripgrep: `apt install ripgrep` or `dnf install ripgrep` |
| Python deps | `[~] numpy not available` | `pip install numpy` (optional, improves semantic perf) |
| Python deps | `[~] sentence-transformers not available` | `pip install sentence-transformers` (needed for semantic indexing) |
| Gateway | `[!] Gateway unreachable` | Check `docker compose ps` — is `claude-gateway` running? Check port 8088. |
| Semantic stores | `[~] No semantic stores found` | Run `cts semantic index --repo myorg/myrepo --root /path/to/repo` |
| Docker host | `[~] DOCKER_HOST not set` | Set `DOCKER_HOST=tcp://localhost:2375` if running outside Docker |
| Docker proxy | `[!] Docker proxy unreachable` | Check `docker compose ps` — is `claude-dockerproxy` running? |
| Tool containers | `[~] Expected containers not running` | Run `docker compose up -d` to start all services |

### 10.2 Docker Proxy Unreachable

```
[!] Docker proxy unreachable at tcp://dockerproxy:2375
```

**If running `cts` on the host (not inside Docker):**
- The proxy is only accessible from within the Docker network.
- Either use `scripts/cts-docker` (runs cts inside the toolstack container), or set `DOCKER_HOST` to the proxy's published port (if exposed).

**If running inside Docker:**
- Check that `dockerproxy` container is running: `docker compose ps dockerproxy`
- Check the `tools-net` network exists: `docker network ls | grep tools`
- Verify the proxy env vars match compose.yaml

### 10.3 Semantic Store Missing

```
[~] No semantic stores found in gw-cache/
```

Semantic search requires a pre-built index:

```bash
cts semantic index --repo myorg/myrepo --root /workspace/repos/myorg/myrepo
```

This creates `gw-cache/myorg/myrepo/semantic.sqlite3` with:
- Code chunks (default: 180 lines with 30-line overlap, files under 512 KB)
- Float32 embeddings from the configured model
- WAL-mode SQLite for concurrent reads

Indexing time depends on repo size. A 100k-line repo typically indexes in 30–60 seconds.

### 10.4 Permission Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `EACCES` on `/workspace/repos` | Directory not readable | `chmod -R a+rX /workspace/repos` |
| Docker socket denied | User not in `docker` group | `sudo usermod -aG docker $USER` then re-login |
| Slice install fails | Not root | `sudo ./scripts/bootstrap.sh` |
| Audit log write fails | Volume permissions | Check `audit/` directory ownership |

### 10.5 Large Repo Indexing Tips

For repos over 300k lines:

1. **Increase index slice memory:** Edit `claude-index.slice` to `MemoryHigh=10G`, `MemoryMax=16G`.
2. **Exclude vendor/generated code:** Add paths to `--extra-excludes` or configure in `repos.yaml`.
3. **Index incrementally:** The semantic store uses content-hash change detection — re-indexing only processes changed files.
4. **Monitor PSI during indexing:** `watch -n 1 'cat /proc/pressure/memory'`. If `full avg10 > 2.0`, the system is thrashing.
5. **Reduce chunk size:** `CTS_SEMANTIC_CHUNK_LINES=120` produces more chunks but each is cheaper to embed.

---

## 11. Design Decisions

### 11.1 Why Exec-Only

Ephemeral container patterns (`docker run --rm`) have three problems at scale:

1. **Startup latency.** Container creation adds 200–500ms per request. For interactive search, this is unacceptable.
2. **Resource unpredictability.** N concurrent requests can spawn N containers, each consuming memory independently of cgroup limits.
3. **Docker API surface.** Container creation requires `CONTAINERS + POST + IMAGES`, which expands the attack surface significantly.

Exec-only solves all three: containers are pre-warmed, resource limits are per-slice (not per-request), and the Docker API surface is minimal.

### 11.2 Why No Ephemeral Jobs (Yet)

The current job runner execs into the `claude-build` container, which means:
- Only one build environment is available at a time.
- The build container's dependencies must cover all repos.
- Concurrent builds share the same filesystem.

Ephemeral jobs (per-repo containers with tailored toolchains) are on the roadmap but require solving the resource accounting problem: how do you enforce slice limits on dynamically-created containers? The current model trades flexibility for predictability.

### 11.3 Why Linux-First

cgroup v2 is the foundation. There is no equivalent on macOS or Windows:

- **macOS** has no process-group memory limits. Docker Desktop runs in a VM with a fixed memory cap, but you can't allocate different budgets to different services.
- **Windows** has Job Objects with memory limits, but Docker Desktop uses WSL2, which adds its own memory management layer.

The recommended pattern for macOS/Windows developers: run the tool farm on a Linux host (cloud VM, spare machine, or dedicated server). Use Claude Desktop's SSH environment feature to connect remotely.

### 11.4 Why Bounded Autopilot

Unbounded refinement loops are the primary risk in agentic search:

1. Each pass costs time (model inference, gateway round-trips, slice fetching).
2. Quality can *decrease* with too many passes (overfitting to low-quality signals).
3. Without a time budget, a single bad query can stall the entire pipeline.

Autopilot is bounded by three gates:
- **Pass count** (default 2) — hard cap on iterations.
- **Wall-clock budget** (default 30s) — time budget across all passes.
- **Confidence gate** (default 0.45) — stop early if quality is already sufficient.

### 11.5 Why Measured Defaults

Every default in the system was chosen based on measurement, not intuition:

- **`exclude_top_k = 10`** — validated via A/B experiment showing no regression vs unfiltered, with measurable latency improvement.
- **`max_slices = 4`** — balances evidence richness vs response size. 3 was too sparse, 5 triggered truncation too often.
- **`confidence_gate = 0.45`** — determined by analyzing the distribution of confidence scores across 80 test cases. Lower gates cause unnecessary refinement; higher gates miss recoverable queries.
- **`chunk_lines = 180`** — empirically, chunks of 120–200 lines produce the best embedding quality for code. Smaller chunks lose context; larger chunks dilute signal.

---

## 12. ELI5

**What does Claude ToolStack do?**

Imagine Claude is a developer who needs to understand a huge codebase. Without ToolStack, Claude would try to read the entire thing, run out of memory, and your computer would freeze.

ToolStack is like a librarian. Instead of giving Claude the entire library, the librarian:
- Knows where every book is (indexes)
- Finds the exact pages Claude needs (bounded search)
- Hands over just those pages (evidence bundles)
- Never lets anyone take more than they can carry (512 KB cap)

The librarian also makes sure that the reading room (your computer) stays comfortable — no one department can hog all the seats (cgroup slices), and the building security (Docker proxy) only lets people do specific things.

**Why can't Claude just use ripgrep directly?**

It can, and does for small repos. But for large repos (100k+ lines across 50+ packages), raw ripgrep returns too much data. ToolStack adds ranking (which files matter most?), context (what's around the match?), structural signals (is this a definition or a usage?), and semantic understanding (what's conceptually similar?). The result is a focused evidence bundle instead of a firehose.

---

## 13. Roadmap

These are planned improvements, not commitments:

- **Background semantic indexing worker** — index repos on file change, not manual trigger.
- **Additional narrowing strategies** — beyond `exclude_top_k`, explore cluster-based and graph-based candidate selection.
- **Multi-user support** — per-user API keys with independent rate limits and repo access.
- **Remote SSH mode** — first-class support for Claude Desktop's SSH environment, including TLS/mTLS termination (nginx profile exists but is undocumented).
- **Optional FAISS backend** — replace pure-Python cosine similarity with FAISS for repos with 100k+ chunks.
- **Streaming responses** — for long-running jobs, stream output instead of buffering to 512 KB.
- **Language server integration** — use LSP hover/definition/references as additional ranking signals.

---

## Appendix A: Environment Variables Reference

### Gateway Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | (required) | Gateway authentication |
| `ALLOWED_REPOS` | (empty = deny all) | Comma-separated repo patterns |
| `DENIED_REPOS` | (empty) | Explicit deny patterns |
| `REPO_ROOT` | `/repos` | Host repo mount point |
| `CACHE_ROOT` | `/cache` | Writable cache volume |
| `DOCKER_HOST` | (from env) | Docker socket proxy address |
| `ALLOWED_CONTAINERS` | `claude-ctags,claude-build` | Container exec whitelist |
| `RG_THREADS` | 4 | Ripgrep parallelism |
| `RG_CONCURRENCY` | 2 | Concurrent rg operations |
| `JOB_CONCURRENCY` | 1 | Concurrent job operations |
| `MAX_MATCHES` | 200 | Max search matches |
| `MAX_RESPONSE_BYTES` | 524288 (512 KB) | Response size cap |
| `REQUEST_TIMEOUT_SEC` | 20 | Per-request timeout |
| `RATE_LIMIT_RPS` | 2.0 | Token refill rate |
| `RATE_LIMIT_BURST` | 10 | Max tokens |
| `RATE_LIMIT_SCOPE` | `key+ip` | Rate limit scope |
| `RATE_LIMIT_BACKEND` | `memory` | Rate limit backend |
| `AUDIT_LOG_PATH` | `/audit/audit.jsonl` | Audit log location |
| `AUDIT_LOG_MAX_MB` | 50 | Log rotation threshold |
| `AUDIT_LOG_BACKUPS` | 5 | Backup file count |

### Semantic Search Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `CTS_SEMANTIC_CHUNK_LINES` | 180 | Lines per chunk |
| `CTS_SEMANTIC_OVERLAP_LINES` | 30 | Overlap between chunks |
| `CTS_SEMANTIC_MAX_FILE_BYTES` | 524288 (512 KB) | Skip files larger than this |
| `CTS_SEMANTIC_TOPK` | 8 | Top-K chunks to retrieve |
| `CTS_SEMANTIC_MAX_SLICES` | 4 | Max semantic slices per query |
| `CTS_SEMANTIC_MAX_SECONDS` | 4.0 | Time budget for semantic search |
| `CTS_SEMANTIC_CONFIDENCE_GATE` | 0.45 | Autopilot refinement threshold |
| `CTS_SEMANTIC_MATCH_GATE` | 5 | Minimum matches for confidence |
| `CTS_SEMANTIC_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `CTS_SEMANTIC_DEVICE` | `auto` | `auto`, `cpu`, or `cuda` |
| `CTS_SEMANTIC_CANDIDATE_STRATEGY` | `exclude_top_k` | Narrowing strategy |
| `CTS_SEMANTIC_CANDIDATE_EXCLUDE_TOP_K` | 10 | Files to exclude from semantic |
| `CTS_SEMANTIC_CANDIDATE_MAX_FILES` | 200 | Max candidate files |
| `CTS_SEMANTIC_CANDIDATE_MAX_CHUNKS` | 20000 | Max candidate chunks |
| `CTS_SEMANTIC_CANDIDATE_FALLBACK` | `global_tight` | Fallback when narrowing yields nothing |

---

## Appendix B: Gateway API Reference

All endpoints require the `x-api-key` header. Gateway binds to `127.0.0.1:8088`.

### `GET /v1/status`

Returns gateway health and configuration.

**Response:** `{ version, config, limits }`

### `POST /v1/search/rg`

Ripgrep search with guardrails.

**Request:**
```json
{
  "repo": "myorg/myrepo",
  "query": "PaymentService",
  "max_matches": 50,
  "fixed_string": false,
  "case_sensitive": false,
  "path_globs": ["*.py"],
  "extra_excludes": ["vendor/"]
}
```

**Response:** Array of matches with file, line, content.

### `POST /v1/file/slice`

Fetch a range of lines from a file.

**Request:**
```json
{
  "repo": "myorg/myrepo",
  "path": "src/main.ts",
  "start": 120,
  "end": 160
}
```

**Response:** `{ start, end, lines: [...] }`

### `POST /v1/index/ctags`

Trigger ctags index build (async, 600s timeout).

**Request:**
```json
{
  "repo": "myorg/myrepo"
}
```

**Response:** `{ exit_code, stdout, stderr }`

### `POST /v1/symbol/ctags`

Query symbol definitions.

**Request:**
```json
{
  "repo": "myorg/myrepo",
  "symbol": "PaymentService"
}
```

**Response:** Array of `{ name, file, excmd, kind }`.

### `POST /v1/run/job`

Run an allowlisted build/test/lint preset.

**Request:**
```json
{
  "repo": "myorg/myrepo",
  "job": "test",
  "preset": "node"
}
```

**Response:** `{ stdout, stderr, exit_code }`

### `GET /v1/metrics`

Prometheus-format counters for monitoring:

```
gateway_requests_total
gateway_rate_limit_429_total
gateway_docker_exec_total
gateway_docker_exec_errors_total
gateway_truncations_total
gateway_search_total
gateway_slice_total
gateway_ctags_index_total
gateway_ctags_query_total
gateway_job_total
gateway_requests_by_status{status="200"}
```

---

## Appendix C: Bundle Schema v2

```json
{
  "version": 2,
  "mode": "default|error|symbol|change",
  "repo": "org/repo",
  "request_id": "uuid",
  "timestamp": 1709316000.0,
  "query": "search text",
  "ranked_sources": [
    {
      "path": "src/auth.ts",
      "score": 1.45,
      "signals": { "path_boost": 0.5, "recency": 0.3, "trace": 0.0 }
    }
  ],
  "matches": [
    {
      "file": "src/auth.ts",
      "line": 42,
      "content": "export class AuthService {",
      "snippet": "..."
    }
  ],
  "slices": [
    {
      "path": "src/auth.ts",
      "start": 30,
      "end": 60,
      "lines": ["..."]
    }
  ],
  "symbols": [],
  "diff": "",
  "suggested_commands": [],
  "notes": [],
  "truncated": false,
  "_debug": {
    "timings": { "search_ms": 142, "ranking_ms": 12, "slicing_ms": 89 },
    "semantic": { "enabled": true, "semantic_time_ms": 340, "slices_added": 2 },
    "narrowing": { "strategy": "exclude_top_k", "excluded": 10, "searched": 190 },
    "confidence": { "score": 0.72, "signals": { "..." } },
    "query_embed_cached": false
  }
}
```

---

*Built by [MCP Tool Shop](https://mcp-tool-shop.github.io/)*
