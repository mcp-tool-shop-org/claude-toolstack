---
title: Usage
description: CLI commands, evidence bundles, semantic search, and curl examples.
sidebar:
  order: 2
---

Claude ToolStack ships a zero-dependency Python CLI called `cts` that wraps all gateway endpoints. This page covers installation, every command, and the evidence bundle system that makes Claude Code effective on large repositories.

## Installing the CLI

```bash
# Editable install (development)
pip install -e .

# Or via pipx for isolated installs
pipx install -e .
```

Configure access to the gateway:

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

## CLI commands

Every command supports `--format json|text|claude`, `--request-id <id>`, and `--debug` flags.

### `cts status`

Check gateway health and current configuration:

```bash
cts status
```

Returns the gateway version, configured limits (max matches, response cap, timeouts), and active repos.

### `cts search`

Ripgrep-powered search with guardrails:

```bash
# Basic text search
cts search "PaymentService" --repo myorg/myrepo --max 50

# Evidence bundle for Claude (auto-fetches context slices)
cts search "PaymentService" --repo myorg/myrepo --format claude

# With path preferences
cts search "handler" --repo myorg/myrepo --format claude \
  --prefer-paths src,core --avoid-paths vendor,test

# With git recency scoring
cts search "handler" --repo myorg/myrepo --format claude \
  --repo-root /workspace/repos/myorg/myrepo
```

Search results are bounded to 200 matches by default (configurable via `MAX_MATCHES`), and the total response is capped at 512 KB.

### `cts slice`

Fetch a specific range of lines from a file:

```bash
cts slice --repo myorg/myrepo src/main.ts:120-180
```

Maximum 800 lines per slice. This is the primitive that evidence bundles use internally to fetch context around matches.

### `cts symbol`

Query ctags for symbol definitions:

```bash
# Direct symbol lookup
cts symbol PaymentService --repo myorg/myrepo

# Symbol bundle with definitions + call sites
cts symbol PaymentService --repo myorg/myrepo --format claude --bundle symbol
```

Uses universal-ctags for definitions and ripgrep for call sites, then ranks by structural signals (definition vs. export vs. caller proximity).

### `cts job`

Run allowlisted build, test, and lint presets:

```bash
cts job test --repo myorg/myrepo --preset node
cts job build --repo myorg/myrepo --preset python
cts job lint --repo myorg/myrepo --preset node
```

Only commands from the preset allowlist execute. There is no arbitrary exec, no shell interpolation, and no user-supplied command strings. Presets for `node`, `python`, `rust`, and `go` ship by default.

### `cts doctor`

Run diagnostic checks across the entire stack:

```bash
cts doctor
cts doctor --format json
```

Checks repo root, ripgrep, Python dependencies, gateway reachability, semantic stores, Docker host, Docker proxy, and tool containers. Each check reports `[OK]`, `[~]` (warning), or `[!]` (failure).

### `cts perf`

Display all tunable performance knobs with current values:

```bash
cts perf
cts perf --format json
```

Shows every configuration variable, its current value, the environment variable that controls it, and whether the current value comes from an override or the default. Overridden values are marked with `*`.

### `cts semantic`

Embedding-based code search for conceptual similarity:

```bash
# Index a repo (builds SQLite vector store)
cts semantic index --repo myorg/myrepo --root /workspace/repos/myorg/myrepo

# Semantic search
cts semantic search "what does the auth middleware do?" --repo myorg/myrepo
```

The default embedding model is `sentence-transformers/all-MiniLM-L6-v2`. Override with the `CTS_SEMANTIC_MODEL` environment variable.

Indexing creates `gw-cache/<org>/<repo>/semantic.sqlite3` containing code chunks (180 lines with 30-line overlap, files under 512 KB) and float32 embeddings. The store uses WAL-mode SQLite for concurrent reads. Re-indexing uses content-hash change detection — only changed files are re-processed.

## Evidence bundles

The `--format claude` output mode produces compact, paste-ready evidence packs designed for Claude Code. Bundles use a structured v2 header format with ranked sources, context slices, and debug metadata.

### Four bundle modes

| Mode | Flag | What it does |
|------|------|-------------|
| **default** | `--bundle default` | Search + ranked matches + context slices around hits |
| **error** | `--bundle error` | Stack-trace-aware: extracts file paths from tracebacks, boosts those files in ranking, assembles slices around crash sites |
| **symbol** | `--bundle symbol` | Definitions + call sites from ctags and search |
| **change** | `--bundle change` | Git diff + hunk context slices for review-style evidence |

### Error bundles

The error bundle mode recognizes Python tracebacks, Node.js stacks, Go panics, and Java/C# exception chains. Files mentioned in the trace receive a +2.0 ranking boost:

```bash
cts search "ConnectionError" --repo myorg/myrepo --format claude \
  --bundle error --error-text "$(cat /tmp/traceback.txt)"
```

### Tuning bundle output

| Flag | Default | Purpose |
|------|---------|---------|
| `--evidence-files` | 5 | Number of files to fetch context slices for |
| `--context` | 30 | Lines of context around each hit |
| `--prefer-paths` | — | Comma-separated path prefixes to boost in ranking |
| `--avoid-paths` | — | Comma-separated path prefixes to demote |
| `--repo-root` | — | Local repo path for git recency scoring |

### Autopilot refinement

Autopilot iteratively refines search quality when initial results are insufficient:

```bash
cts search "auth" --repo myorg/myrepo --format claude --autopilot 2
```

Each pass checks a confidence score (based on top match score, definition presence, source diversity, and slice coverage). If confidence falls below the gate (default 0.45), autopilot refines the query and searches again, up to the pass limit or a 30-second wall-clock budget.

### Semantic fallback

When a vector store exists for a repo, semantic search activates automatically as a bounded fallback under two conditions:

- **Sparse lexical results** — too few ripgrep matches. Semantic adds embedding-based matches that lexical search missed.
- **High-hit, low-quality** — many matches but low confidence (vendor files, test fixtures). Semantic surfaces conceptually relevant code instead.

Semantic fallback is bounded to 4 additional slices and a 4-second time budget. It uses candidate narrowing (`exclude_top_k` by default) to surface new information rather than duplicating lexical results.

## curl examples

For direct gateway access without the CLI:

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

# Symbol lookup
curl -sS -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"repo":"myorg/myrepo","symbol":"PaymentService"}' \
  http://127.0.0.1:8088/v1/symbol/ctags | jq

# Gateway health
curl -sS -H "x-api-key: $KEY" \
  http://127.0.0.1:8088/v1/status | jq

# Prometheus metrics
curl -sS -H "x-api-key: $KEY" \
  http://127.0.0.1:8088/v1/metrics
```
