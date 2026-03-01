# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |
| < 1.0   | No        |

## Reporting a Vulnerability

Email: **64996768+mcp-tool-shop@users.noreply.github.com**

Include:

- Description of the vulnerability
- Steps to reproduce
- Version affected
- Potential impact

### Response timeline

| Action | Target |
|--------|--------|
| Acknowledge report | 48 hours |
| Assess severity | 7 days |
| Release fix | 30 days |

## Scope

Claude Toolstack is a **local-first** CLI + Docker tool farm for bounded code intelligence.

### Data touched

- **Source code** — read-only access to repos under `/workspace/repos` (host-mounted, path-jailed)
- **Semantic indexes** — SQLite embeddings in `gw-cache/` (local, never transmitted)
- **Audit logs** — JSONL request logs in `gw-audit/` (API keys hashed, never stored plaintext)
- **Docker API** — scoped through socket proxy to CONTAINERS + EXEC only

### Data NOT touched

- No access to repos outside the configured allowlist
- No access to host filesystem outside mounted volumes
- No access to Docker images, volumes, networks, or system endpoints
- No credentials stored — API key is configured via environment variable

### Network posture

- Gateway binds to **127.0.0.1 only** by default (no remote access)
- **No telemetry** is collected or sent
- **No network egress** unless user explicitly enables the `[remote]` Compose profile (nginx reverse proxy)
- Docker socket proxy denies all Docker API endpoints except container inspection and exec

### Permissions required

- **Host root** (one-time): `bootstrap.sh` installs systemd slices and sysctl configs
- **Docker access**: gateway container communicates with tool containers via socket proxy
- **File read**: repos mounted read-only into gateway/ctags containers
- **File write**: build container has read-write repo access (for test/build artifacts only)
