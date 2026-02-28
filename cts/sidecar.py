"""Sidecar artifact utilities: load, validate, summarize, secrets scan.

Operates on sidecar JSON files produced by ``cts --format sidecar``
or ``--emit``. Zero external dependencies.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from cts.schema import BUNDLE_SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Required envelope keys and types
# ---------------------------------------------------------------------------

_REQUIRED_KEYS: Dict[str, type] = {
    "bundle_schema_version": int,
    "created_at": (int, float),  # type: ignore[assignment]
    "tool": dict,
    "request_id": str,
    "repo": str,
    "mode": str,
    "debug": bool,
    "passes": list,
    "final": dict,
}

_VALID_MODES = {"default", "error", "symbol", "change"}

# Keys that are always allowed at the top level (stability contract)
_ALLOWED_TOP_KEYS = {
    "bundle_schema_version",
    "created_at",
    "tool",
    "request_id",
    "repo",
    "mode",
    "query",
    "inputs",
    "debug",
    "passes",
    "final",
}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load(path: str) -> Dict[str, Any]:
    """Load a sidecar JSON file from disk.

    Raises ValueError on parse errors.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


def load_text(text: str) -> Dict[str, Any]:
    """Load a sidecar from a JSON string."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def validate_envelope(sidecar: Dict[str, Any]) -> List[str]:
    """Validate the sidecar envelope structure.

    Returns a list of error strings (empty = valid).
    """
    errors: List[str] = []

    # Required keys and types
    for key, expected_type in _REQUIRED_KEYS.items():
        if key not in sidecar:
            errors.append(f"missing required key: {key}")
            continue
        val = sidecar[key]
        if not isinstance(val, expected_type):
            errors.append(
                f"{key}: expected {expected_type.__name__}, got {type(val).__name__}"
            )

    # Schema version
    ver = sidecar.get("bundle_schema_version")
    if isinstance(ver, int) and ver != BUNDLE_SCHEMA_VERSION:
        errors.append(
            f"unsupported bundle_schema_version: {ver} "
            f"(expected {BUNDLE_SCHEMA_VERSION})"
        )

    # Mode
    mode = sidecar.get("mode")
    if isinstance(mode, str) and mode not in _VALID_MODES:
        errors.append(f"invalid mode: {mode!r}")

    # Tool structure
    tool = sidecar.get("tool")
    if isinstance(tool, dict):
        if "name" not in tool:
            errors.append("tool.name is required")
        if "cli_version" not in tool:
            errors.append("tool.cli_version is required")

    # Passes shape
    passes = sidecar.get("passes")
    if isinstance(passes, list):
        for i, p in enumerate(passes):
            if not isinstance(p, dict):
                errors.append(f"passes[{i}]: expected dict, got {type(p).__name__}")

    # Final bundle shape
    final = sidecar.get("final")
    if isinstance(final, dict):
        if "version" not in final:
            errors.append("final.version is required")
        if "mode" not in final:
            errors.append("final.mode is required")

    return errors


def validate_stability_contract(
    sidecar: Dict[str, Any],
    *,
    extra_ok: Optional[set] = None,
) -> List[str]:
    """Validate the stability contract.

    Checks that no unexpected top-level keys exist.
    Returns list of warnings (not hard errors).
    """
    warnings: List[str] = []
    allowed = _ALLOWED_TOP_KEYS | (extra_ok or set())
    unexpected = set(sidecar.keys()) - allowed
    for key in sorted(unexpected):
        warnings.append(f"unexpected top-level key: {key!r}")
    return warnings


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------


def summarize(
    sidecar: Dict[str, Any],
    *,
    format: str = "text",
) -> str:
    """Produce a human-readable summary of a sidecar artifact.

    Args:
        sidecar: Loaded sidecar dict.
        format: "text" or "markdown".

    Returns summary string.
    """
    if format == "markdown":
        return _summarize_markdown(sidecar)
    return _summarize_text(sidecar)


def _summarize_text(s: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("Sidecar Summary")
    lines.append(f"  repo:       {s.get('repo', '?')}")
    lines.append(f"  mode:       {s.get('mode', '?')}")
    lines.append(f"  request_id: {s.get('request_id', '?')}")

    ts = s.get("created_at", 0)
    ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    lines.append(f"  created_at: {ts_str}")

    tool = s.get("tool", {})
    lines.append(f"  cli:        {tool.get('cli_version', '?')}")
    gw = tool.get("gateway_version")
    if gw:
        lines.append(f"  gateway:    {gw}")

    query = s.get("query")
    if query:
        lines.append(f"  query:      {query}")

    # Passes
    passes = s.get("passes", [])
    if passes:
        lines.append(f"  passes:     {len(passes)}")
        first_conf = passes[0].get("confidence_before", "?")
        n = len(passes)
        lines.append(f"  confidence: {first_conf} -> (after {n} passes)")

    # Final bundle summary
    final = s.get("final", {})
    sources = final.get("ranked_sources", [])
    lines.append(f"  sources:    {len(sources)}")

    if sources:
        lines.append("  top files:")
        for src in sources[:5]:
            p = src.get("path", "?")
            sc = src.get("score", 0.0)
            lines.append(f"    {sc:+.2f}  {p}")

    slices = final.get("slices", [])
    matches = final.get("matches", [])
    lines.append(f"  matches:    {len(matches)}")
    lines.append(f"  slices:     {len(slices)}")

    if final.get("truncated"):
        lines.append("  [truncated]")

    cmds = final.get("suggested_commands", [])
    if cmds:
        lines.append("  suggested:")
        for c in cmds[:3]:
            lines.append(f"    {c}")

    return "\n".join(lines)


def _summarize_markdown(s: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("### Evidence Bundle Summary")
    lines.append("")

    ts = s.get("created_at", 0)
    ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    repo = s.get("repo", "?")
    mode = s.get("mode", "?")
    rid = s.get("request_id", "?")

    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| Repo | `{repo}` |")
    lines.append(f"| Mode | `{mode}` |")
    lines.append(f"| Request ID | `{rid}` |")
    lines.append(f"| Created | {ts_str} |")

    tool = s.get("tool", {})
    lines.append(f"| CLI | `{tool.get('cli_version', '?')}` |")
    gw = tool.get("gateway_version")
    if gw:
        lines.append(f"| Gateway | `{gw}` |")

    query = s.get("query")
    if query:
        lines.append(f"| Query | `{query}` |")

    lines.append("")

    # Passes
    passes = s.get("passes", [])
    if passes:
        lines.append(f"**Autopilot:** {len(passes)} refinement pass(es)")
        for p in passes:
            pn = p.get("pass", "?")
            actions = ", ".join(p.get("actions", []))
            conf = p.get("confidence_before", "?")
            status = p.get("status", "?")
            lines.append(f"- Pass {pn}: `{actions}` (conf={conf}, {status})")
        lines.append("")

    # Top files
    final = s.get("final", {})
    sources = final.get("ranked_sources", [])
    matches = final.get("matches", [])
    slices = final.get("slices", [])

    lines.append(
        f"**Results:** {len(sources)} sources, "
        f"{len(matches)} matches, {len(slices)} slices"
    )

    if sources:
        lines.append("")
        lines.append("**Top ranked files:**")
        for src in sources[:5]:
            p = src.get("path", "?")
            sc = src.get("score", 0.0)
            lines.append(f"- `{p}` ({sc:+.2f})")

    if final.get("truncated"):
        lines.append("")
        lines.append("> Search results were truncated at 512 KB")

    cmds = final.get("suggested_commands", [])
    if cmds:
        lines.append("")
        lines.append("**Suggested next:**")
        for c in cmds[:3]:
            lines.append(f"- `{c}`")

    lines.append("")
    lines.append("*Download the full artifact from CI for detailed inspection.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Secrets scan
# ---------------------------------------------------------------------------

# Conservative secret patterns (no external deps)
_SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [  # type: ignore[type-arg]
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("GitHub Fine-Grained Token", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S{20,}")),
    ("Slack Token", re.compile(r"xox[bpras]-[0-9a-zA-Z\-]{10,}")),
    ("Slack Webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/")),
    (
        "Private Key Block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "Generic API Key",
        re.compile(r"(?i)(?:api[_-]?key|apikey)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    ),
    (
        "Generic Secret",
        re.compile(r"(?i)(?:secret|password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
    ),
    ("JWT Token", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
    ("GCP Service Account", re.compile(r'"type"\s*:\s*"service_account"')),
    ("Anthropic API Key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("npm Token", re.compile(r"npm_[A-Za-z0-9]{36,}")),
]


def secrets_scan(
    sidecar: Dict[str, Any],
    *,
    ignore_patterns: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Scan a sidecar artifact for potential secrets.

    Args:
        sidecar: Loaded sidecar dict.
        ignore_patterns: Optional regex patterns to skip (repo-specific).

    Returns list of finding dicts:
        type: str — what was matched
        location: str — where in the sidecar
        redacted: str — redacted preview (first/last 4 chars visible)
    """
    ignore_res = []
    if ignore_patterns:
        for pat in ignore_patterns:
            try:
                ignore_res.append(re.compile(pat))
            except re.error:
                continue

    findings: List[Dict[str, Any]] = []

    # Scan these fields in the final bundle
    final = sidecar.get("final", {})
    _scan_dict(final, "final", findings, ignore_res)

    # Also scan passes for any stashed content
    for i, p in enumerate(sidecar.get("passes", [])):
        _scan_dict(p, f"passes[{i}]", findings, ignore_res)

    # Scan _debug if present
    debug = final.get("_debug")
    if isinstance(debug, dict):
        _scan_dict(debug, "final._debug", findings, ignore_res)

    return findings


def _scan_dict(
    obj: Any,
    location: str,
    findings: List[Dict[str, Any]],
    ignore_res: List[re.Pattern],  # type: ignore[type-arg]
) -> None:
    """Recursively scan dict/list/str values for secret patterns."""
    if isinstance(obj, str):
        _scan_string(obj, location, findings, ignore_res)
    elif isinstance(obj, dict):
        for key, val in obj.items():
            _scan_dict(val, f"{location}.{key}", findings, ignore_res)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_dict(item, f"{location}[{i}]", findings, ignore_res)


def _scan_string(
    text: str,
    location: str,
    findings: List[Dict[str, Any]],
    ignore_res: List[re.Pattern],  # type: ignore[type-arg]
) -> None:
    """Check a string value against all secret patterns."""
    for name, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            matched = m.group(0)
            # Check ignore list
            if any(ign.search(matched) for ign in ignore_res):
                continue
            findings.append(
                {
                    "type": name,
                    "location": location,
                    "redacted": _redact(matched),
                }
            )


def _redact(value: str) -> str:
    """Redact a secret value, showing only first and last 4 chars."""
    if len(value) <= 12:
        return value[:2] + "***" + value[-2:]
    return value[:4] + "***" + value[-4:]
