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

## Qué es esto

Un conjunto de herramientas listo para implementar que mantiene a Claude Code productivo en repositorios grandes y multilingües sin sobrecargar una estación de trabajo Linux de 64 GB.

**Idea principal:** No cargar todo el repositorio en Claude. Mantener índices duraderos cerca del código en contenedores con restricciones de recursos. Transmitir solo la evidencia más pequeña necesaria de vuelta a Claude a través de una puerta de enlace HTTP ligera.

## Arquitectura

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

## Cómo empezar

### 1. Configurar el host

```bash
sudo ./scripts/bootstrap.sh
```

Esto instala:
- zram swap (Ubuntu) o verifica swap-on-zram (Fedora)
- Ajuste de Sysctl (swappiness, monitores inotify)
- Slices de systemd con gobernanza MemoryHigh/Max
- Configuración del demonio Docker (controlador de registro local)
- claude-toolstack.service (gestión de inicio)

### 2. Configurar

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. Clonar repositorios

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. Iniciar el conjunto de herramientas

```bash
docker compose up -d --build
```

### 5. Verificar

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## API de la puerta de enlace

Todos los puntos finales requieren el encabezado `x-api-key`. La puerta de enlace solo se vincula a `127.0.0.1:8088`.

| Método | Punto final | Propósito |
|--------|----------|---------|
| `GET` | `/v1/status` | Estado + configuración |
| `POST` | `/v1/search/rg` | Ripgrep con protecciones |
| `POST` | `/v1/file/slice` | Obtener rango de archivo (máximo 800 líneas) |
| `POST` | `/v1/index/ctags` | Crear índice ctags (asíncrono) |
| `POST` | `/v1/symbol/ctags` | Consultar definiciones de símbolos |
| `POST` | `/v1/run/job` | Ejecutar pruebas/compilación/análisis permitidas |
| `GET` | `/v1/metrics` | Contadores en formato Prometheus |

Todas las respuestas incluyen `X-Request-ID` para la correlación de extremo a extremo. Los clientes pueden enviar su propio ID a través del encabezado `X-Request-ID`.

## CLI (`cts`)

Una CLI de Python sin dependencias que envuelve todos los puntos finales de la puerta de enlace.

### Instalar

```bash
pip install -e .
# or: pipx install -e .
```

### Configurar

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

### Paquetes de evidencia v2 (`--format claude`)

El modo de salida `--claude` produce paquetes de evidencia compactos y listos para pegar con encabezados estructurados de la versión 2. Cuatro modos de paquete están disponibles:

| Modo | Bandera | Qué hace |
|------|------|-------------|
| `default` | `--bundle default` | Búsqueda + coincidencias clasificadas + fragmentos de contexto |
| `error` | `--bundle error` | Con conocimiento de la traza: extrae archivos de la traza, aumenta la clasificación |
| `symbol` | `--bundle symbol` | Definiciones + sitios de llamada de la búsqueda |
| `change` | `--bundle change` | Diferencia de Git + fragmentos de contexto |

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

Ajuste: `--evidence-files 5` (archivos a fragmentar), `--context 30` (líneas alrededor del resultado).

### Ejemplos de curl

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

## Gobernanza de recursos

Los slices de systemd imponen MemoryHigh (limitación) y MemoryMax (límite máximo) por grupo de servicios:

| Slice | MemoryHigh | MemoryMax | Propósito |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 GB | 4 GB | Puerta de enlace + proxy de socket |
| `claude-index` | 6 GB | 10 GB | Indexadores, búsqueda |
| `claude-lsp` | 8 GB | 16 GB | Servidores de lenguaje |
| `claude-build` | 10 GB | 18 GB | Ejecutores de compilación/pruebas |
| `claude-vector` | 8 GB | 16 GB | Base de datos vectorial (opcional) |

Estos son los valores predeterminados para repositorios medianos. Edite los archivos de slice en `systemd/` para su carga de trabajo.

SO + espacio libre: siempre se reservan de 10 a 14 GB para la caché del sistema de archivos, el escritorio y SSH.

## Seguridad

### Modelo de amenazas

**Contra qué nos protegemos:**
- Abuso de la puerta de enlace (acceso no autorizado, agotamiento de recursos)
- Recorrido de ruta (escapar de la raíz del repositorio a través de `../` o enlaces simbólicos)
- Elevación del socket de Docker (socket sin formato = equivalente a root)
- Inundación de resultados (resultados de búsqueda/compilación ilimitados que consumen memoria)

**Capas de seguridad:**

| Capa | Mecanismo |
|-------|-----------|
| Autenticación | Clave de API (`x-api-key` header), configurable |
| Red | La puerta de enlace solo se vincula a `127.0.0.1` |
| Docker | Proxy de socket (Tecnativa), solo `CONTAINERS+EXEC` |
| Repositorios | Lista de permitidos/denegados con soporte para comodines. |
| Rutas. | "Jail" con `realpath`, rechazo de bytes nulos. |
| Comandos. | Solo lista de permitidos predefinida, sin ejecución arbitraria. |
| Salida. | Límite de 512 KB, truncamiento de líneas. |
| Límite de velocidad. | "Token bucket" por clave + IP. |
| Auditoría. | Registro en formato JSONL, clave encriptada, rotación de registros. |
| Contenedores. | Lista de permitidos con nombre, sin comodines. |
| Recursos. | "Slices" de cgroup v2, límites de memoria/CPU por contenedor. |

### Lo que la puerta de enlace no puede hacer

- Ejecutar comandos arbitrarios (solo lista de permitidos predefinida).
- Acceder a repositorios fuera de `/workspace/repos` (jaula de rutas).
- Modificar imágenes de Docker, volúmenes, redes o el sistema (bloqueos del proxy).
- Devolver una salida ilimitada (límite máximo de 512 KB).
- Aceptar conexiones desde direcciones que no sean localhost (dirección de enlace).
- Recopilar o enviar datos de telemetría: **no hay telemetría, no hay envío de información, no hay análisis**.

## Estructura de directorios

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

## Integración con Claude Code

### Linux local

Claude Code se ejecuta directamente en el host. Configure la puerta de enlace como un servidor MCP o llámela a través de HTTP desde los scripts de tareas.

### Remoto (macOS/Windows)

Utilice la pestaña "Code" de Claude Desktop con un entorno SSH que apunte a su host Linux. La granja de herramientas se ejecuta en el host; la interfaz gráfica permanece en su computadora portátil.

## Ajustes

Consulte [docs/tuning.md](docs/tuning.md) para:
- Tamaño de los "slices" según el tamaño del repositorio (pequeño/mediano/grande).
- Monitoreo de PSI y detección de sobrecarga.
- Agregar servidores de lenguaje (clangd, rust-analyzer, tsserver).
- Opciones de almacenamiento vectorial (SQLite+FAISS, Weaviate, Milvus).
- Personalización de los ajustes de las tareas.

## Validación sin sobrecarga

Después de la implementación, confirme:

1. **PSI completo cercano a cero**: `watch -n 1 'cat /proc/pressure/memory'`
2. **Los contenedores alcanzan MemoryHigh antes de Max**: verifique el estado del "slice".
3. **SSH permanece receptivo**: durante la indexación y las compilaciones.
4. **El aislamiento funciona**: reduzca el límite de un servicio, ejecute una tarea pesada y confirme que solo ese contenedor falla.

---

Creado por <a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a>.
