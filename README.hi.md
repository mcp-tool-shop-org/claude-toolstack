<p align="center">
  <a href="README.ja.md">日本語</a> | <a href="README.zh.md">中文</a> | <a href="README.es.md">Español</a> | <a href="README.fr.md">Français</a> | <a href="README.md">English</a> | <a href="README.it.md">Italiano</a> | <a href="README.pt-BR.md">Português (BR)</a>
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

## यह क्या है।

एक ऐसा तैयार समाधान जो क्लाउड कोड को बड़े, बहु-भाषा वाले रिपॉजिटरी पर कुशलतापूर्वक काम करने में मदद करता है, बिना किसी 64 जीबी लिनक्स वर्कस्टेशन को अत्यधिक दबाव में डाले।

**मुख्य विचार:** क्लाउड (Claude) में पूरी रिपॉजिटरी (repository) को लोड न करें। कोड के करीब, संसाधनों की सीमा वाले कंटेनरों में स्थायी इंडेक्स (indexes) रखें। केवल आवश्यक न्यूनतम जानकारी ही एक सरल एचटीटीपी गेटवे (HTTP gateway) के माध्यम से क्लाउड को भेजें।

## आर्किटेक्चर।

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

## शुरुआत कैसे करें।

### 1. होस्ट सिस्टम को शुरू करें।

```bash
sudo ./scripts/bootstrap.sh
```

यह निम्नलिखित चीजें स्थापित करता है:
- ज़ेडराम स्वैप (उबंटू) या स्वैप-ऑन-ज़ेडराम की पुष्टि (फेडोरा)
- सिस्टमctl ट्यूनिंग (स्वैपनेस, इनोटिफाई वॉचेस)
- सिस्टमडी स्लाइस, जिनमें मेमोरीहाई/मैक्स गवर्नेंस है
- डॉकर डेमॉन कॉन्फ़िगरेशन (स्थानीय लॉग ड्राइवर)
- क्लाउड-टूलस्टैक.सर्विस (बूट प्रबंधन)

### 2. कॉन्फ़िगर करें।

```bash
cp .env.example .env
# Edit .env: set API_KEY, ALLOWED_REPOS, etc.
```

### 3. रिपॉजिटरी की प्रतियां बनाएं।

```bash
# Repos go under /workspace/repos/<org>/<repo>
git clone https://github.com/myorg/myrepo /workspace/repos/myorg/myrepo
```

### 4. स्टैक को शुरू करें।

```bash
docker compose up -d --build
```

### 5. पुष्टि करें।

```bash
./scripts/smoke-test.sh "$API_KEY" myorg/myrepo
./scripts/health.sh
```

## गेटवे एपीआई (Gateway API)

सभी एंडपॉइंट्स को `x-api-key` हेडर की आवश्यकता होती है। गेटवे केवल `127.0.0.1:8088` पर ही काम करता है।

| विधि। | अंतिम बिंदु। | उद्देश्य। |
|--------|----------|---------|
| `GET` | `/v1/status` | स्वास्थ्य + कॉन्फ़िगरेशन। |
| `POST` | `/v1/search/rg` | रिपग्रेप, सुरक्षा उपायों के साथ। |
| `POST` | `/v1/file/slice` | फ़ाइल का एक भाग (अधिकतम 800 पंक्तियाँ) प्राप्त करें। |
| `POST` | `/v1/index/ctags` | सीटैग्स इंडेक्स बनाएं (असिंक्रोनस रूप से)। |
| `POST` | `/v1/symbol/ctags` | क्वेरी सिंबल की परिभाषाएँ। |
| `POST` | `/v1/run/job` | अनुमत परीक्षण/बिल्ड/लिंट चलाएं। |
| `GET` | `/v1/metrics` | प्रोमेथियस-प्रारूप के काउंटर। |

सभी प्रतिक्रियाओं में `X-Request-ID` शामिल होता है, जिसका उपयोग शुरुआत से अंत तक जानकारी को जोड़ने के लिए किया जाता है। क्लाइंट अपनी जानकारी भी `X-Request-ID` हेडर के माध्यम से भेज सकते हैं।

## सीएलआई (`सीटीएस`)

एक ऐसा पाइथन कमांड-लाइन इंटरफेस (CLI) जो किसी भी बाहरी निर्भरता पर निर्भर नहीं करता है और सभी गेटवे एंडपॉइंट्स को एक साथ जोड़ता है।

