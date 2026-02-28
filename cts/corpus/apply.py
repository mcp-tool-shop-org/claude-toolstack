"""Apply engine: write patch plan to repos.yaml with guardrails.

Consumes a patch plan (from ``generate_patch_plan``) and writes
changes to repos.yaml.  Safety measures:

1. **Backup**: writes ``repos.yaml.bak.<timestamp>`` before any edit
2. **Risk gate**: refuses to apply ``high``-risk patches unless
   ``--allow-high-risk`` is passed
3. **Atomic write**: writes to tmp file, then renames over target
4. **Rollback artifact**: emits ``rollback.json`` with everything
   needed to restore the previous state

This module never deletes the backup file — the operator decides
when to clean up.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, List

from cts.corpus.patch import (
    PatchItem,
    apply_plan_to_yaml,
)

# YAML is optional
try:
    import yaml  # type: ignore[import-untyped]

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Rollback artifact
# ---------------------------------------------------------------------------


class RollbackRecord:
    """Everything needed to undo an apply."""

    __slots__ = (
        "applied_at",
        "repos_yaml_path",
        "backup_path",
        "items_applied",
        "original_snapshot",
    )

    def __init__(
        self,
        *,
        applied_at: float,
        repos_yaml_path: str,
        backup_path: str,
        items_applied: List[Dict[str, Any]],
        original_snapshot: Dict[str, Any],
    ) -> None:
        self.applied_at = applied_at
        self.repos_yaml_path = repos_yaml_path
        self.backup_path = backup_path
        self.items_applied = items_applied
        self.original_snapshot = original_snapshot

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applied_at": self.applied_at,
            "repos_yaml_path": self.repos_yaml_path,
            "backup_path": self.backup_path,
            "items_applied": self.items_applied,
            "original_snapshot": self.original_snapshot,
        }


# ---------------------------------------------------------------------------
# Risk gate
# ---------------------------------------------------------------------------

BLOCKED_RISK = "high"


def check_risk_gate(
    items: List[PatchItem],
    *,
    allow_high_risk: bool = False,
) -> List[str]:
    """Check for high-risk patches and return blocking reasons.

    Returns empty list if all patches are safe to apply.
    """
    if allow_high_risk:
        return []

    blocking: List[str] = []
    for item in items:
        if item.skipped:
            continue
        if item.risk == BLOCKED_RISK:
            blocking.append(
                f"High-risk patch blocked: {item.recommendation_id} "
                f"({item.yaml_path}). Use --allow-high-risk to override."
            )
    return blocking


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def create_backup(
    repos_yaml_path: str,
    *,
    suffix: str = "",
) -> str:
    """Create a timestamped backup of repos.yaml.

    Returns the backup file path.
    """
    ts = suffix or str(int(time.time()))
    backup_path = f"{repos_yaml_path}.bak.{ts}"
    shutil.copy2(repos_yaml_path, backup_path)
    return backup_path


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_patch_plan(
    repos_yaml: Dict[str, Any],
    items: List[PatchItem],
    *,
    repos_yaml_path: str,
    allow_high_risk: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply a patch plan to repos.yaml.

    Args:
        repos_yaml: Parsed repos.yaml content.
        items: Patch items from generate_patch_plan().
        repos_yaml_path: Path to repos.yaml on disk.
        allow_high_risk: If False, refuse high-risk patches.
        dry_run: If True, compute everything but don't write.

    Returns dict with:
        applied: list of PatchItem dicts that were applied
        skipped: list of PatchItem dicts that were skipped
        blocked: list of reasons for blocked patches
        backup_path: path to backup file (None if dry_run)
        rollback: RollbackRecord dict (None if dry_run)
        patched_yaml: the new repos.yaml content (always computed)
    """
    result: Dict[str, Any] = {
        "applied": [],
        "skipped": [],
        "blocked": [],
        "backup_path": None,
        "rollback": None,
        "patched_yaml": None,
    }

    # Risk gate
    blocking = check_risk_gate(items, allow_high_risk=allow_high_risk)
    if blocking:
        result["blocked"] = blocking
        return result

    # Separate active from skipped
    active_items = [i for i in items if not i.skipped]
    skipped_items = [i for i in items if i.skipped]

    result["skipped"] = [i.to_dict() for i in skipped_items]

    if not active_items:
        result["applied"] = []
        return result

    # Compute patched YAML
    patched = apply_plan_to_yaml(repos_yaml, active_items)
    result["patched_yaml"] = patched
    result["applied"] = [i.to_dict() for i in active_items]

    if dry_run:
        return result

    # Create backup
    now = time.time()
    backup_path = create_backup(repos_yaml_path, suffix=str(int(now)))
    result["backup_path"] = backup_path

    # Atomic write
    _write_yaml_atomic(patched, repos_yaml_path)

    # Write rollback artifact
    rollback = RollbackRecord(
        applied_at=now,
        repos_yaml_path=repos_yaml_path,
        backup_path=backup_path,
        items_applied=[i.to_dict() for i in active_items],
        original_snapshot=repos_yaml,
    )
    result["rollback"] = rollback.to_dict()

    return result


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback_from_backup(
    repos_yaml_path: str,
    backup_path: str,
) -> bool:
    """Restore repos.yaml from a backup file.

    Returns True if successful.
    """
    if not os.path.isfile(backup_path):
        return False

    shutil.copy2(backup_path, repos_yaml_path)
    return True


def rollback_from_record(
    rollback_record: Dict[str, Any],
) -> bool:
    """Restore repos.yaml from a rollback record.

    Uses the backup_path stored in the record.
    Returns True if successful.
    """
    repos_path = rollback_record.get("repos_yaml_path", "")
    backup_path = rollback_record.get("backup_path", "")

    if not repos_path or not backup_path:
        return False

    return rollback_from_backup(repos_path, backup_path)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def _write_yaml_atomic(
    data: Dict[str, Any],
    target_path: str,
) -> None:
    """Write YAML/JSON atomically via tmp+rename."""
    import tempfile

    target_dir = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(target_dir, exist_ok=True)

    content = _serialize_yaml(data)

    fd, tmp_path = tempfile.mkstemp(
        dir=target_dir,
        prefix=".cts-apply-",
        suffix=".yaml",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _serialize_yaml(data: Dict[str, Any]) -> str:
    """Serialize to YAML if available, otherwise JSON."""
    if HAS_YAML:
        return yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
    return json.dumps(data, indent=2, sort_keys=True, default=str) + "\n"


def write_rollback(
    rollback: Dict[str, Any],
    out_path: str,
) -> None:
    """Write rollback record to a JSON file."""
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rollback, f, indent=2, default=str)
        f.write("\n")
