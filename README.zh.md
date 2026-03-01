<p align="center">
  <a href="README.ja.md">日本語</a> | <a href="README.md">English</a> | <a href="README.es.md">Español</a> | <a href="README.fr.md">Français</a> | <a href="README.hi.md">हिन्दी</a> | <a href="README.it.md">Italiano</a> | <a href="README.pt-BR.md">Português (BR)</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/mcp-tool-shop-org/brand/main/logos/claude-toolstack/readme.png" width="400" alt="Claude ToolStack">
</p>

<p align="center">
  Docker + Claude Code workstation config for 64-GB Linux hosts.<br>
  cgroup v2 slices &bull; Compose tool farm &bull; FastAPI gateway &bull; no thrash.
</p>

<p align="center">
  <a href="https://github.com/mcp-tool-shop-org/claude-toolstack/actions/workflows/ci.yml"><img src="https://github.com/mcp-tool-shop-org/claude-toolstack/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/mcp-tool-shop-org/claude-toolstack"><img src="https://codecov.io/gh/mcp-tool-shop-org/claude-toolstack/graph/badge.svg" alt="Coverage"></a>
  <a href="https://github.com/mcp-tool-shop-org/claude-toolstack/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
  <a href="https://mcp-tool-shop-org.github.io/claude-toolstack/"><img src="https://img.shields.io/badge/Landing_Page-live-blue" alt="Landing Page"></a>
</p>

---

## 这是什么

一个可以直接使用的软件栈，能够在大型、多语言的代码仓库中保持 Claude Code 的高效运行，而无需在配置为 64GB 内存的 Linux 工作站上造成性能瓶颈。

**核心思想：** 不要将整个代码仓库导入到 Claude 中。将持久化的索引数据存储在靠近代码的、资源受限的容器中。仅通过一个轻量级的 HTTP 网关，将最必要的证据片段流式传输回 Claude。

## 架构

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

## 快速入门指南

### 1. 启动主机

```bash
sudo ./scripts/bootstrap.sh
```

此脚本会安装以下内容：
- zram 交换空间（Ubuntu 系统）或验证是否启用了 zram 交换空间（Fedora 系统）
- 系统参数调整（包括 swappiness 值和 inotify 监控）
- 使用 MemoryHigh/Max 策略的 systemd 服务组
- Docker 守护进程配置（本地日志驱动）
- claude-toolstack.service（引导管理）

### 2. 配置

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. 克隆代码仓库

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. 启动堆栈

```bash
docker compose up -d --build
```

### 5. 验证

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## 网关API

所有接口都需要包含 `x-api-key` 头部信息。网关仅绑定到 `127.0.0.1:8088` 地址。

| 方法。 | 终端。 | 目的。 |
|--------|----------|---------|
| `GET` | `/v1/status` | 健康 + 配置 |
| `POST` | `/v1/search/rg` | 带有安全机制的 Ripgrep。 |
| `POST` | `/v1/file/slice` | 获取文件指定范围的内容（最多800行）。 |
| `POST` | `/v1/index/ctags` | 构建 ctags 索引（异步）。 |
| `POST` | `/v1/symbol/ctags` | 查询符号定义。 |
| `POST` | `/v1/run/job` | 运行允许列表中的测试、构建和代码检查。 |
| `GET` | `/v1/metrics` | Prometheus 格式的计数器。 |

所有响应都包含 `X-Request-ID` 字段，用于端到端的追踪。客户端可以通过 `X-Request-ID` 头部发送自己的请求 ID。

## 命令行界面 (cts)

一个零依赖的 Python 命令行工具，它封装了所有网关的接口。

### 安装

```bash
pip install -e .
# or: pipx install -e .
```

### 配置

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### 用法

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

### 证据包版本 2 (使用 `--format claude` 选项)

“--claude” 输出模式会生成简洁、可以直接复制粘贴的证据包，并采用结构化的 V2 格式。提供四种打包模式供选择：

| 模式。 | 国旗。 | 它的作用/功能。 |
|------|------|-------------|
| `default` | `--bundle default` | 搜索 + 排名匹配 + 上下文片段。 |
| `error` | `--bundle error` | 支持堆栈跟踪：能够从跟踪信息中提取文件，并提升其在搜索结果中的排名。 |
| `symbol` | `--bundle symbol` | 定义及调用位置（来自搜索结果）。 |
| `change` | `--bundle change` | Git diff，以及包含代码块上下文信息的差异显示。 |

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

