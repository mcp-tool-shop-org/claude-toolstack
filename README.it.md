<p align="center">
  <a href="README.ja.md">日本語</a> | <a href="README.zh.md">中文</a> | <a href="README.es.md">Español</a> | <a href="README.fr.md">Français</a> | <a href="README.hi.md">हिन्दी</a> | <a href="README.md">English</a> | <a href="README.pt-BR.md">Português (BR)</a>
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

## Cosa è questo

Una soluzione pronta all'uso che mantiene Claude Code produttivo su repository di grandi dimensioni e multilingue, senza sovraccaricare una workstation Linux da 64 GB.

**Idea principale:** non caricare l'intero repository in Claude. Mantenere indici persistenti vicino al codice, all'interno di container con risorse limitate. Trasmettere solo le informazioni più rilevanti a Claude tramite un gateway HTTP leggero.

## Architettura

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

## Guida rapida

### 1. Configurazione iniziale del server

```bash
sudo ./scripts/bootstrap.sh
```

Questo installerà:
- Swap zram (per Ubuntu) o verificherà lo swap-on-zram (per Fedora)
- Ottimizzazione di Sysctl (swappiness, monitoraggio inotify)
- Slice systemd con governance MemoryHigh/Max
- Configurazione del demone Docker (driver di log locale)
- Servizio claude-toolstack.service (gestione all'avvio)

### 2. Configurazione

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. Clonazione dei repository

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. Avvio della soluzione

```bash
docker compose up -d --build
```

### 5. Verifica

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## API del gateway

Tutti gli endpoint richiedono l'intestazione `x-api-key`. Il gateway è accessibile solo su `127.0.0.1:8088`.

| Metodo | Endpoint | Scopo |
|--------|----------|---------|
| `GET` | `/v1/status` | Stato + configurazione |
| `POST` | `/v1/search/rg` | Ricerca con filtri |
| `POST` | `/v1/file/slice` | Recupero di un intervallo di file (massimo 800 righe) |
| `POST` | `/v1/index/ctags` | Creazione di un indice ctags (asincrona) |
| `POST` | `/v1/symbol/ctags` | Interrogazione delle definizioni dei simboli |
| `POST` | `/v1/run/job` | Esecuzione di test/build/analisi consentiti |
| `GET` | `/v1/metrics` | Contatori in formato Prometheus |

Tutte le risposte includono `X-Request-ID` per la correlazione end-to-end. I client possono inviare il proprio tramite l'intestazione `X-Request-ID`.

## CLI (`cts`)

Un'interfaccia a riga di comando Python leggera che avvolge tutti gli endpoint del gateway.

### Installazione

```bash
pip install -e .
# or: pipx install -e .
```

### Configurazione

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### Utilizzo

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

### Bundle di evidenziazioni v2 (`--format claude`)

La modalità di output `--claude` produce pacchetti di evidenziazioni compatti e pronti per essere copiati, con intestazioni strutturate v2. Sono disponibili quattro modalità di bundle:

| Modalità | Flag | Cosa fa |
|------|------|-------------|
| `default` | `--bundle default` | Ricerca + corrispondenze ordinate + sezioni di contesto |
| `error` | `--bundle error` | Consapevole dello stack di chiamate: estrae i file dalla traccia, aumenta il ranking |
| `symbol` | `--bundle symbol` | Definizioni + siti di chiamata dalla ricerca |
| `change` | `--bundle change` | Differenza Git + sezioni di contesto |

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

Ottimizzazione: `--evidence-files 5` (numero di file da includere), `--context 30` (numero di righe attorno alla corrispondenza).

### Esempi curl

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

## Gestione delle risorse

Gli slice systemd applicano limiti di memoria (throttling) e limiti massimi di memoria per ogni gruppo di servizi:

| Slice | MemoryHigh | MemoryMax | Scopo |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 GB | 4 GB | Gateway + proxy socket |
| `claude-index` | 6 GB | 10 GB | Indexer, ricerca |
| `claude-lsp` | 8 GB | 16 GB | Server di linguaggio |
| `claude-build` | 10 GB | 18 GB | Esecuzione di build/test |
| `claude-vector` | 8 GB | 16 GB | Database vettoriale (opzionale) |

Questi sono i valori predefiniti per repository di dimensioni medie. Modificare i file degli slice nella directory `systemd/` in base al proprio carico di lavoro.

Sistema operativo + spazio libero: 10-14 GB sono sempre riservati per la cache del file system, il desktop e SSH.

## Sicurezza

### Modello di minaccia

**Cosa proteggiamo:**
- Abuso del gateway (accesso non autorizzato, esaurimento delle risorse)
- Attacchi di path traversal (uscita dalla radice del repository tramite `../` o collegamenti simbolici)
- Escalation del socket Docker (il socket raw equivale all'accesso root)
- Inondazione di output (risultati di ricerca/build illimitati che consumano memoria)

**Strati di sicurezza:**

| Strato | Meccanismo |
|-------|-----------|
| Autenticazione | Chiave API (`x-api-key` header), configurabile |
| Rete | Il gateway è accessibile solo da `127.0.0.1` |
| Docker | Proxy socket (Tecnativa), solo `CONTAINERS+EXEC` |
| Repository | Lista di permessi/divieti con supporto per caratteri jolly. |
| Percorsi. | "Jail" con `realpath`, rifiuto di byte null. |
| Comandi. | Solo lista di permessi predefinita, nessuna esecuzione arbitraria. |
| Output. | Limite a 512 KB, troncamento delle righe. |
| Limite di velocità. | "Token bucket" per chiave + indirizzo IP. |
| Audit. | Log in formato JSONL, chiave hashata, con rotazione. |
| Container. | Lista di permessi con nomi, senza caratteri jolly. |
| Risorse. | "Slice" di cgroup v2, limiti di memoria/CPU per container. |

### Cosa il gateway non può fare

- Eseguire comandi arbitrari (solo lista di permessi predefinita).
- Accedere a repository al di fuori di `/workspace/repos` (jail dei percorsi).
- Modificare immagini Docker, volumi, reti o il sistema (blocchi del proxy).
- Restituire output illimitato (limite massimo di 512 KB).
- Accettare connessioni da indirizzi diversi da localhost (indirizzo di binding).
- Raccogliere o inviare dati di telemetria — **nessuna telemetria, nessuna trasmissione di dati, nessuna analisi**.

## Struttura delle directory

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

## Integrazione con Claude Code

### Linux locale

Claude Code viene eseguito direttamente sull'host. Configurare il gateway come server MCP o chiamarlo tramite HTTP dagli script delle attività.

### Remoto (macOS/Windows)

Utilizzare la scheda "Code" di Claude Desktop con un ambiente SSH che punta al vostro host Linux. La "tool farm" viene eseguita sull'host; l'interfaccia grafica rimane sul vostro laptop.

## Ottimizzazione

Consultare [docs/tuning.md](docs/tuning.md) per:
- Dimensionamento degli "slice" in base alla dimensione del repository (piccolo/medio/grande).
- Monitoraggio di PSI e rilevamento di "thrash".
- Aggiunta di server di linguaggio (clangd, rust-analyzer, tsserver).
- Opzioni di "vector store" (SQLite+FAISS, Weaviate, Milvus).
- Personalizzazione dei preset delle attività.

## Validazione senza "thrash"

Dopo la distribuzione, verificare:

1. **PSI pieno vicino a zero**: `watch -n 1 'cat /proc/pressure/memory'`
2. **I container raggiungono MemoryHigh prima di Max**: controllare lo stato dello "slice".
3. **SSH rimane reattivo**: durante l'indicizzazione e le compilazioni.
4. **Il contenimento funziona**: ridurre il limite di un servizio, eseguire un'attività pesante e verificare che solo quel container si blocchi.

---

Creato da <a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a
