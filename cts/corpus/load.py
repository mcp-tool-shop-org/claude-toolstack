"""Load and validate sidecar artifacts for corpus ingestion.

Wraps ``cts.sidecar.validate_envelope`` to provide a load-or-fail
interface suitable for batch processing: returns ``(data, errors)``
so callers decide whether to skip or abort on invalid files.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from cts.sidecar import validate_envelope


def load_artifact(
    path: str,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Load a sidecar JSON file and validate its envelope.

    Args:
        path: Filesystem path to the sidecar JSON file.

    Returns:
        ``(sidecar_dict, errors)``.  When *errors* is non-empty the
        dict may be ``None`` (parse failure) or partially populated
        (validation failure).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, [f"parse error: {exc}"]
    except OSError as exc:
        return None, [f"read error: {exc}"]

    if not isinstance(data, dict):
        return None, [f"expected JSON object, got {type(data).__name__}"]

    errors = validate_envelope(data)
    if errors:
        return data, errors

    return data, []
