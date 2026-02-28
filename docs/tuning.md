# Tuning Guide

## Slice Sizing by Repo Size

The systemd slices ship with **medium-repo defaults**. Adjust based on your workload:

| Slice | Small (<50k files) | Medium (50k-300k) | Large (>300k files) |
|---|---|---|---|
| `claude-index` | high 2G / max 4G | high 6G / max 10G | high 10G / max 16G |
| `claude-lsp` | high 4G / max 8G | high 8G / max 16G | high 16G / max 24G |
| `claude-build` | high 6G / max 12G | high 10G / max 18G | high 12G / max 24G |
| `claude-vector` | high 2G / max 6G | high 8G / max 16G | high 16G / max 28G |

Edit files in `systemd/` and reload:

```bash
sudo systemctl daemon-reload
```

## PSI Monitoring

Pressure Stall Information is your primary thrash detector:

```bash
# Live monitoring
watch -n 1 'cat /proc/pressure/memory; echo; cat /proc/pressure/io'

# What "good" looks like:
# full avg10=0.00 avg60=0.00 avg300=0.00  ← near zero
# some avg10=2.50 avg60=1.00 avg300=0.50  ← short spikes OK

# What "bad" looks like:
# full avg10=15.00 avg60=8.00 avg300=4.00  ← sustained thrash
```

- `some` = at least one task stalled (brief spikes OK during bursts)
- `full` = all tasks stalled (indicates thrashing, wasted CPU cycles)

## Adding Language Servers

Add services to `compose.yaml` under the `claude-lsp.slice`:

### clangd (C/C++)

```yaml
clangd:
  image: silkeh/clangd:latest
  container_name: claude-clangd
  init: true
  entrypoint: ["/bin/sh", "-c"]
  command: >
    mkdir -p /cache/clangd &&
    clangd -j=4 --background-index
    --index-file=/cache/clangd/index &&
    sleep infinity
  volumes:
    - /workspace/repos:/repos:ro
    - clangd-cache:/cache
  networks: [tools-net]
  mem_limit: 10g
  cpus: "6"
  cgroup_parent: "claude-lsp.slice"
```

### rust-analyzer

```yaml
rust_analyzer:
  image: rust:latest
  container_name: claude-rust-analyzer
  init: true
  entrypoint: ["/bin/sh", "-c"]
  command: >
    rustup component add rust-analyzer || true;
    echo "rust-analyzer ready";
    sleep infinity
  volumes:
    - /workspace/repos:/repos:ro
    - ra-cache:/cache
  networks: [tools-net]
  mem_limit: 6g
  cpus: "4"
  cgroup_parent: "claude-lsp.slice"
```

### tsserver (TypeScript/JavaScript)

```yaml
tsserver:
  image: node:20
  container_name: claude-tsserver
  init: true
  entrypoint: ["/bin/sh", "-c"]
  command: >
    npm i -g typescript;
    echo "tsserver ready";
    sleep infinity
  volumes:
    - /workspace/repos:/repos:ro
    - ts-cache:/cache
  networks: [tools-net]
  mem_limit: 4g
  cpus: "4"
  cgroup_parent: "claude-lsp.slice"
```

Add corresponding volumes to the `volumes:` section.

## Vector Store Options

### SQLite + FAISS (simplest)

Minimal ops, runs as a library. Good for local-first setups.
Add a service or embed in the gateway.

### Weaviate (HNSW in-memory)

Rule of thumb: memory ≈ 2 × (footprint of all vectors).
Clear sizing guidance. HNSW index must be in memory.

### Milvus (standalone)

Heavier footprint (etcd, MinIO dependencies).
Minimum 8 GB RAM, recommended 16 GB.
Disk latency sensitive for etcd.

## Job Preset Customization

The gateway ships with presets for `node`, `python`, `rust`, and `go`.
Each preset defines commands for `test`, `build`, and `lint`.

To add a preset, edit the `presets` dict in `gateway/main.py`:

```python
"java": {
    "container": "claude-build",
    "cwd": f"/repos/{repo_id}",
    "commands": {
        "test": ["sh", "-c", "cd $CWD && ./gradlew test"],
        "build": ["sh", "-c", "cd $CWD && ./gradlew build"],
        "lint": ["sh", "-c", "cd $CWD && ./gradlew spotlessCheck"],
    },
    "timeout": 1200,
},
```

Then add `claude-build` to `ALLOWED_CONTAINERS` in `.env` (if not already present).

## Ripgrep Tuning

Default search guardrails in `gateway/main.py`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `RG_THREADS` | 4 | Prevents CPU storms |
| `MAX_MATCHES` | 200 | Bounds result count |
| `MAX_RESPONSE_BYTES` | 512 KB | Prevents output flooding |
| `REQUEST_TIMEOUT_SEC` | 20s | Prevents hung searches |
| `RG_CONCURRENCY` | 2 | Max parallel rg calls |

Default excludes: `.git/`, `node_modules/`, `dist/`, `build/`, `target/`, `.next/`, `.turbo/`, `.cache/`, `vendor/`
