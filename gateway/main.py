"""
Claude Toolstack Gateway — thin HTTP API for bounded code intelligence.

Endpoints:
  POST /v1/search/rg     — ripgrep with guardrails
  POST /v1/file/slice    — fetch file range
  POST /v1/index/ctags   — trigger ctags build (async job)
  POST /v1/symbol/ctags  — query symbol defs from tags
  POST /v1/run/job       — run allowlisted test/build/lint
  GET  /v1/status        — health + config
  GET  /v1/metrics       — Prometheus-format counters

Security layers:
  - API key auth (x-api-key header)
  - Per-repo allowlist / denylist
  - Token bucket rate limiting
  - Path jail (realpath checks)
  - Allowlisted container exec targets
  - JSONL audit log with key hashing
  - Output truncation (512 KB default)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import docker
import yaml
from docker.errors import APIError, DockerException, NotFound
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(os.getenv("REPO_ROOT", "/repos")).resolve()
CACHE_ROOT = Path(os.getenv("CACHE_ROOT", "/cache")).resolve()

API_KEY = os.getenv("API_KEY", "")
RG_THREADS = int(os.getenv("RG_THREADS", "4"))
MAX_MATCHES_DEFAULT = int(os.getenv("MAX_MATCHES", "200"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", str(512 * 1024)))
REQUEST_TIMEOUT_SEC = float(os.getenv("REQUEST_TIMEOUT_SEC", "20"))

DOCKER_HOST = os.getenv("DOCKER_HOST", "")

ALLOWED_CONTAINERS = {
    c.strip() for c in os.getenv("ALLOWED_CONTAINERS", "").split(",") if c.strip()
}

RG_CONCURRENCY = int(os.getenv("RG_CONCURRENCY", "2"))
JOB_CONCURRENCY = int(os.getenv("JOB_CONCURRENCY", "1"))

rg_sem = asyncio.Semaphore(RG_CONCURRENCY)
job_sem = asyncio.Semaphore(JOB_CONCURRENCY)

# Per-repo access control
ALLOWED_REPOS = [
    s.strip() for s in os.getenv("ALLOWED_REPOS", "").split(",") if s.strip()
]
DENIED_REPOS = [
    s.strip() for s in os.getenv("DENIED_REPOS", "").split(",") if s.strip()
]

# Rate limiting
RATE_LIMIT_RPS = float(os.getenv("RATE_LIMIT_RPS", "2"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "10"))
RATE_LIMIT_SCOPE = os.getenv("RATE_LIMIT_SCOPE", "key").lower()
RATE_LIMIT_BACKEND = os.getenv(
    "RATE_LIMIT_BACKEND", "memory"
).lower()  # "memory" or "redis"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Audit logging
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "/audit/audit.jsonl")
AUDIT_LOG_MAX_MB = int(os.getenv("AUDIT_LOG_MAX_MB", "50"))
AUDIT_LOG_BACKUPS = int(os.getenv("AUDIT_LOG_BACKUPS", "5"))

# Default excludes for rg
DEFAULT_EXCLUDES = [
    ".git/",
    "node_modules/",
    "dist/",
    "build/",
    "target/",
    ".next/",
    ".turbo/",
    ".cache/",
    "vendor/",
]

# Repos config file (optional, for preset mapping + repo-specific excludes)
REPOS_YAML_PATH = os.getenv("REPOS_YAML_PATH", "/app/repos.yaml")

# ---------------------------------------------------------------------------
# Metrics counters (lightweight, in-memory)
# ---------------------------------------------------------------------------

_metrics = {
    "requests_total": 0,
    "requests_by_status": {},  # status_code -> count
    "rate_limit_429": 0,
    "docker_exec_total": 0,
    "docker_exec_errors": 0,
    "truncations": 0,
    "search_total": 0,
    "slice_total": 0,
    "ctags_index_total": 0,
    "ctags_query_total": 0,
    "job_total": 0,
}
_metrics_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------

audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False

Path(AUDIT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)

_audit_handler = RotatingFileHandler(
    AUDIT_LOG_PATH,
    maxBytes=AUDIT_LOG_MAX_MB * 1024 * 1024,
    backupCount=AUDIT_LOG_BACKUPS,
)
_audit_handler.setFormatter(logging.Formatter("%(message)s"))
audit_logger.addHandler(_audit_handler)


def audit(event: Dict[str, Any], request_id: str = "") -> None:
    event.setdefault("ts", time.time())
    if request_id:
        event["request_id"] = request_id
    audit_logger.info(json.dumps(event, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Rate limiter (token bucket)
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    tokens: float
    last: float


_buckets: Dict[str, _Bucket] = {}
_buckets_lock = asyncio.Lock()

# Optional Redis client (lazy init)
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        try:
            import redis

            _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            _redis_client.ping()
        except Exception as e:
            logging.warning(
                f"Redis rate limiter unavailable, falling back to memory: {e}"
            )
            return None
    return _redis_client


def _rl_key(api_key: str, client_ip: str) -> str:
    if RATE_LIMIT_SCOPE == "ip":
        return f"rl:ip:{client_ip}"
    if RATE_LIMIT_SCOPE == "key+ip":
        return f"rl:keyip:{api_key}:{client_ip}"
    return f"rl:key:{api_key}"


async def _rate_limit_check(api_key: str, client_ip: str) -> None:
    if RATE_LIMIT_RPS <= 0 or RATE_LIMIT_BURST <= 0:
        return

    if RATE_LIMIT_BACKEND == "redis":
        await _rate_limit_redis(api_key, client_ip)
    else:
        await _rate_limit_memory(api_key, client_ip)


async def _rate_limit_memory(api_key: str, client_ip: str) -> None:
    key = _rl_key(api_key, client_ip)
    now = time.time()

    async with _buckets_lock:
        b = _buckets.get(key)
        if b is None:
            b = _Bucket(tokens=float(RATE_LIMIT_BURST), last=now)
            _buckets[key] = b

        elapsed = max(0.0, now - b.last)
        b.tokens = min(float(RATE_LIMIT_BURST), b.tokens + elapsed * RATE_LIMIT_RPS)
        b.last = now

        if b.tokens < 1.0:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        b.tokens -= 1.0


async def _rate_limit_redis(api_key: str, client_ip: str) -> None:
    """Token bucket via Redis MULTI/EXEC for multi-instance durability."""
    r = _get_redis()
    if r is None:
        # Fallback to memory if Redis unavailable
        return await _rate_limit_memory(api_key, client_ip)

    key = _rl_key(api_key, client_ip)
    now = time.time()
    ttl = int(RATE_LIMIT_BURST / max(RATE_LIMIT_RPS, 0.01)) + 60

    def _check():
        pipe = r.pipeline(True)
        try:
            pipe.watch(key)
            raw = pipe.hgetall(key)
            tokens = float(raw.get("t", RATE_LIMIT_BURST))
            last = float(raw.get("l", now))
            elapsed = max(0.0, now - last)
            tokens = min(float(RATE_LIMIT_BURST), tokens + elapsed * RATE_LIMIT_RPS)
            if tokens < 1.0:
                return False
            tokens -= 1.0
            pipe.multi()
            pipe.hset(key, mapping={"t": str(tokens), "l": str(now)})
            pipe.expire(key, ttl)
            pipe.execute()
            return True
        except Exception:
            return True  # fail open on Redis errors
        finally:
            pipe.reset()

    allowed = await asyncio.to_thread(_check)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Claude Tooling Gateway", version="0.2.0")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RgSearchRequest(BaseModel):
    repo: str = Field(..., description="org/repo identifier")
    query: str
    fixed_string: bool = False
    case_sensitive: bool = False
    path_globs: Optional[List[str]] = None
    extra_excludes: Optional[List[str]] = None
    max_matches: int = MAX_MATCHES_DEFAULT


class FileSliceRequest(BaseModel):
    repo: str
    path: str
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)


class CtagsIndexRequest(BaseModel):
    repo: str


class CtagsQueryRequest(BaseModel):
    repo: str
    symbol: str


class RunJobRequest(BaseModel):
    repo: str
    job: str = Field(..., description="One of: test, build, lint")
    preset: str = Field(
        "",
        description="Preset: node, pnpm, yarn, python, rust, go, java, dotnet, bazel, cmake. "
        "If empty, looks up default in repos.yaml.",
    )
    args: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Auth + security helpers
# ---------------------------------------------------------------------------


def _require_api_key(key: str) -> None:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _repo_matches(pattern: str, repo_id: str) -> bool:
    if pattern == "*":
        return True
    if "*" not in pattern:
        return pattern == repo_id
    regex = "^" + re.escape(pattern).replace(r"\*", "[^/]+") + "$"
    return re.match(regex, repo_id) is not None


def _enforce_repo_allowlist(repo_id: str) -> None:
    for p in DENIED_REPOS:
        if _repo_matches(p, repo_id):
            raise HTTPException(
                status_code=403, detail=f"Repo denied by policy: {repo_id}"
            )
    if not ALLOWED_REPOS:
        raise HTTPException(
            status_code=403, detail="No ALLOWED_REPOS configured (deny by default)"
        )
    for p in ALLOWED_REPOS:
        if _repo_matches(p, repo_id):
            return
    raise HTTPException(status_code=403, detail=f"Repo not allowed: {repo_id}")


def _normalize_repo(repo: str) -> Tuple[str, str]:
    repo = repo.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise HTTPException(
            status_code=400, detail="Invalid repo format; expected org/repo"
        )
    org, name = repo.split("/", 1)
    return repo, f"{org}__{name}"


def _resolve_repo_path(repo_id: str) -> Path:
    org, name = repo_id.split("/", 1)
    candidate = REPO_ROOT / org / name
    if not candidate.exists():
        raise HTTPException(
            status_code=404, detail=f"Repo not found on host: {repo_id}"
        )
    resolved = candidate.resolve()
    if not str(resolved).startswith(str(REPO_ROOT) + os.sep):
        raise HTTPException(status_code=403, detail="Repo path escapes REPO_ROOT")
    return resolved


def _resolve_file_path(repo_path: Path, rel_path: str) -> Path:
    if rel_path.startswith("/") or "\x00" in rel_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    rel_path = rel_path.lstrip("./")
    candidate = repo_path / rel_path
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="File not found")
    resolved = candidate.resolve()
    if not str(resolved).startswith(str(repo_path) + os.sep):
        raise HTTPException(status_code=403, detail="File path escapes repo root")
    return resolved


def _truncate_bytes(data: bytes, limit: int = MAX_RESPONSE_BYTES) -> Tuple[bytes, bool]:
    if len(data) <= limit:
        return data, False
    return data[:limit], True


def _cache_dir_for(repo_norm: str) -> Path:
    d = (CACHE_ROOT / repo_norm).resolve()
    if not str(d).startswith(str(CACHE_ROOT) + os.sep) and d != CACHE_ROOT:
        raise HTTPException(status_code=500, detail="Cache root misconfigured")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_container_allowed(name: str) -> None:
    if not ALLOWED_CONTAINERS:
        raise HTTPException(status_code=500, detail="ALLOWED_CONTAINERS not configured")
    if name not in ALLOWED_CONTAINERS:
        raise HTTPException(status_code=403, detail=f"Container not allowed: {name}")


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _load_repos_yaml() -> Dict[str, Any]:
    """Load repos.yaml if it exists. Returns {repo_id: config_dict}."""
    path = Path(REPOS_YAML_PATH)
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return (data or {}).get("repos", {}) or {}
    except Exception:
        return {}


def _repo_preset_from_yaml(repo_id: str) -> Optional[str]:
    """Look up default preset for a repo from repos.yaml."""
    repos = _load_repos_yaml()
    entry = repos.get(repo_id, {})
    return entry.get("preset") if entry else None


def _repo_excludes_from_yaml(repo_id: str) -> List[str]:
    """Look up repo-specific rg excludes from repos.yaml."""
    repos = _load_repos_yaml()
    entry = repos.get(repo_id, {})
    return entry.get("excludes", []) if entry else []


def _path_relative_to_repo(path_text: str, repo_path: Path) -> str:
    try:
        p = Path(path_text)
        if p.is_absolute():
            return str(p.resolve().relative_to(repo_path))
    except Exception:
        pass
    return path_text


# ---------------------------------------------------------------------------
# Docker client
# ---------------------------------------------------------------------------


def _get_docker_client():
    try:
        if DOCKER_HOST:
            return docker.DockerClient(base_url=DOCKER_HOST)
        return docker.from_env()
    except DockerException as e:
        raise HTTPException(status_code=500, detail=f"Docker client error: {e}")


# ---------------------------------------------------------------------------
# Subprocess / Docker exec
# ---------------------------------------------------------------------------


async def _run_subprocess(cmd: List[str], timeout: float) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return stdout.decode("utf-8", errors="ignore")


async def _docker_exec(
    container_name: str, cmd: List[str], timeout: int, request_id: str = ""
) -> Tuple[int, str, str]:
    _metrics["docker_exec_total"] += 1
    audit(
        {
            "type": "docker_exec",
            "container": container_name,
            "cmd": cmd,
            "timeout_sec": timeout,
        },
        request_id=request_id,
    )

    client = _get_docker_client()
    try:
        container = client.containers.get(container_name)
    except NotFound:
        raise HTTPException(
            status_code=409, detail=f"Container not running: {container_name}"
        )
    except DockerException as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e}")

    def _run():
        try:
            res = container.exec_run(cmd, demux=True)
            exit_code = res.exit_code if hasattr(res, "exit_code") else res[0]
            out_err = res.output if hasattr(res, "output") else res[1]
            stdout_b = out_err[0] if out_err and out_err[0] else b""
            stderr_b = out_err[1] if out_err and out_err[1] else b""
            return (
                int(exit_code),
                stdout_b.decode("utf-8", errors="ignore"),
                stderr_b.decode("utf-8", errors="ignore"),
            )
        except APIError as e:
            return 125, "", f"Docker APIError: {e}"
        except Exception as e:
            return 125, "", f"Exec error: {e}"

    try:
        rc, out, err = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
    except asyncio.TimeoutError:
        _metrics["docker_exec_errors"] += 1
        raise HTTPException(
            status_code=408, detail=f"Docker exec timed out ({timeout}s)"
        )

    if rc != 0:
        _metrics["docker_exec_errors"] += 1
    audit(
        {
            "type": "docker_exec_result",
            "container": container_name,
            "exit_code": rc,
            "stdout_len": len(out),
            "stderr_len": len(err),
        },
        request_id=request_id,
    )
    return rc, out, err


# ---------------------------------------------------------------------------
# Request ID helpers
# ---------------------------------------------------------------------------

_REQUEST_ID_RE = re.compile(r"^[\x20-\x7E]{1,128}$")


def _resolve_request_id(request: Request) -> str:
    """Accept client-provided X-Request-ID or generate a UUIDv4."""
    incoming = request.headers.get("x-request-id", "").strip()
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return str(uuid.uuid4())


def _get_request_id(request: Request) -> str:
    """Retrieve the request ID stored during middleware processing."""
    return getattr(request.state, "request_id", "")


# ---------------------------------------------------------------------------
# Middleware: request-id + auth + rate limit + audit
# ---------------------------------------------------------------------------


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    start = time.time()

    # Resolve request ID (accept or generate)
    request_id = _resolve_request_id(request)
    request.state.request_id = request_id

    x_api_key = request.headers.get("x-api-key", "")
    client_ip = request.client.host if request.client else "unknown"
    key_hash = (
        hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()[:16] if x_api_key else ""
    )

    _metrics["requests_total"] += 1

    # Auth + rate limit
    try:
        _require_api_key(x_api_key)
        await _rate_limit_check(x_api_key, client_ip)
    except HTTPException as e:
        if e.status_code == 429:
            _metrics["rate_limit_429"] += 1
        sc = str(e.status_code)
        _metrics["requests_by_status"][sc] = (
            _metrics["requests_by_status"].get(sc, 0) + 1
        )
        audit(
            {
                "type": "http",
                "ip": client_ip,
                "key": key_hash,
                "method": request.method,
                "path": request.url.path,
                "status": e.status_code,
                "duration_sec": round(time.time() - start, 4),
            },
            request_id=request_id,
        )
        resp = JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        resp.headers["X-Request-ID"] = request_id
        return resp

    # Forward
    response = await call_next(request)
    duration = round(time.time() - start, 4)
    sc = str(response.status_code)
    _metrics["requests_by_status"][sc] = _metrics["requests_by_status"].get(sc, 0) + 1
    audit(
        {
            "type": "http",
            "ip": client_ip,
            "key": key_hash,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_sec": duration,
        },
        request_id=request_id,
    )
    response.headers["X-Request-ID"] = request_id
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/status")
def status():
    return {
        "ok": True,
        "version": "0.2.0",
        "repo_root": str(REPO_ROOT),
        "cache_root": str(CACHE_ROOT),
        "rg_threads": RG_THREADS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "timeout_sec": REQUEST_TIMEOUT_SEC,
        "rg_concurrency": RG_CONCURRENCY,
        "job_concurrency": JOB_CONCURRENCY,
        "docker_host": DOCKER_HOST or "env",
        "allowed_containers": sorted(ALLOWED_CONTAINERS),
        "allowed_repos": ALLOWED_REPOS,
    }


@app.post("/v1/search/rg")
async def rg_search(req: RgSearchRequest):
    repo_id, _ = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    repo_path = _resolve_repo_path(repo_id)

    max_matches = max(1, min(req.max_matches, 2000))
    excludes = list(DEFAULT_EXCLUDES)
    # Add repo-specific excludes from repos.yaml
    excludes.extend(_repo_excludes_from_yaml(repo_id))
    if req.extra_excludes:
        excludes.extend(req.extra_excludes)

    cmd = ["rg", "--json"]
    cmd += ["--threads", str(max(1, min(RG_THREADS, 16)))]
    cmd += ["--max-count", str(max_matches)]
    if req.fixed_string:
        cmd.append("-F")
    if req.case_sensitive:
        cmd.append("-s")

    for ex in excludes:
        ex = ex.strip()
        if not ex:
            continue
        if ex.endswith("/"):
            cmd += ["--glob", f"!{ex}**"]
        else:
            cmd += ["--glob", f"!{ex}"]

    if req.path_globs:
        for g in req.path_globs:
            g = (g or "").strip()
            if g:
                cmd += ["--glob", g]

    cmd.append(req.query)
    cmd.append(str(repo_path))

    _metrics["search_total"] += 1
    async with rg_sem:
        try:
            out = await _run_subprocess(cmd, timeout=REQUEST_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="rg search timed out")

    matches: List[Dict[str, Any]] = []
    for line in out.splitlines():
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "match":
            continue
        data = evt.get("data", {})
        path = data.get("path", {}).get("text", "")
        line_no = data.get("line_number")
        submatches = data.get("submatches", [])
        snippet = data.get("lines", {}).get("text", "").rstrip("\n")
        if len(snippet) > 500:
            snippet = snippet[:500] + "\u2026"
        matches.append(
            {
                "path": _path_relative_to_repo(path, repo_path),
                "line": line_no,
                "snippet": snippet,
                "submatches": [
                    {"start": sm.get("start"), "end": sm.get("end")}
                    for sm in submatches
                ],
            }
        )
        if len(matches) >= max_matches:
            break

    payload = {
        "repo": repo_id,
        "query": req.query,
        "count": len(matches),
        "matches": matches,
    }
    raw = json.dumps(payload).encode("utf-8")
    raw, truncated = _truncate_bytes(raw)
    result = json.loads(raw.decode("utf-8", errors="ignore"))
    result["truncated"] = truncated
    return result


@app.post("/v1/file/slice")
async def file_slice(req: FileSliceRequest):
    repo_id, _ = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    repo_path = _resolve_repo_path(repo_id)
    file_path = _resolve_file_path(repo_path, req.path)

    if req.end < req.start:
        raise HTTPException(status_code=400, detail="end must be >= start")
    if (req.end - req.start) > 800:
        raise HTTPException(
            status_code=400, detail="Slice too large; max 800 lines per request"
        )

    _metrics["slice_total"] += 1
    lines: List[str] = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            if i < req.start:
                continue
            if i > req.end:
                break
            lines.append(line.rstrip("\n"))

    payload = {
        "repo": repo_id,
        "path": req.path,
        "start": req.start,
        "end": req.end,
        "lines": lines,
    }
    raw = json.dumps(payload).encode("utf-8")
    raw, truncated = _truncate_bytes(raw)
    result = json.loads(raw.decode("utf-8", errors="ignore"))
    result["truncated"] = truncated
    return result


@app.post("/v1/index/ctags")
async def ctags_index(req: CtagsIndexRequest, request: Request):
    repo_id, repo_norm = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    _resolve_repo_path(repo_id)  # validate path exists
    _cache_dir_for(repo_norm)  # ensure exists

    _metrics["ctags_index_total"] += 1
    container_name = "claude-ctags"
    _ensure_container_allowed(container_name)

    # ctags container mounts repos at /repos and gw-cache at /gwcache
    repo_in_container = f"/repos/{repo_id}"
    exec_cmd = [
        "sh",
        "-c",
        (
            f"set -e; "
            f"mkdir -p /gwcache/{repo_norm}; "
            f"ctags -R "
            f"--fields=+iaS --extras=+q --output-format=e-ctags "
            f"-f /gwcache/{repo_norm}/tags "
            f"{_sh_quote(repo_in_container)}"
        ),
    ]

    rid = _get_request_id(request)
    async with job_sem:
        started = time.time()
        rc, out, err = await _docker_exec(
            container_name, exec_cmd, timeout=600, request_id=rid
        )
        dur = round(time.time() - started, 3)

    stdout_b, _ = _truncate_bytes(out.encode("utf-8", errors="ignore"))
    stderr_b, _ = _truncate_bytes(err.encode("utf-8", errors="ignore"))

    return {
        "repo": repo_id,
        "ok": rc == 0,
        "exit_code": rc,
        "duration_sec": dur,
        "tags_path": f"{repo_norm}/tags",
        "stdout": stdout_b.decode("utf-8", errors="ignore"),
        "stderr": stderr_b.decode("utf-8", errors="ignore"),
    }


@app.post("/v1/symbol/ctags")
async def ctags_query(req: CtagsQueryRequest):
    repo_id, repo_norm = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    _resolve_repo_path(repo_id)  # verify repo exists
    cdir = _cache_dir_for(repo_norm)
    tags_path = cdir / "tags"

    if not tags_path.exists():
        raise HTTPException(
            status_code=409,
            detail="Tags not built yet; call /v1/index/ctags first",
        )

    _metrics["ctags_query_total"] += 1
    sym = req.symbol.strip()
    if not sym or len(sym) > 200:
        raise HTTPException(status_code=400, detail="Invalid symbol")

    results = []
    max_results = 50
    with open(tags_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("!_TAG_"):
                continue
            if not line.startswith(sym + "\t"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            results.append(
                {
                    "name": parts[0],
                    "file": parts[1],
                    "excmd": parts[2],
                    "kind": parts[3] if len(parts) > 3 else None,
                }
            )
            if len(results) >= max_results:
                break

    payload = {
        "repo": repo_id,
        "symbol": sym,
        "count": len(results),
        "defs": results,
    }
    raw = json.dumps(payload).encode("utf-8")
    raw, truncated = _truncate_bytes(raw)
    result = json.loads(raw.decode("utf-8", errors="ignore"))
    result["truncated"] = truncated
    return result


@app.post("/v1/run/job")
async def run_job(req: RunJobRequest, request: Request):
    repo_id, _ = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    _resolve_repo_path(repo_id)

    job = req.job.strip().lower()
    preset = req.preset.strip().lower()

    # Allowlisted presets — no arbitrary commands.
    # If no preset given, try repos.yaml default.
    if not preset:
        yaml_preset = _repo_preset_from_yaml(repo_id)
        if yaml_preset:
            preset = yaml_preset.strip().lower()

    cwd = f"/repos/{repo_id}"
    presets: Dict[str, Dict[str, Any]] = {
        "node": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && npm test"],
                "build": ["sh", "-c", "cd $CWD && npm run build"],
                "lint": ["sh", "-c", "cd $CWD && npm run lint"],
            },
            "timeout": 900,
        },
        "pnpm": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && pnpm test"],
                "build": ["sh", "-c", "cd $CWD && pnpm run build"],
                "lint": ["sh", "-c", "cd $CWD && pnpm run lint"],
            },
            "timeout": 900,
        },
        "yarn": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && yarn test"],
                "build": ["sh", "-c", "cd $CWD && yarn build"],
                "lint": ["sh", "-c", "cd $CWD && yarn lint"],
            },
            "timeout": 900,
        },
        "python": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && pytest -q"],
                "build": ["sh", "-c", "cd $CWD && python -m build"],
                "lint": ["sh", "-c", "cd $CWD && ruff check ."],
            },
            "timeout": 900,
        },
        "rust": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && cargo test -q"],
                "build": ["sh", "-c", "cd $CWD && cargo build -q"],
                "lint": ["sh", "-c", "cd $CWD && cargo clippy -q"],
            },
            "timeout": 1200,
        },
        "go": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && go test ./..."],
                "build": ["sh", "-c", "cd $CWD && go build ./..."],
                "lint": ["sh", "-c", "cd $CWD && golangci-lint run"],
            },
            "timeout": 1200,
        },
        "java": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": [
                    "sh",
                    "-c",
                    "cd $CWD && ./gradlew test --no-daemon -q 2>/dev/null || mvn test -q",
                ],
                "build": [
                    "sh",
                    "-c",
                    "cd $CWD && ./gradlew build --no-daemon -q 2>/dev/null || mvn package -q",
                ],
                "lint": [
                    "sh",
                    "-c",
                    "cd $CWD && ./gradlew spotlessCheck --no-daemon -q 2>/dev/null || mvn checkstyle:check -q",
                ],
            },
            "timeout": 1200,
        },
        "dotnet": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && dotnet test -q"],
                "build": ["sh", "-c", "cd $CWD && dotnet build -q"],
                "lint": ["sh", "-c", "cd $CWD && dotnet format --verify-no-changes"],
            },
            "timeout": 1200,
        },
        "bazel": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": ["sh", "-c", "cd $CWD && bazel test //..."],
                "build": ["sh", "-c", "cd $CWD && bazel build //..."],
                "lint": ["sh", "-c", "cd $CWD && buildifier -lint warn -r ."],
            },
            "timeout": 1800,
        },
        "cmake": {
            "container": "claude-build",
            "cwd": cwd,
            "commands": {
                "test": [
                    "sh",
                    "-c",
                    "cd $CWD && cmake --build build && ctest --test-dir build -q",
                ],
                "build": [
                    "sh",
                    "-c",
                    "cd $CWD && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build",
                ],
                "lint": ["sh", "-c", "cd $CWD && cmake-lint CMakeLists.txt"],
            },
            "timeout": 1200,
        },
    }

    if not preset:
        raise HTTPException(
            status_code=400,
            detail=f"No preset specified and no default in repos.yaml for {repo_id}. "
            f"Available: {', '.join(sorted(presets))}",
        )
    if preset not in presets:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset: {preset}. Available: {', '.join(sorted(presets))}",
        )
    if job not in ("test", "build", "lint"):
        raise HTTPException(
            status_code=400, detail=f"Unknown job: {job}. Available: test, build, lint"
        )

    spec = presets[preset]
    container_name = spec["container"]
    _ensure_container_allowed(container_name)

    cmd = [c.replace("$CWD", _sh_quote(spec["cwd"])) for c in spec["commands"][job]]
    timeout = int(spec.get("timeout", 900))

    rid = _get_request_id(request)
    async with job_sem:
        started = time.time()
        rc, out, err = await _docker_exec(
            container_name, cmd, timeout=timeout, request_id=rid
        )
        dur = round(time.time() - started, 3)

    stdout_b, out_trunc = _truncate_bytes(out.encode("utf-8", errors="ignore"))
    stderr_b, err_trunc = _truncate_bytes(err.encode("utf-8", errors="ignore"))

    result = {
        "repo": repo_id,
        "job": job,
        "preset": preset,
        "ok": rc == 0,
        "exit_code": rc,
        "duration_sec": dur,
        "stdout": stdout_b.decode("utf-8", errors="ignore"),
        "stderr": stderr_b.decode("utf-8", errors="ignore"),
        "truncated": out_trunc or err_trunc,
    }
    if result["truncated"]:
        _metrics["truncations"] += 1
    _metrics["job_total"] += 1
    return result


# ---------------------------------------------------------------------------
# Metrics endpoint (Prometheus text format)
# ---------------------------------------------------------------------------


@app.get("/v1/metrics", response_class=PlainTextResponse)
def metrics():
    lines = []
    lines.append("# HELP gateway_requests_total Total HTTP requests")
    lines.append("# TYPE gateway_requests_total counter")
    lines.append(f"gateway_requests_total {_metrics['requests_total']}")

    lines.append("# HELP gateway_rate_limit_429_total Rate limit rejections")
    lines.append("# TYPE gateway_rate_limit_429_total counter")
    lines.append(f"gateway_rate_limit_429_total {_metrics['rate_limit_429']}")

    lines.append("# HELP gateway_docker_exec_total Docker exec calls")
    lines.append("# TYPE gateway_docker_exec_total counter")
    lines.append(f"gateway_docker_exec_total {_metrics['docker_exec_total']}")

    lines.append("# HELP gateway_docker_exec_errors_total Docker exec errors")
    lines.append("# TYPE gateway_docker_exec_errors_total counter")
    lines.append(f"gateway_docker_exec_errors_total {_metrics['docker_exec_errors']}")

    lines.append("# HELP gateway_truncations_total Responses truncated at 512KB")
    lines.append("# TYPE gateway_truncations_total counter")
    lines.append(f"gateway_truncations_total {_metrics['truncations']}")

    lines.append("# HELP gateway_search_total Search requests")
    lines.append("# TYPE gateway_search_total counter")
    lines.append(f"gateway_search_total {_metrics['search_total']}")

    lines.append("# HELP gateway_slice_total File slice requests")
    lines.append("# TYPE gateway_slice_total counter")
    lines.append(f"gateway_slice_total {_metrics['slice_total']}")

    lines.append("# HELP gateway_ctags_index_total Ctags index requests")
    lines.append("# TYPE gateway_ctags_index_total counter")
    lines.append(f"gateway_ctags_index_total {_metrics['ctags_index_total']}")

    lines.append("# HELP gateway_ctags_query_total Ctags query requests")
    lines.append("# TYPE gateway_ctags_query_total counter")
    lines.append(f"gateway_ctags_query_total {_metrics['ctags_query_total']}")

    lines.append("# HELP gateway_job_total Job run requests")
    lines.append("# TYPE gateway_job_total counter")
    lines.append(f"gateway_job_total {_metrics['job_total']}")

    for status_code, count in sorted(_metrics["requests_by_status"].items()):
        lines.append(f'gateway_requests_by_status{{status="{status_code}"}} {count}')

    return "\n".join(lines) + "\n"
