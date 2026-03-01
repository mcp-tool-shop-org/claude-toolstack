<p align="center">
  <a href="README.md">English</a> | <a href="README.zh.md">中文</a> | <a href="README.es.md">Español</a> | <a href="README.fr.md">Français</a> | <a href="README.hi.md">हिन्दी</a> | <a href="README.it.md">Italiano</a> | <a href="README.pt-BR.md">Português (BR)</a>
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

## これは何ですか？

大規模で多言語のリポジトリに対して、Claude Codeを効率的に動作させ、64GBのLinuxワークステーションのリソースを圧迫しない、すぐにデプロイできる環境です。

**基本的な考え方:** リポジトリ全体をClaudeに読み込ませるのではなく、リソース制限のあるコンテナ内に永続的なインデックスを作成します。そして、必要な最小限の情報だけを、HTTPゲートウェイを通じてClaudeに送信します。

## アーキテクチャ

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

## クイックスタート

### 1. ホストのセットアップ

```bash
sudo ./scripts/bootstrap.sh
```

これにより、以下のものがインストールまたは設定されます。
- zramスワップ (Ubuntu) またはスワップオンzramの有効化 (Fedora)
- システム設定 (swappiness、inotify監視)
- systemdのスライス (MemoryHigh/Maxによるリソース制限)
- Dockerデーモンの設定 (ローカルログドライバー)
- claude-toolstack.service (起動管理)

### 2. 設定

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. リポジトリのクローン

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. 環境の起動

```bash
docker compose up -d --build
```

### 5. 動作確認

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## ゲートウェイAPI

すべてのエンドポイントで `x-api-key` ヘッダーが必要です。ゲートウェイは `127.0.0.1:8088` のみにバインドされます。

| メソッド | エンドポイント | 目的 |
|--------|----------|---------|
| `GET` | `/v1/status` | ヘルスチェック + 設定 |
| `POST` | `/v1/search/rg` | ガードレール付きのgrep |
| `POST` | `/v1/file/slice` | ファイル範囲の取得 (最大800行) |
| `POST` | `/v1/index/ctags` | ctagsインデックスのビルド (非同期) |
| `POST` | `/v1/symbol/ctags` | シンボル定義のクエリ |
| `POST` | `/v1/run/job` | 許可リストにあるテスト/ビルド/リンターの実行 |
| `GET` | `/v1/metrics` | Prometheus形式のカウンタ |

すべての応答には、エンドツーエンドの相関のための `X-Request-ID` が含まれます。クライアントは `X-Request-ID` ヘッダーで独自のIDを送信できます。

## CLI (`cts`)

すべてのゲートウェイエンドポイントをラップする、依存関係のないPython CLIです。

### インストール

```bash
pip install -e .
# or: pipx install -e .
```

### 設定

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### 使用方法

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

### エビデンスバンドル v2 (`--format claude`)

`--claude` オプションは、構造化されたv2ヘッダーを持つ、コンパクトで貼り付け可能なエビデンスパックを生成します。4つのバンドルモードが利用可能です。

| モード | フラグ | 機能 |
|------|------|-------------|
| `default` | `--bundle default` | 検索 + ランク付けされたマッチ + コンテキストスライス |
| `error` | `--bundle error` | スタックトレース対応: トレースからファイルを抽出し、ランキングを向上させます。 |
| `symbol` | `--bundle symbol` | 検索結果からの定義 + 呼び出し元 |
| `change` | `--bundle change` | Gitの差分 + hunkコンテキストスライス |

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

チューニング: `--evidence-files 5` (スライスするファイル数)、`--context 30` (ヒット箇所の周囲の行数)。

### curlの例

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

## リソース管理

systemdのスライスは、サービスグループごとにMemoryHigh (スロットリング) とMemoryMax (ハードキャップ) を適用します。