参数调整：`--evidence-files 5` (指定需要处理的文件数量)，`--context 30` (指定匹配结果周围的行数)。

### curl 命令示例

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

## 资源治理

systemd 的 "slices" 功能可以对每个服务组实施内存限制，包括 "MemoryHigh"（软限额，会进行降频）和 "MemoryMax"（硬限额，超出则强制停止）。

| 切片。 | 内存优化/内存提升。 | MemoryMax (产品名称，可直接音译) | 目的。 |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 亿字节。 | 4 GB (千兆字节) | 网关 + 代理服务器。 |
| `claude-index` | 6 GB。 | 10 兆字节。 | 索引员，搜索。 |
| `claude-lsp` | 8 GB。 | 16 GB。 | 语言服务器。 |
| `claude-build` | 10 兆字节。 | 18 兆字节。 | 构建/测试框架。 |
| `claude-vector` | 8 GB。 | 16 GB。 | 向量数据库（可选）。 |

这些是默认的存储配置。请根据您的工作负载，修改 `systemd/` 目录下的配置文件。

操作系统 + 剩余空间：10-14GB 的空间始终预留用于文件系统缓存、桌面环境和 SSH 连接。

## 安全

### 威胁模型

**我们保护的内容：**
- 网关滥用（未经授权的访问、资源耗尽）
- 路径遍历（通过“../”或符号链接绕过代码仓库根目录）
- Docker 套接字权限提升（原始套接字相当于 root 权限）
- 输出泛滥（无限的搜索/构建结果占用内存）

**安全层：**

| 层。 | 机制。 |
|-------|-----------|
| 身份验证。 | API 密钥（`x-api-key` 头部），可配置。 |
| 网络 | 网关仅绑定到 `127.0.0.1` 地址。 |
| Docker | 套接字代理 (Tecnativa)，仅支持 `CONTAINERS+EXEC` 功能。 |
| 回购协议。 | 允许/禁止列表，支持通配符。 |
| 路径。 | `realpath` 隔离，拒绝空字节。 |
| 命令。 | 仅允许预设的命令，不允许执行任意命令。 |
| 输出。 | 限制为 512 KB，支持行截断。 |
| 速率限制。 | 每个键+IP 地址的令牌桶。 |
| 审计。 | JSONL 日志，键进行哈希处理，并进行轮换。 |
| 容器。 | 命名允许列表，不支持通配符。 |
| 资源。 | cgroup v2 分片，每个容器的内存/CPU 限制。 |

### 网关无法执行的功能

- 执行任意命令（仅允许预设的命令）。
- 访问 `/workspace/repos` 目录之外的仓库（路径隔离）。
- 修改 Docker 镜像、卷、网络或系统（代理阻止）。
- 返回无限长度的输出（限制为 512 KB）。
- 接受来自非本地主机的连接（绑定地址）。
- 收集或发送遥测数据——**不收集遥测数据，不进行任何数据传输，不进行任何分析。**

## 目录结构

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

## Claude Code 集成

### 本地 Linux

Claude Code 直接运行在主机上。将网关配置为 MCP 服务器，或通过 HTTP 从任务脚本调用它。

### 远程 (macOS/Windows)

使用 Claude Desktop 的代码选项卡，并通过 SSH 连接到您的 Linux 主机。工具运行在主机上，GUI 界面保留在您的笔记本电脑上。

## 调优

请参阅 [docs/tuning.md](docs/tuning.md)，了解以下内容：
- 根据仓库大小调整分片大小（小/中/大）。
- PSI 监控和资源耗尽检测。
- 添加语言服务器（clangd, rust-analyzer, tsserver）。
- 向量存储选项（SQLite+FAISS, Weaviate, Milvus）。
- 任务预设自定义。

## 无资源耗尽验证

部署完成后，请确认：

1. **PSI 满值接近零**: `watch -n 1 'cat /proc/pressure/memory'`
2. **容器在达到最大值之前达到内存高水位**: 检查分片状态。
3. **SSH 连接保持响应**: 在索引和构建过程中。
4. **隔离有效**: 缩小一个服务的限制，运行一个高负载任务，确认只有该容器崩溃。

---

由 <a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a> 构建。
