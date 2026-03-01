<p align="center">
  <a href="README.ja.md">日本語</a> | <a href="README.zh.md">中文</a> | <a href="README.es.md">Español</a> | <a href="README.fr.md">Français</a> | <a href="README.hi.md">हिन्दी</a> | <a href="README.it.md">Italiano</a> | <a href="README.md">English</a>
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

## O que é isso

Um conjunto de ferramentas pronto para uso que mantém o Claude Code produtivo em repositórios grandes e multilíngues, sem sobrecarregar uma estação de trabalho Linux de 64 GB.

**Ideia central:** não carregue todo o repositório no Claude. Mantenha índices duráveis próximos ao código em contêineres com restrições de recursos. Transmita apenas as evidências mínimas necessárias de volta para o Claude por meio de um gateway HTTP leve.

## Arquitetura

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

## Início rápido

### 1. Inicialize o host

```bash
sudo ./scripts/bootstrap.sh
```

Isso instala:
- zram swap (Ubuntu) ou verifica swap-on-zram (Fedora)
- Ajustes do Sysctl (swappiness, inotify watches)
- Slices do systemd com governança MemoryHigh/Max
- Configuração do daemon Docker (driver de log local)
- claude-toolstack.service (gerenciamento de inicialização)

### 2. Configure

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. Clone os repositórios

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. Inicie o conjunto de ferramentas

```bash
docker compose up -d --build
```

### 5. Verifique

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## API do gateway

Todos os endpoints requerem o cabeçalho `x-api-key`. O gateway se vincula apenas a `127.0.0.1:8088`.

| Método | Endpoint | Propósito |
|--------|----------|---------|
| `GET` | `/v1/status` | Saúde + configuração |
| `POST` | `/v1/search/rg` | Ripgrep com proteções |
| `POST` | `/v1/file/slice` | Busca de intervalo de arquivo (máximo de 800 linhas) |
| `POST` | `/v1/index/ctags` | Criação de índice ctags (assíncrona) |
| `POST` | `/v1/symbol/ctags` | Consulta de definições de símbolos |
| `POST` | `/v1/run/job` | Execução de testes/compilações/análises permitidos |
| `GET` | `/v1/metrics` | Contadores no formato Prometheus |

Todas as respostas incluem `X-Request-ID` para correlação de ponta a ponta. Os clientes podem enviar seus próprios usando o cabeçalho `X-Request-ID`.

## CLI (`cts`)

Uma CLI Python sem dependências que envolve todos os endpoints do gateway.

### Instalação

```bash
pip install -e .
# or: pipx install -e .
```

### Configuração

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### Uso

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

### Pacotes de evidências v2 (`--format claude`)

O modo de saída `--claude` produz pacotes de evidências compactos e prontos para colar, com cabeçalhos estruturados v2. Quatro modos de pacote estão disponíveis:

| Modo | Flag | O que ele faz |
|------|------|-------------|
| `default` | `--bundle default` | Busca + correspondências classificadas + fatias de contexto |
| `error` | `--bundle error` | Consciente de rastreamento de pilha: extrai arquivos do rastreamento, aumenta a classificação |
| `symbol` | `--bundle symbol` | Definições + locais de chamada da busca |
| `change` | `--bundle change` | Diferença do Git + fatias de contexto do "hunk" |

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

Ajuste: `--evidence-files 5` (número de arquivos a serem fatiados), `--context 30` (número de linhas ao redor do resultado).

### Exemplos de curl

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

## Governança de recursos

Slices do systemd impõem MemoryHigh (limitação de velocidade) e MemoryMax (limite máximo) por grupo de serviços:

| Slice | MemoryHigh | MemoryMax | Propósito |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 GB | 4 GB | Gateway + proxy de socket |
| `claude-index` | 6 GB | 10 GB | Indexadores, busca |
| `claude-lsp` | 8 GB | 16 GB | Servidores de linguagem |
| `claude-build` | 10 GB | 18 GB | Executores de compilação/teste |
| `claude-vector` | 8 GB | 16 GB | Banco de dados vetorial (opcional) |

Estes são os valores padrão para repositórios de tamanho médio. Edite os arquivos de slice em `systemd/` para sua carga de trabalho.

Sistema operacional + espaço livre: 10-14 GB sempre reservados para o cache do sistema de arquivos, desktop, SSH.

## Segurança

### Modelo de ameaças

**O que protegemos:**
- Abuso do gateway (acesso não autorizado, esgotamento de recursos)
- Travessia de caminho (escape da raiz do repositório via `../` ou links simbólicos)
- Escalada do socket Docker (socket bruto = equivalente à raiz)
- Inundação de saída (resultados de busca/compilação ilimitados consumindo memória)

**Camadas de segurança:**

| Camada | Mecanismo |
|-------|-----------|
| Autenticação | Chave de API (`x-api-key` header), configurável |
| Rede | O gateway se vincula apenas a `127.0.0.1` |
| Docker | Proxy de socket (Tecnativa), apenas `CONTAINERS+EXEC` |
| Repositórios | Lista de permissões/proibição com suporte a padrões globais. |
| Caminhos. | "Jail" com `realpath`, rejeição de bytes nulos. |
| Comandos. | Apenas lista de permissões predefinida, sem execução arbitrária. |
| Saída. | Limite de 512 KB, truncamento de linhas. |
| Limite de taxa. | "Token bucket" por chave + IP. |
| Auditoria. | Registro em formato JSONL, chave criptografada, com rotação. |
| Contêineres. | Lista de permissões nomeada, sem curingas. |
| Recursos. | Fatias cgroup v2, limites de memória/CPU por contêiner. |

### O que o gateway não pode fazer

- Executar comandos arbitrários (apenas lista de permissões predefinida).
- Acessar repositórios fora de `/workspace/repos` (jail de caminhos).
- Modificar imagens Docker, volumes, redes ou o sistema (bloqueios do proxy).
- Retornar saída ilimitada (limite máximo de 512 KB).
- Aceitar conexões de endereços diferentes de localhost (endereço de vinculação).
- Coletar ou enviar telemetria — **sem telemetria, sem envio de dados, sem análise**.

## Estrutura de diretórios

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

## Integração com Claude Code

### Linux local

O Claude Code é executado diretamente no host. Configure o gateway como um servidor MCP ou chame-o via HTTP a partir de scripts de tarefas.

### Remoto (macOS/Windows)

Use a aba "Code" do Claude Desktop com um ambiente SSH apontando para o seu host Linux. A "fazenda" de ferramentas é executada no host; a interface gráfica permanece no seu laptop.

## Ajustes

Consulte [docs/tuning.md](docs/tuning.md) para:
- Dimensionamento de fatias com base no tamanho do repositório (pequeno/médio/grande).
- Monitoramento de PSI e detecção de sobrecarga.
- Adição de servidores de linguagem (clangd, rust-analyzer, tsserver).
- Opções de armazenamento vetorial (SQLite+FAISS, Weaviate, Milvus).
- Personalização de configurações de tarefas.

## Validação sem sobrecarga

Após a implantação, confirme:

1. **PSI total próximo de zero**: `watch -n 1 'cat /proc/pressure/memory'`
2. **Contêineres atingem MemoryHigh antes de Max**: verifique o status da fatia.
3. **SSH permanece responsivo**: durante a indexação e compilação.
4. **O isolamento funciona**: diminua o limite de um serviço, execute uma tarefa pesada e confirme que apenas esse contêiner falha.

---

Criado por <a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a>