| スライス | MemoryHigh | MemoryMax | 目的 |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 GB | 4 GB | ゲートウェイ + ソケットプロキシ |
| `claude-index` | 6 GB | 10 GB | インデクサ、検索 |
| `claude-lsp` | 8 GB | 16 GB | 言語サーバー |
| `claude-build` | 10 GB | 18 GB | ビルド/テストランナー |
| `claude-vector` | 8 GB | 16 GB | ベクトルDB (オプション) |

これらは、中規模リポジトリのデフォルト値です。ワークロードに合わせて、`systemd/` 内のスライスファイルを編集してください。

OS + 余裕: ファイルシステムキャッシュ、デスクトップ、SSHのために、常に10〜14GBが予約されています。

## セキュリティ

### 脅威モデル

**保護対象:**
- ゲートウェイの不正利用 (無許可アクセス、リソース枯渇)
- パス穿越 (リポジトリのルートからのエスケープ、シンボリックリンクの利用)
- Dockerソケットの悪用 (rawソケット = root権限と同等)
- 出力洪水 (無制限の検索/ビルド結果によるメモリ消費)

**セキュリティ層:**

| 層 | 仕組み |
|-------|-----------|
| 認証 | APIキー (`x-api-key` ヘッダー)、設定可能 |
| ネットワーク | ゲートウェイは `127.0.0.1` のみにバインド |
| Docker | ソケットプロキシ (Tecnativa)、`CONTAINERS+EXEC` のみ許可 |
| リポジトリ | 許可リスト/拒否リスト（glob形式のパターンに対応） |
| パス | `realpath`による隔離、ヌルバイトの拒否 |
| コマンド | あらかじめ定義された許可リストのみ、任意のコマンドの実行は不可 |
| 出力 | 512 KBの制限、行の切り捨て |
| レート制限 | キーとIPアドレスごとにトークンバケット方式 |
| 監査 | JSONL形式のログ、キーのハッシュ化、ローテーション |
| コンテナ | 名前付きの許可リスト、ワイルドカードは不可 |
| リソース | cgroup v2のスライス、コンテナごとのメモリ/CPU制限 |

### このゲートウェイでできないこと

- 任意のコマンドの実行（あらかじめ定義された許可リストのみ）
- `/workspace/repos`以外のリポジトリへのアクセス（パス隔離）
- Dockerイメージ、ボリューム、ネットワーク、またはシステムへの変更（プロキシによるブロック）
- 無制限の出力（512 KBのハード制限）
- localhost以外の接続の受け入れ（バインドアドレス）
- テレメトリーの収集または送信 — **テレメトリーは行いません。電話による情報送信も分析もありません。**

## ディレクトリ構造

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

## Claude Codeとの連携

### ローカル環境（Linux）

Claude Codeはホスト上で直接実行されます。ゲートウェイをMCPサーバーとして構成するか、タスクスクリプトからHTTP経由で呼び出します。

### リモート環境（macOS/Windows）

Claude DesktopのCodeタブを使用し、LinuxホストへのSSH環境を構築します。ツールファームはホスト上で実行され、GUIはラップトップ上に表示されます。

## チューニング

[docs/tuning.md](docs/tuning.md)で以下の内容を確認してください。
- リポジトリのサイズ（small/medium/large）ごとのスライスのサイズ設定
- PSIの監視とスロットリングの検出
- 言語サーバーの追加（clangd, rust-analyzer, tsserver）
- ベクトルストアのオプション（SQLite+FAISS, Weaviate, Milvus）
- ジョブのプリセットのカスタマイズ

## スロットリングの検証

デプロイ後、以下の点を確認してください。

1. **PSIのフル値がほぼゼロ**: `watch -n 1 'cat /proc/pressure/memory'`
2. **コンテナがMaxに達する前にMemoryHighに到達**: スライスの状態を確認
3. **SSHが応答よく動作**: インデックス作成時およびビルド時
4. **隔離が機能している**: あるサービスの制限を縮小し、負荷の高いタスクを実行し、そのコンテナのみが停止することを確認

---

構築：<a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a>
