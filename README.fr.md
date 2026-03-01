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

## Ce qu'est ceci

Un ensemble de logiciels prêt à être déployé qui permet à Claude Code de fonctionner efficacement sur de grands référentiels multilingues, sans surcharger une station de travail Linux de 64 Go.

**Idée principale :** Ne chargez pas le référentiel dans Claude. Conservez des index durables à proximité du code, dans des conteneurs avec une gestion des ressources. Transmettez uniquement les informations les plus pertinentes à Claude via une passerelle HTTP légère.

## Architecture

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

## Démarrage rapide

### 1. Initialisation du serveur

```bash
sudo ./scripts/bootstrap.sh
```

Cela installe :
- zram swap (Ubuntu) ou vérifie swap-on-zram (Fedora)
- Optimisation de Sysctl (swappiness, montages inotify)
- Tranches systemd avec gestion de la mémoire MemoryHigh/Max
- Configuration du démon Docker (pilote de journalisation local)
- claude-toolstack.service (gestion du démarrage)

### 2. Configuration

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. Clonage des référentiels

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. Démarrage de l'ensemble de logiciels

```bash
docker compose up -d --build
```

### 5. Vérification

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## API de la passerelle

Tous les points de terminaison nécessitent l'en-tête `x-api-key`. La passerelle est uniquement accessible sur `127.0.0.1:8088`.

| Méthode | Point de terminaison | Fonction |
|--------|----------|---------|
| `GET` | `/v1/status` | État + configuration |
| `POST` | `/v1/search/rg` | Recherche avec filtres |
| `POST` | `/v1/file/slice` | Récupération d'une plage de fichiers (maximum 800 lignes) |
| `POST` | `/v1/index/ctags` | Création d'un index ctags (asynchrone) |
| `POST` | `/v1/symbol/ctags` | Requête des définitions de symboles |
| `POST` | `/v1/run/job` | Exécution de tests/constructions/analyses autorisés |
| `GET` | `/v1/metrics` | Compteurs au format Prometheus |

Toutes les réponses incluent `X-Request-ID` pour la corrélation de bout en bout. Les clients peuvent envoyer leur propre ID via l'en-tête `X-Request-ID`.

## CLI (`cts`)

Une CLI Python sans dépendances qui encapsule tous les points de terminaison de la passerelle.

### Installation

```bash
pip install -e .
# or: pipx install -e .
```

### Configuration

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### Utilisation

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

### Paquets de preuves v2 (`--format claude`)

Le mode de sortie `--claude` génère des paquets de preuves compacts, prêts à être copiés, avec des en-têtes structurés v2. Quatre modes de paquet sont disponibles :

| Mode | Indicateur | Ce qu'il fait |
|------|------|-------------|
| `default` | `--bundle default` | Recherche + correspondances classées + extraits de contexte |
| `error` | `--bundle error` | Conscient des traces de pile : extrait les fichiers de la trace, améliore le classement |
| `symbol` | `--bundle symbol` | Définitions + sites d'appel à partir de la recherche |
| `change` | `--bundle change` | Diff Git + extraits de contexte |

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

Réglage : `--evidence-files 5` (nombre de fichiers à extraire), `--context 30` (nombre de lignes autour de la correspondance).

### Exemples curl

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

## Gestion des ressources

Les tranches systemd appliquent MemoryHigh (limitation) et MemoryMax (limite stricte) par groupe de services :

| Tranche | MemoryHigh | MemoryMax | Fonction |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 Go | 4 Go | Passerelle + proxy de socket |
| `claude-index` | 6 Go | 10 Go | Indexeurs, recherche |
| `claude-lsp` | 8 Go | 16 Go | Serveurs de langage |
| `claude-build` | 10 Go | 18 Go | Exécuteurs de construction/tests |
| `claude-vector` | 8 Go | 16 Go | Base de données vectorielle (facultatif) |

Ce sont les valeurs par défaut pour les référentiels de taille moyenne. Modifiez les fichiers de tranche dans `systemd/` en fonction de votre charge de travail.

Système d'exploitation + marge de sécurité : 10 à 14 Go sont toujours réservés pour le cache du système de fichiers, le bureau et SSH.

## Sécurité

### Modèle de menace

**Ce contre quoi nous nous protégeons :**
- Abus de la passerelle (accès non autorisé, épuisement des ressources)
- Traversal de chemin (échappement de la racine du référentiel via `../` ou des liens symboliques)
- Escalade de socket Docker (socket brut = équivalent à root)
- Surcharge de la sortie (résultats de recherche/construction illimités consommant de la mémoire)

**Couches de sécurité :**

| Couche | Mécanisme |
|-------|-----------|
| Authentification | Clé API (`x-api-key` en-tête), configurable |
| Réseau | La passerelle est uniquement accessible sur `127.0.0.1` |
| Docker | Proxy de socket (Tecnativa), uniquement `CONTAINERS+EXEC` |
| Référentiels | Liste blanche/liste noire avec prise en charge des caractères génériques. |
| Chemins d'accès. | Environnement restreint par `realpath`, rejet des octets nuls. |
| Commandes. | Seule la liste blanche prédéfinie est autorisée, aucune exécution arbitraire. |
| Sortie. | Limite de 512 Ko, troncature des lignes. |
| Limitation du débit. | "Bucket" de jetons par clé + adresse IP. |
| Audit. | Journalisation au format JSONL, clé hachée, rotation des journaux. |
| Conteneurs. | Liste blanche nommée, sans caractères génériques. |
| Ressources. | Slices cgroup v2, limites de mémoire/CPU par conteneur. |

### Ce que la passerelle ne peut pas faire

- Exécuter des commandes arbitraires (seule la liste blanche prédéfinie est autorisée).
- Accéder aux dépôts situés en dehors de `/workspace/repos` (environnement restreint par chemin).
- Modifier les images Docker, les volumes, les réseaux ou le système (blocage par proxy).
- Renvoyer une sortie illimitée (limite stricte de 512 Ko).
- Accepter les connexions provenant d'adresses autres que localhost (adresse de liaison).
- Collecter ou envoyer des données télémétriques — **pas de télémétrie, pas de signalement, pas d'analyse**.

## Structure des répertoires

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

## Intégration Claude Code

### Linux local

Claude Code s'exécute directement sur l'hôte. Configurez la passerelle en tant que serveur MCP ou appelez-la via HTTP à partir des scripts de tâches.

### Distante (macOS/Windows)

Utilisez l'onglet Code de Claude Desktop avec un environnement SSH pointant vers votre hôte Linux. La ferme d'outils s'exécute sur l'hôte ; l'interface graphique reste sur votre ordinateur portable.

## Optimisation

Consultez [docs/tuning.md](docs/tuning.md) pour :
- Dimensionnement des slices en fonction de la taille du dépôt (petit/moyen/grand).
- Surveillance de PSI et détection des blocages.
- Ajout de serveurs de langage (clangd, rust-analyzer, tsserver).
- Options de stockage vectoriel (SQLite+FAISS, Weaviate, Milvus).
- Personnalisation des paramètres des tâches.

## Validation sans blocage

Après le déploiement, vérifiez :

1. **PSI complet proche de zéro** : `watch -n 1 'cat /proc/pressure/memory'`
2. **Les conteneurs atteignent MemoryHigh avant Max** : vérifiez l'état du slice.
3. **SSH reste réactif** : pendant l'indexation et la compilation.
4. **L'isolation fonctionne** : réduisez la limite d'un service, exécutez une tâche lourde, vérifiez que seul ce conteneur plante.

---

Créé par <a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a>.
