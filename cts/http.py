"""HTTP client wrapper — retries, timeouts, request-ID propagation."""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cts.config import api_key, gateway_url

# Default timeout for all requests (seconds)
DEFAULT_TIMEOUT = 30


def _make_request_id(override: Optional[str] = None) -> str:
    return override or str(uuid.uuid4())


def call(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    request_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Call a gateway endpoint. Returns parsed JSON response.

    Raises SystemExit on connection or HTTP errors with clear messages.
    """
    url = f"{gateway_url()}{path}"
    rid = _make_request_id(request_id)

    headers = {
        "x-api-key": api_key(),
        "X-Request-ID": rid,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = json.dumps(body).encode("utf-8") if body else None
    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            result = json.loads(raw) if raw.strip() else {}
            # Attach response metadata
            result["_request_id"] = resp.headers.get("X-Request-ID", rid)
            result["_status"] = resp.status
            return result
    except HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = ""
        try:
            detail = json.loads(body_text).get("detail", body_text)
        except Exception:
            detail = body_text

        resp_rid = e.headers.get("X-Request-ID", rid) if e.headers else rid
        print(
            f"Error {e.code}: {detail}\n  Request-ID: {resp_rid}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except URLError as e:
        print(
            f"Connection error: {e.reason}\n  URL: {url}\n  Is the gateway running?",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        raise SystemExit(1)


def get(path: str, **kwargs) -> Dict[str, Any]:
    return call("GET", path, **kwargs)


def post(path: str, body: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    return call("POST", path, body=body, **kwargs)
