"""Sidecar JSON schema wrapper for evidence bundles.

Wraps raw bundles with stable metadata for downstream consumers:
  - bundle_schema_version: integer, bumped on breaking changes
  - tool info: CLI version, gateway version
  - inputs: the original query parameters
  - passes: list of refinement passes (for autopilot)
  - final: the final bundle

Consumers check bundle_schema_version to decide if they can parse
the payload. Non-breaking additions (new keys) don't bump the version.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

BUNDLE_SCHEMA_VERSION = 1


def wrap_bundle(
    raw_bundle: Dict[str, Any],
    *,
    mode: str,
    request_id: str = "",
    cli_version: str = "",
    gateway_version: Optional[str] = None,
    repo: str = "",
    query: Optional[str] = None,
    created_at: Optional[float] = None,
    inputs: Optional[Dict[str, Any]] = None,
    debug: bool = False,
    passes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Wrap a raw evidence bundle in the sidecar schema envelope.

    Args:
        raw_bundle: The evidence bundle dict from bundle.py builders.
        mode: Bundle mode (default, error, symbol, change).
        request_id: Request identifier.
        cli_version: cts CLI version string.
        gateway_version: Gateway version string (None if not available).
        repo: Repository identifier.
        query: Original search query or symbol name.
        created_at: Unix timestamp (defaults to now).
        inputs: Dict of original CLI inputs / query parameters.
        debug: Whether debug telemetry was enabled.
        passes: List of refinement pass dicts (for autopilot).

    Returns:
        Sidecar-wrapped dict with stable schema version.
    """
    ts = created_at if created_at is not None else time.time()

    sidecar: Dict[str, Any] = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "created_at": ts,
        "tool": {
            "name": "cts",
            "cli_version": cli_version,
        },
        "request_id": request_id,
        "repo": repo,
        "mode": mode,
    }

    if gateway_version is not None:
        sidecar["tool"]["gateway_version"] = gateway_version

    if query is not None:
        sidecar["query"] = query

    if inputs is not None:
        sidecar["inputs"] = inputs

    sidecar["debug"] = debug

    # Refinement passes (autopilot stores intermediate bundles here)
    sidecar["passes"] = passes or []

    # The final bundle payload
    sidecar["final"] = _strip_debug_if_needed(raw_bundle, debug)

    return sidecar


def _strip_debug_if_needed(bundle: Dict[str, Any], keep_debug: bool) -> Dict[str, Any]:
    """Optionally strip _debug from the final bundle.

    When debug=False, remove _debug to keep sidecar clean.
    When debug=True, keep it in the final bundle for introspection.
    """
    if keep_debug or "_debug" not in bundle:
        return bundle
    return {k: v for k, v in bundle.items() if k != "_debug"}