### स्थापित करें।

```bash
pip install -e .
# or: pipx install -e .
```

### कॉन्फ़िगर करें।

```bash
export CLAUDE_TOOLSTACK_API_KEY=<your-key>
export CLAUDE_TOOLSTACK_URL=http://127.0.0.1:8088  # default
```

### उपयोग

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

### एविडेंस बंडल्स संस्करण 2 (`--format claude` विकल्प के साथ)।

"--claude" आउटपुट मोड संक्षिप्त, उपयोग के लिए तैयार प्रमाण सामग्री तैयार करता है, जिसमें संरचित v2 हेडर होते हैं। चार प्रकार की बंडलिंग विधियां उपलब्ध हैं:

| मोड। | ध्वज। | यह क्या करता है। |
|------|------|-------------|
| `default` | `--bundle default` | खोज + क्रमबद्ध मिलान + प्रासंगिक जानकारी के अंश। |
| `error` | `--bundle error` | स्टैक-ट्रेस के बारे में जानकारी: यह ट्रेस से फ़ाइलें निकालता है और रैंकिंग में सुधार करता है। |
| `symbol` | `--bundle symbol` | परिभाषाएँ + खोज से प्राप्त संबंधित संदर्भ। |
| `change` | `--bundle change` | गिट डिफ़ (Git diff) में खंडों के साथ संदर्भ शामिल करना। |

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

ट्यूनिंग: `--evidence-files 5` (उन फ़ाइलों की संख्या जिन्हें विभाजित किया जाएगा), `--context 30` (मिलान वाले भाग के आसपास की पंक्तियों की संख्या)।

### curl के उदाहरण।

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

## संसाधन प्रबंधन।

सिस्टमडी स्लाइस प्रत्येक सेवा समूह के लिए "मेमोरीहाई" (गति सीमा) और "मेमोरीमैक्स" (कठोर सीमा) लागू करते हैं:

| स्लाइस। | मेमोरीहाई (MemoryHigh) | मेमोरीमैक्स। | उद्देश्य। |
|-------|-----------|-----------|---------|
| `claude-gw` | 2 जीबी. | 4 जीबी. | गेटवे + सॉकेट प्रॉक्सी। |
| `claude-index` | 6 जीबी. | 10 गीगाबाइट। | इंडेक्सर, खोज। |
| `claude-lsp` | 8 जीबी. | 16 जीबी. | भाषा सर्वर। |
| `claude-build` | 10 गीगाबाइट। | 18 गीगाबाइट। | बिल्ड/परीक्षण निष्पादनकर्ता (Build/Test Runners) |
| `claude-vector` | 8 जीबी. | 16 जीबी. | वेक्टर डेटाबेस (वैकल्पिक)। |

ये डिफ़ॉल्ट सेटिंग्स हैं जो मध्यम आकार के रिपॉजिटरी के लिए उपयुक्त हैं। अपने विशिष्ट कार्यभार के अनुसार, `systemd/` फ़ोल्डर में मौजूद फ़ाइलों को संपादित करें।

ऑपरेटिंग सिस्टम (ओएस) में, 10-14 जीबी स्टोरेज हमेशा फाइल सिस्टम कैश, डेस्कटॉप और एसएसएच (SSH) के लिए आरक्षित रहता है।

## सुरक्षा।

### खतरे का मॉडल।

**हम किन खतरों से सुरक्षा करते हैं:**
- गेटवे का दुरुपयोग (अनधिकृत पहुंच, संसाधनों का अत्यधिक उपयोग)
- पाथ ट्रैवर्सल (रिपॉजिटरी के मूल फ़ोल्डर से बाहर निकलने की कोशिश, जैसे कि `../` का उपयोग करके या सिंबॉलिक लिंक के माध्यम से)
- डॉकर सॉकेट का दुरुपयोग (रॉ सॉकेट का उपयोग करना, जो रूट उपयोगकर्ता के समान अधिकार प्रदान करता है)
- आउटपुट का अत्यधिक प्रवाह (असीमित खोज/बिल्ड परिणामों के कारण मेमोरी का अत्यधिक उपयोग)

सुरक्षा परतें:

