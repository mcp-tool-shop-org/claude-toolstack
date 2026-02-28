"""
Claude Toolstack Gateway — thin HTTP API for bounded code intelligence.

Endpoints:
  POST /v1/search/rg     — ripgrep with guardrails
  POST /v1/file/slice    — fetch file range
  POST /v1/index/ctags   — trigger ctags build (async job)
  POST /v1/symbol/ctags  — query symbol defs from tags
  POST /v1/run/job       — run allowlisted test/build/lint
  GET  /v1/status        — health + config

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
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import docker
from docker.errors import APIError, DockerException, NotFound
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
    c.strip()
    for c in os.getenv("ALLOWED_CONTAINERS", "").split(",")
    if c.strip()
}

RG_CONCURRENCY = int(os.getenv("RG_CONCURRENCY", "2"))
JOB_CONCURRENCY = int(os.getenv("JOB_CONCURRENCY", "1"))

rg_sem = asyncio.Semaphore(RG_CONCURRENCY)
job_sem = asyncio.Semaphore(JOB_CONCURRENCY)

# Per-repo access control
ALLOWED_REPOS = [
    s.strip()
    for s in os.getenv("ALLOWED_REPOS", "").split(",")
    if s.strip()
]
DENIED_REPOS = [
    s.strip()
    for s in os.getenv("DENIED_REPOS", "").split(",")
    if s.strip()
]

# Rate limiting
RATE_LIMIT_RPS = float(os.getenv("RATE_LIMIT_RPS", "2"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "10"))
RATE_LIMIT_SCOPE = os.getenv("RATE_LIMIT_SCOPE", "key").lower()

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


def audit(event: Dict[str, Any]) -> None:
    event.setdefault("ts", time.time())
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


def _rl_key(api_key: str, client_ip: str) -> str:
    if RATE_LIMIT_SCOPE == "ip":
        return f"ip:{client_ip}"
    if RATE_LIMIT_SCOPE == "key+ip":
        return f"keyip:{api_key}:{client_ip}"
    return f"key:{api_key}"


async def _rate_limit_check(api_key: str, client_ip: str) -> None:
    if RATE_LIMIT_RPS <= 0 or RATE_LIMIT_BURST <= 0:
        return

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


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Claude Tooling Gateway", version="0.1.0")


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
    preset: str = Field(..., description="Preset name: node, python, rust, go")
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


def _truncate_bytes(
    data: bytes, limit: int = MAX_RESPONSE_BYTES
) -> Tuple[bytes, bool]:
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
        raise HTTPException(
            status_code=500, detail="ALLOWED_CONTAINERS not configured"
        )
    if name not in ALLOWED_CONTAINERS:
        raise HTTPException(
            status_code=403, detail=f"Container not allowed: {name}"
        )


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


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
        raise HTTPException(
            status_code=500, detail=f"Docker client error: {e}"
        )


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
    container_name: str, cmd: List[str], timeout: int
) -> Tuple[int, str, str]:
    audit({
        "type": "docker_exec",
        "container": container_name,
        "cmd": cmd,
        "timeout_sec": timeout,
    })

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
        rc, out, err = await asyncio.wait_for(
            asyncio.to_thread(_run), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408, detail=f"Docker exec timed out ({timeout}s)"
        )

    audit({
        "type": "docker_exec_result",
        "container": container_name,
        "exit_code": rc,
        "stdout_len": len(out),
        "stderr_len": len(err),
    })
    return rc, out, err


# ---------------------------------------------------------------------------
# Middleware: auth + rate limit + audit
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    start = time.time()
    x_api_key = request.headers.get("x-api-key", "")
    client_ip = request.client.host if request.client else "unknown"
    key_hash = (
        hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()[:16]
        if x_api_key
        else ""
    )

    # Auth + rate limit
    try:
        _require_api_key(x_api_key)
        await _rate_limit_check(x_api_key, client_ip)
    except HTTPException as e:
        audit({
            "type": "http",
            "ip": client_ip,
            "key": key_hash,
            "method": request.method,
            "path": request.url.path,
            "status": e.status_code,
            "duration_sec": round(time.time() - start, 4),
        })
        return JSONResponse(
            status_code=e.status_code, content={"detail": e.detail}
        )

    # Forward
    response = await call_next(request)
    duration = round(time.time() - start, 4)
    audit({
        "type": "http",
        "ip": client_ip,
        "key": key_hash,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_sec": duration,
    })
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/status")
def status():
    return {
        "ok": True,
        "version": "0.1.0",
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
        matches.append({
            "path": _path_relative_to_repo(path, repo_path),
            "line": line_no,
            "snippet": snippet,
            "submatches": [
                {"start": sm.get("start"), "end": sm.get("end")}
                for sm in submatches
            ],
        })
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
async def ctags_index(req: CtagsIndexRequest):
    repo_id, repo_norm = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    repo_path = _resolve_repo_path(repo_id)
    _cache_dir_for(repo_norm)  # ensure exists

    container_name = "claude-ctags"
    _ensure_container_allowed(container_name)

    # ctags container mounts repos at /repos and gw-cache at /gwcache
    repo_in_container = f"/repos/{repo_id}"
    exec_cmd = [
        "sh", "-c",
        (
            f"set -e; "
            f"mkdir -p /gwcache/{repo_norm}; "
            f"ctags -R "
            f"--fields=+iaS --extras=+q --output-format=e-ctags "
            f"-f /gwcache/{repo_norm}/tags "
            f"{_sh_quote(repo_in_container)}"
        ),
    ]

    async with job_sem:
        started = time.time()
        rc, out, err = await _docker_exec(container_name, exec_cmd, timeout=600)
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
            results.append({
                "name": parts[0],
                "file": parts[1],
                "excmd": parts[2],
                "kind": parts[3] if len(parts) > 3 else None,
            })
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
async def run_job(req: RunJobRequest):
    repo_id, _ = _normalize_repo(req.repo)
    _enforce_repo_allowlist(repo_id)
    _resolve_repo_path(repo_id)

    job = req.job.strip().lower()
    preset = req.preset.strip().lower()

    # Allowlisted presets — no arbitrary commands
    presets: Dict[str, Dict[str, Any]] = {
        "node": {
            "container": "claude-build",
            "cwd": f"/repos/{repo_id}",
            "commands": {
                "test": ["sh", "-c", "cd $CWD && npm test"],
                "build": ["sh", "-c", "cd $CWD && npm run build"],
                "lint": ["sh", "-c", "cd $CWD && npm run lint"],
            },
            "timeout": 900,
        },
        "python": {
            "container": "claude-build",
            "cwd": f"/repos/{repo_id}",
            "commands": {
                "test": ["sh", "-c", "cd $CWD && pytest -q"],
                "build": ["sh", "-c", "cd $CWD && python -m build"],
                "lint": ["sh", "-c", "cd $CWD && ruff check ."],
            },
            "timeout": 900,
        },
        "rust": {
            "container": "claude-build",
            "cwd": f"/repos/{repo_id}",
            "commands": {
                "test": ["sh", "-c", "cd $CWD && cargo test -q"],
                "build": ["sh", "-c", "cd $CWD && cargo build -q"],
                "lint": ["sh", "-c", "cd $CWD && cargo clippy -q"],
            },
            "timeout": 1200,
        },
        "go": {
            "container": "claude-build",
            "cwd": f"/repos/{repo_id}",
            "commands": {
                "test": ["sh", "-c", "cd $CWD && go test ./..."],
                "build": ["sh", "-c", "cd $CWD && go build ./..."],
                "lint": ["sh", "-c", "cd $CWD && golangci-lint run"],
            },
            "timeout": 1200,
        },
    }

    if preset not in presets:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset: {preset}. Available: {', '.join(presets)}",
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

    async with job_sem:
        started = time.time()
        rc, out, err = await _docker_exec(container_name, cmd, timeout=timeout)
        dur = round(time.time() - started, 3)

    stdout_b, out_trunc = _truncate_bytes(out.encode("utf-8", errors="ignore"))
    stderr_b, err_trunc = _truncate_bytes(err.encode("utf-8", errors="ignore"))

    return {
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
