---
title: Getting Started
description: Bootstrap your Linux host, configure the stack, and verify deployment.
sidebar:
  order: 1
---

This guide walks you through a complete deployment of Claude ToolStack on a 64-GB Linux workstation. By the end, you will have a running gateway, resource-governed containers, and a verified smoke test.

## Prerequisites

Before you begin, ensure you have:

- **Ubuntu 22.04** or **Fedora 38+** (other systemd-based distros may work but are untested)
- **64 GB RAM** (the default slice configuration assumes this; see the [Reference](/claude-toolstack/handbook/reference/) page for tuning smaller or larger hosts)
- **Docker Engine** with Compose v2 (not Docker Desktop — the cgroup integration requires native Linux Docker)
- **Git** and basic command-line familiarity

## 1. Bootstrap the host

The bootstrap script is a one-time setup that configures your system for resource governance:

```bash
sudo ./scripts/bootstrap.sh
```

This installs and configures:

| Component | What it does |
|-----------|-------------|
| **zram swap** | Compresses memory pages in-place using LZ4, effectively expanding usable memory by 2-3x for compressible data like build artifacts and indexer state. On Ubuntu, installs `zram-generator`; on Fedora, verifies the existing swap-on-zram. |
| **Sysctl tuning** | Sets `vm.swappiness` for optimal cgroup behavior, increases `fs.inotify.max_user_watches` for large repos. |
| **systemd slices** | Installs five cgroup v2 slices (`claude-gw`, `claude-index`, `claude-lsp`, `claude-build`, `claude-vector`) with MemoryHigh/MemoryMax governance. |
| **Docker daemon config** | Configures local JSON log driver to prevent log-driven disk exhaustion. |
| **Boot service** | Installs `claude-toolstack.service` for automatic stack management on boot. |

:::tip
You only need to run the bootstrap script once. It is idempotent, so running it again is safe but unnecessary.
:::

## 2. Configure

Copy the example environment file and edit it with your settings:

```bash
cp .env.example .env
```

The critical variables to set:

| Variable | Purpose | Example |
|----------|---------|---------|
| `API_KEY` | Authentication for all gateway requests | A strong random string (32+ characters) |
| `ALLOWED_REPOS` | Comma-separated glob patterns for repo access | `myorg/*,partner/shared-lib` |
| `DENIED_REPOS` | Explicit deny patterns (checked first) | `myorg/secrets,myorg/credentials` |

Deny rules take precedence over allow rules. If `ALLOWED_REPOS` is empty, all repos are denied by default — this is a secure-by-default posture.

## 3. Clone repos

Repos live under `/workspace/repos/<org>/<repo>`. This structure is required because the gateway uses the `org/repo` path as a routing key:

```bash
# Create the workspace root
sudo mkdir -p /workspace/repos
sudo chown $USER:$USER /workspace/repos

# Clone your repositories
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
git clone https://github.com/myorg/another /workspace/repos/myorg/another
```

:::caution
Do not use symlinks to point `/workspace/repos` elsewhere. The gateway validates paths using `realpath` and symlinks that resolve outside the jail will be rejected.
:::

## 4. Start the stack

Build and launch all services:

```bash
docker compose up -d --build
```

This starts five containers by default:

| Container | Purpose |
|-----------|---------|
| `claude-gateway` | FastAPI gateway on `127.0.0.1:8088` |
| `claude-dockerproxy` | Socket proxy — filters Docker API to exec-only |
| `claude-toolstack` | The `cts` CLI running inside the Docker network |
| `claude-ctags` | Universal ctags indexer |
| `claude-build` | Build/test runner with read-write repo access |

Optional profiles add more containers:

```bash
# Add language servers (clangd, rust-analyzer, tsserver, pylsp, gopls)
docker compose --profile lsp up -d --build

# Add nginx reverse proxy for remote access
docker compose --profile remote up -d --build
```

## 5. Verify

Run the smoke test to confirm everything is wired correctly:

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
```

The smoke test exercises each gateway endpoint (status, search, file slice, ctags index, symbol query, job runner, metrics) and reports pass/fail for each.

For a broader health check:

```bash
./scripts/health.sh
```

You can also use the built-in diagnostic command:

```bash
cts doctor
```

`cts doctor` checks the repo root, ripgrep availability, Python dependencies (numpy, sentence-transformers), gateway reachability, semantic stores, Docker host configuration, Docker proxy connectivity, and expected tool containers. Each check reports `[OK]`, `[~]` (warning), or `[!]` (failure) with a specific remediation hint.

## What comes next

Once the stack is verified:

1. **Search your code:** `cts search "PaymentService" --repo myorg/myrepo`
2. **Build evidence bundles:** `cts search "auth" --repo myorg/myrepo --format claude`
3. **Index for semantic search:** `cts semantic index --repo myorg/myrepo --root /workspace/repos/myorg/myrepo`

See the [Usage](/claude-toolstack/handbook/usage/) page for the full command reference and bundle modes.
