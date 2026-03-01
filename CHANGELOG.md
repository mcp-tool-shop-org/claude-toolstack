# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-03-01

### Added

- **`cts doctor`** — stack health diagnostics (ripgrep, ctags, numpy, semantic stores, Docker containers)
- **`cts perf`** — display all tunable performance knobs with current values and sources
- **Semantic search** — embedding-based code retrieval with cosine similarity, default-on when store exists
- **Candidate narrowing** — `exclude_top_k` strategy reduces semantic search latency by ~10%
- **Query embedding cache** — LRU cache (64 entries) for repeated queries
- **Evidence bundles v2** — 4 bundle modes: default, error, symbol, change
- **Sidecar artifacts** — structured evidence packages with secrets scanning and schema validation
- **Docker exec-only topology** — gateway executes tools via `docker exec` into known containers
- **`claude-gw.slice`** — dedicated systemd slice for gateway + dockerproxy (2G/4G)
- **Hardened dockerproxy** — 6 explicit allows (CONTAINERS, EXEC, POST, LOGS, INFO, PING), 14 explicit denials
- **`toolstack` Compose service** — run `cts` inside the Docker stack via `[cli]` profile
- **`cts-docker` wrapper** — `scripts/cts-docker doctor` convenience script
- **Corpus evaluation framework** — A/B experiment infrastructure with 8 KPIs
- **Path scoring + ranking** — path preferences, git recency, trace-aware boosting
- **Rate limiting** — token bucket per key+ip, memory or Redis backends
- **Audit logging** — JSONL with rotated files, hashed API keys
- **systemd cgroup v2 slices** — 5 slices (gw, index, lsp, build, vector) with MemoryHigh/Max governance
- **Bootstrap script** — one-command host setup (zram, sysctl, slices, Docker config)
- **Policy lint CI** — compose validation, socket proxy audit, slice unit checks

### Security

- Gateway binds to 127.0.0.1 only
- Path jail with realpath + null byte rejection
- Command execution via preset allowlist only
- 512 KB hard output cap on all responses
- Docker socket proxy blocks images/volumes/networks/system
- Container execution restricted to named allowlist

## [0.2.0] - 2026-02-28

### Added

- Semantic fallback pipeline (embedding + cosine search)
- Semantic store management (`cts semantic ingest/status/reset`)
- Candidate narrowing with configurable strategies
- Batch experiment runner for A/B evaluation

## [0.1.0] - 2026-02-20

### Added

- Initial CLI with search, slice, symbol, index, job commands
- FastAPI gateway with 6 endpoints
- Docker Compose stack (gateway, ctags, build, LSPs, proxy)
- Evidence bundle v1 output (`--format claude`)
- systemd slice templates
- Bootstrap and smoke-test scripts