| लेयर (परत) | तंत्र। |
|-------|-----------|
| माफ़ करना, लेकिन "Auth" शब्द का अर्थ स्पष्ट नहीं है। कृपया अधिक जानकारी दें ताकि मैं इसका सही अनुवाद कर सकूं। | एपीआई कुंजी (`x-api-key` हेडर), जिसे बदला जा सकता है। |
| नेटवर्क। | गेटवे केवल `127.0.0.1` से जुड़ता है। |
| डॉकर। | सॉकेट प्रॉक्सी (टेक्नाटिवा), केवल `कंटेनर्स+एग्जीक्यूट`। |
| रिपॉज़ (Repos) | अनुमत/अस्वीकृत सूची, जिसमें ग्लोब समर्थन शामिल है। |
| पथ | `realpath` सुरक्षा, शून्य बाइट अस्वीकृति। |
| कमांड | केवल पूर्वनिर्धारित अनुमत सूची, कोई मनमाना निष्पादन नहीं। |
| आउटपुट | 512 KB की सीमा, पंक्ति काटना। |
| दर सीमा | प्रत्येक कुंजी+आईपी के लिए टोकन बकेट। |
| लेखा परीक्षा | JSONL लॉग, कुंजी हैश की गई, घुमाई गई। |
| कंटेनर | नाम वाली अनुमत सूची, कोई वाइल्डकार्ड नहीं। |
| संसाधन | cgroup v2 स्लाइस, प्रति-कंटेनर मेमोरी/सीपीयू सीमाएं। |

### गेटवे क्या नहीं कर सकता

- मनमाने कमांड निष्पादित करना (केवल पूर्वनिर्धारित अनुमत सूची)।
- `/workspace/repos` के बाहर रिपॉजिटरी तक पहुंच (पथ सुरक्षा)।
- डॉकर छवियों, वॉल्यूम, नेटवर्क या सिस्टम को बदलना (प्रॉक्सी अवरोध)।
- असीमित आउटपुट लौटाना (512 KB की सख्त सीमा)।
- गैर-लोकलहोस्ट से कनेक्शन स्वीकार करना (बाइंड एड्रेस)।
- टेलीमेट्री एकत्र करना या भेजना — **कोई टेलीमेट्री नहीं, कोई डेटा नहीं भेजा जाएगा, कोई विश्लेषण नहीं।**

## डायरेक्टरी संरचना

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

## क्लाउड कोड एकीकरण

### स्थानीय लिनक्स

क्लाउड कोड सीधे होस्ट पर चलता है। गेटवे को एक MCP सर्वर के रूप में कॉन्फ़िगर करें या इसे कार्य स्क्रिप्ट से HTTP के माध्यम से कॉल करें।

### रिमोट (macOS/विंडोज)

अपने लिनक्स होस्ट की ओर इशारा करने वाले SSH वातावरण के साथ क्लाउड डेस्कटॉप के कोड टैब का उपयोग करें। टूल फ़ार्म होस्ट पर चलता है; GUI आपके लैपटॉप पर रहता है।

## ट्यूनिंग

निम्नलिखित के लिए [docs/tuning.md](docs/tuning.md) देखें:
- रिपॉजिटरी आकार के आधार पर स्लाइस का आकार (छोटा/मध्यम/बड़ा)।
- PSI निगरानी और थ्रैश का पता लगाना।
- भाषा सर्वर जोड़ना (clangd, rust-analyzer, tsserver)।
- वेक्टर स्टोर विकल्प (SQLite+FAISS, Weaviate, Milvus)।
- जॉब प्रीसेट अनुकूलन।

## थ्रैश-मुक्त सत्यापन

तैनाती के बाद, पुष्टि करें:

1. **PSI पूर्ण लगभग शून्य**: `watch -n 1 'cat /proc/pressure/memory'`
2. **कंटेनर मैक्स से पहले मेमोरीहाई तक पहुंचते हैं**: स्लाइस स्थिति की जांच करें।
3. **SSH प्रतिक्रियाशील रहता है**: अनुक्रमण और बिल्ड के दौरान।
4. **कंटेनमेंट काम करता है**: एक सेवा की सीमा को कम करें, एक भारी कार्य चलाएं, पुष्टि करें कि केवल वह कंटेनर मरता है।

---

<a href="https://mcp-tool-shop.github.io/">MCP Tool Shop</a> द्वारा निर्मित।
