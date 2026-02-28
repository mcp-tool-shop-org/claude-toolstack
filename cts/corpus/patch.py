"""Patch planner: map tuning recommendations to concrete config edits.

Takes a tuning envelope (from ``generate_tuning``) and a repos.yaml
and produces a *patch plan* — a structured, reviewable description of
every edit that *would* be applied.  **This module never writes files.**
The apply step (Commit 3) consumes the patch plan.

Patch targets are dotted paths in ``repos.yaml``::

    defaults.bundle.default.max_bytes          → global bundle knob
    defaults.bundle.<mode>.context_lines       → per-mode bundle knob
    defaults.autopilot.sufficient_threshold    → autopilot threshold
    defaults.autopilot.default_passes          → autopilot pass count
    defaults.autopilot.actions.<name>.enabled  → per-action toggle

repos.yaml layout expected::

    defaults:
      bundle:
        default: { max_bytes: 524288, context_lines: 30, evidence_files: 5 }
        error:   { ... }
      autopilot:
        sufficient_threshold: 0.6
        default_passes: 0
        actions:
          widen_search: { enabled: true }
          ...
    repos:
      org/repo:
        url: ...
        overrides:
          bundle: { ... }  # per-repo overrides

Output formats:
  - ``json``: machine-readable patch plan (list of patch items)
  - ``diff``: human-readable unified diff of repos.yaml
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Tuple

# YAML is optional — we try to import it but fall back to JSON round-trip
try:
    import yaml  # type: ignore[import-untyped]

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Patch item model
# ---------------------------------------------------------------------------


class PatchItem:
    """A single concrete edit to repos.yaml."""

    __slots__ = (
        "recommendation_id",
        "yaml_path",
        "old_value",
        "new_value",
        "risk",
        "rationale",
        "rollback",
        "skipped",
        "skip_reason",
    )

    def __init__(
        self,
        *,
        recommendation_id: str,
        yaml_path: str,
        old_value: Any = None,
        new_value: Any = None,
        risk: str = "low",
        rationale: str = "",
        rollback: str = "",
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.recommendation_id = recommendation_id
        self.yaml_path = yaml_path
        self.old_value = old_value
        self.new_value = new_value
        self.risk = risk
        self.rationale = rationale
        self.rollback = rollback
        self.skipped = skipped
        self.skip_reason = skip_reason

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "recommendation_id": self.recommendation_id,
            "yaml_path": self.yaml_path,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "risk": self.risk,
            "rationale": self.rationale,
            "rollback": self.rollback,
        }
        if self.skipped:
            d["skipped"] = True
            d["skip_reason"] = self.skip_reason
        return d


# ---------------------------------------------------------------------------
# YAML path helpers
# ---------------------------------------------------------------------------


def _get_nested(data: Dict[str, Any], dotted: str) -> Any:
    """Walk a dotted path like ``defaults.bundle.default.max_bytes``.

    Returns the value, or ``_MISSING`` sentinel if not found.
    """
    keys = dotted.split(".")
    current: Any = data
    for k in keys:
        if not isinstance(current, dict):
            return _MISSING
        current = current.get(k, _MISSING)
        if current is _MISSING:
            return _MISSING
    return current


def _set_nested(data: Dict[str, Any], dotted: str, value: Any) -> None:
    """Set a value at a dotted path, creating intermediate dicts."""
    keys = dotted.split(".")
    current = data
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value


class _MissingSentinel:
    """Sentinel for missing values (distinct from None)."""

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _MissingSentinel()


# ---------------------------------------------------------------------------
# Target mapping: recommendation target → repos.yaml dotted path
# ---------------------------------------------------------------------------

# Maps tuning_schema target prefixes to repos.yaml paths.
# The tuning schema uses targets like ``bundle.default.max_bytes``
# which map to ``defaults.bundle.default.max_bytes`` in repos.yaml.
_TARGET_PREFIX_MAP = {
    "bundle.": "defaults.bundle.",
    "autopilot.": "defaults.autopilot.",
}


def _map_target_to_yaml(target: str) -> str:
    """Convert a tuning recommendation target to a repos.yaml path.

    Example::

        bundle.default.max_bytes → defaults.bundle.default.max_bytes
        autopilot.actions.widen_search.enabled
            → defaults.autopilot.actions.widen_search.enabled
    """
    for prefix, yaml_prefix in _TARGET_PREFIX_MAP.items():
        if target.startswith(prefix):
            return yaml_prefix + target[len(prefix) :]
    # Fallback: assume it's already a full path
    return target


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------


def _resolve_value(
    change_type: str,
    current: Any,
    from_value: Any,
    to_value: Any,
) -> Tuple[Any, bool, str]:
    """Compute the new value given the change type.

    Returns (new_value, skipped, skip_reason).
    """
    if change_type == "set":
        return to_value, False, ""

    elif change_type == "toggle":
        return to_value, False, ""

    elif change_type == "delta":
        # to_value is a string like "+131072" or "-10"
        if current is _MISSING or current is None:
            return None, True, "Cannot apply delta — current value unknown"
        try:
            delta = int(str(to_value))
            base = int(current) if current != "current" else 0
            if current == "current":
                return None, True, "Cannot apply delta — current value is 'current'"
            return base + delta, False, ""
        except (ValueError, TypeError):
            return None, True, f"Cannot parse delta: {to_value!r}"

    elif change_type == "cap":
        # Enforce max/min — to_value is the cap
        if current is _MISSING or current is None:
            # Set as new value
            try:
                return int(str(to_value)), False, ""
            except (ValueError, TypeError):
                return to_value, False, ""
        try:
            cap_val = int(str(to_value))
            cur_val = int(current)
            return min(cur_val, cap_val), False, ""
        except (ValueError, TypeError):
            return to_value, False, ""

    else:
        return None, True, f"Unknown change_type: {change_type}"


# ---------------------------------------------------------------------------
# Patch plan generation
# ---------------------------------------------------------------------------


def generate_patch_plan(
    tuning: Dict[str, Any],
    repos_yaml: Dict[str, Any],
) -> List[PatchItem]:
    """Generate a patch plan from a tuning envelope and repos.yaml.

    Args:
        tuning: Parsed tuning envelope dict (from TuningEnvelope.to_dict()).
        repos_yaml: Parsed repos.yaml dict.

    Returns:
        List of PatchItem objects (some may be skipped).
    """
    recommendations = tuning.get("recommendations", [])
    items: List[PatchItem] = []

    for rec in recommendations:
        rec_id = rec.get("id", "unknown")
        target = rec.get("target", "")
        change_type = rec.get("change_type", "set")
        from_value = rec.get("from")
        to_value = rec.get("to")
        risk = rec.get("risk", "low")
        rationale = rec.get("rationale", "")
        rollback = rec.get("rollback", "")
        scope = rec.get("scope", "global")

        # Map target to YAML path
        yaml_path = _map_target_to_yaml(target)

        # Read current value from repos.yaml
        current = _get_nested(repos_yaml, yaml_path)
        old_val = None if current is _MISSING else current

        # Resolve the new value
        new_val, skipped, skip_reason = _resolve_value(
            change_type, current, from_value, to_value
        )

        # If the scope is per-repo, handle differently
        if scope.startswith("repo:"):
            repo_name = scope[5:]
            repo_path = f"repos.{repo_name}.overrides.{target}"
            repo_current = _get_nested(repos_yaml, repo_path)
            old_val = None if repo_current is _MISSING else repo_current
            yaml_path = repo_path
            # Re-resolve with repo-level current
            new_val, skipped, skip_reason = _resolve_value(
                change_type, repo_current, from_value, to_value
            )

        # Skip if new == old (no change needed)
        if not skipped and new_val == old_val and old_val is not None:
            skipped = True
            skip_reason = "Value already matches recommendation"

        items.append(
            PatchItem(
                recommendation_id=rec_id,
                yaml_path=yaml_path,
                old_value=old_val,
                new_value=new_val,
                risk=risk,
                rationale=rationale,
                rollback=rollback,
                skipped=skipped,
                skip_reason=skip_reason,
            )
        )

    return items


# ---------------------------------------------------------------------------
# Render: preview YAML (for diff generation)
# ---------------------------------------------------------------------------


def apply_plan_to_yaml(
    repos_yaml: Dict[str, Any],
    items: List[PatchItem],
) -> Dict[str, Any]:
    """Apply patch items to a copy of repos.yaml (in memory).

    Returns a new dict with all non-skipped edits applied.
    Only used for preview/diff — does NOT write to disk.
    """
    patched = copy.deepcopy(repos_yaml)
    for item in items:
        if item.skipped:
            continue
        _set_nested(patched, item.yaml_path, item.new_value)
    return patched


def render_plan_json(items: List[PatchItem]) -> str:
    """Render patch plan as JSON."""
    plan = {
        "patch_count": len([i for i in items if not i.skipped]),
        "skipped_count": len([i for i in items if i.skipped]),
        "items": [i.to_dict() for i in items],
    }
    return json.dumps(plan, indent=2, default=str)


def render_plan_diff(
    repos_yaml: Dict[str, Any],
    items: List[PatchItem],
    *,
    yaml_path: str = "repos.yaml",
) -> str:
    """Render patch plan as a unified diff of repos.yaml.

    If PyYAML is available, renders as YAML diff.  Otherwise,
    falls back to JSON diff (still perfectly readable).
    """
    before = _serialize_config(repos_yaml)
    patched = apply_plan_to_yaml(repos_yaml, items)
    after = _serialize_config(patched)

    if before == after:
        return "# No changes\n"

    return _unified_diff(before, after, yaml_path)


def render_plan_text(items: List[PatchItem]) -> str:
    """Render patch plan as human-readable text summary."""
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("TUNING PATCH PLAN")
    lines.append("=" * 60)
    lines.append("")

    active = [i for i in items if not i.skipped]
    skipped = [i for i in items if i.skipped]

    lines.append(f"Active patches: {len(active)}")
    lines.append(f"Skipped:        {len(skipped)}")
    lines.append("")

    if active:
        lines.append("-" * 40)
        lines.append("PATCHES TO APPLY")
        lines.append("-" * 40)
        for i, item in enumerate(active, 1):
            lines.append(f"\n{i}. [{item.risk.upper()}] {item.recommendation_id}")
            lines.append(f"   Path:      {item.yaml_path}")
            lines.append(f"   Old value: {item.old_value}")
            lines.append(f"   New value: {item.new_value}")
            if item.rationale:
                lines.append(f"   Reason:    {item.rationale}")
            if item.rollback:
                lines.append(f"   Rollback:  {item.rollback}")

    if skipped:
        lines.append(f"\n{'-' * 40}")
        lines.append("SKIPPED")
        lines.append("-" * 40)
        for item in skipped:
            lines.append(f"  - {item.recommendation_id}: {item.skip_reason}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialize_config(data: Dict[str, Any]) -> str:
    """Serialize config for diff — prefer YAML, fallback to JSON."""
    if HAS_YAML:
        return yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _unified_diff(before: str, after: str, filename: str) -> str:
    """Generate a unified diff between two strings."""
    import difflib

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return "".join(diff)


def load_tuning(path: str) -> Dict[str, Any]:
    """Load a tuning envelope from a JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_repos_yaml(path: str) -> Dict[str, Any]:
    """Load repos.yaml config.

    Uses PyYAML if available; otherwise falls back to a minimal
    parser that handles the simple key-value structure we need.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()

    if HAS_YAML:
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}

    # Minimal fallback: if it's valid JSON (some configs are), parse it
    content_stripped = content.strip()
    if content_stripped.startswith("{"):
        return json.loads(content_stripped)

    # Otherwise return empty — the patcher will create defaults
    return {}
