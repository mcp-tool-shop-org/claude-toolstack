"""Experiment registry: archive experiment runs into a durable layout.

On-disk convention::

    experiments/
      <exp_id>/
        experiment.json          — envelope (immutable once archived)
        variants/
          tuning_A.json          — per-variant tuning
          patch_A.diff           — per-variant patch preview
          tuning_B.json
          patch_B.diff
        results/
          run_<timestamp>/
            result.json          — evaluation result
            result.md            — rendered markdown
        meta.json                — hashes + timestamps

Immutability rules:
  - Never edit prior results; append new ones under results/run_<ts>/
  - Reruns produce a new results/ subdirectory
  - meta.json tracks content hashes for integrity verification
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

REGISTRY_VERSION = 1


def _file_hash(path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_hash(content: str) -> str:
    """Compute SHA-256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Meta.json builder
# ---------------------------------------------------------------------------


def build_meta(
    *,
    exp_id: str,
    experiment_path: str,
    variant_files: Dict[str, str],
    result_path: Optional[str] = None,
    repos_yaml_path: Optional[str] = None,
    corpus_paths: Optional[List[str]] = None,
    run_id: str = "",
) -> Dict[str, Any]:
    """Build a meta.json dict with content hashes.

    Args:
        exp_id: Experiment identifier.
        experiment_path: Path to experiment.json.
        variant_files: Mapping {filename: path} for variant artifacts.
        result_path: Path to result.json (optional).
        repos_yaml_path: Path to repos.yaml (optional).
        corpus_paths: Paths to corpus files (optional).
        run_id: Run identifier for this archival.

    Returns:
        Meta dict suitable for writing as JSON.
    """
    meta: Dict[str, Any] = {
        "registry_version": REGISTRY_VERSION,
        "exp_id": exp_id,
        "archived_at": time.time(),
        "run_id": run_id,
        "hashes": {},
    }

    # Experiment hash
    if os.path.exists(experiment_path):
        meta["hashes"]["experiment"] = _file_hash(experiment_path)

    # Variant hashes
    variant_hashes: Dict[str, str] = {}
    for name, path in variant_files.items():
        if os.path.exists(path):
            variant_hashes[name] = _file_hash(path)
    if variant_hashes:
        meta["hashes"]["variants"] = variant_hashes

    # Result hash
    if result_path and os.path.exists(result_path):
        meta["hashes"]["result"] = _file_hash(result_path)

    # Repos.yaml hash
    if repos_yaml_path and os.path.exists(repos_yaml_path):
        meta["hashes"]["repos_yaml"] = _file_hash(repos_yaml_path)

    # Corpus hashes
    if corpus_paths:
        corpus_hashes: Dict[str, str] = {}
        for cp in corpus_paths:
            if os.path.exists(cp):
                corpus_hashes[os.path.basename(cp)] = _file_hash(cp)
        if corpus_hashes:
            meta["hashes"]["corpus"] = corpus_hashes

    return meta


# ---------------------------------------------------------------------------
# Archive command
# ---------------------------------------------------------------------------


_RUN_COUNTER = 0


def _generate_run_id() -> str:
    """Generate a timestamped run ID with sub-second uniqueness."""
    global _RUN_COUNTER  # noqa: PLW0603
    _RUN_COUNTER += 1
    ts = int(time.time() * 1000)
    return f"run_{ts}_{_RUN_COUNTER}"


def archive_experiment(
    *,
    experiment_path: str,
    result_path: Optional[str] = None,
    result_md_path: Optional[str] = None,
    variant_dir: Optional[str] = None,
    repos_yaml_path: Optional[str] = None,
    corpus_paths: Optional[List[str]] = None,
    registry_root: str = "experiments",
) -> Dict[str, Any]:
    """Archive an experiment run into the registry.

    Copies all artifacts into the standardized directory layout.
    Idempotent: if the same experiment+result hash exists, returns
    a no-op result.

    Args:
        experiment_path: Path to experiment.json.
        result_path: Path to result.json (optional).
        result_md_path: Path to result.md (optional).
        variant_dir: Directory containing variant artifacts (optional).
        repos_yaml_path: Path to repos.yaml (optional).
        corpus_paths: Corpus file paths (optional).
        registry_root: Root directory for the registry.

    Returns:
        Summary dict with archive status and paths.
    """
    # Load experiment to get ID
    with open(experiment_path, encoding="utf-8") as f:
        exp_data = json.load(f)

    exp_id = exp_data.get("id", "unknown")
    exp_dir = os.path.join(registry_root, exp_id)

    # Create directory structure
    variants_dir = os.path.join(exp_dir, "variants")
    results_dir = os.path.join(exp_dir, "results")
    os.makedirs(variants_dir, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)

    # Copy experiment.json (only if not already there)
    dest_exp = os.path.join(exp_dir, "experiment.json")
    if not os.path.exists(dest_exp):
        shutil.copy2(experiment_path, dest_exp)

    # Copy variant artifacts
    variant_files: Dict[str, str] = {}
    if variant_dir and os.path.isdir(variant_dir):
        for fname in os.listdir(variant_dir):
            src = os.path.join(variant_dir, fname)
            if os.path.isfile(src) and fname != "experiment.json":
                dest = os.path.join(variants_dir, fname)
                if not os.path.exists(dest):
                    shutil.copy2(src, dest)
                variant_files[fname] = src

    # Check idempotency: if result has same hash as existing run
    result_hash = None
    if result_path and os.path.exists(result_path):
        result_hash = _file_hash(result_path)

        # Scan existing runs for duplicate
        if os.path.isdir(results_dir):
            for existing_run in os.listdir(results_dir):
                existing_result = os.path.join(results_dir, existing_run, "result.json")
                if os.path.exists(existing_result):
                    if _file_hash(existing_result) == result_hash:
                        return {
                            "status": "already_archived",
                            "exp_id": exp_id,
                            "exp_dir": exp_dir,
                            "run_id": existing_run,
                            "message": (
                                f"Result already archived (hash: {result_hash[:12]}…)"
                            ),
                        }

    # Create new run directory
    run_id = _generate_run_id()
    run_dir = os.path.join(results_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    # Copy result files
    if result_path and os.path.exists(result_path):
        shutil.copy2(result_path, os.path.join(run_dir, "result.json"))

    if result_md_path and os.path.exists(result_md_path):
        shutil.copy2(result_md_path, os.path.join(run_dir, "result.md"))

    # Build and write meta.json
    meta = build_meta(
        exp_id=exp_id,
        experiment_path=experiment_path,
        variant_files=variant_files,
        result_path=result_path,
        repos_yaml_path=repos_yaml_path,
        corpus_paths=corpus_paths,
        run_id=run_id,
    )

    meta_path = os.path.join(exp_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)
        f.write("\n")

    return {
        "status": "archived",
        "exp_id": exp_id,
        "exp_dir": exp_dir,
        "run_id": run_id,
        "run_dir": run_dir,
        "meta_path": meta_path,
        "result_hash": result_hash,
    }


def validate_registry_entry(exp_dir: str) -> List[str]:
    """Validate an archived experiment directory.

    Returns list of error strings (empty = valid).
    """
    errors: List[str] = []

    # Check experiment.json exists
    exp_path = os.path.join(exp_dir, "experiment.json")
    if not os.path.exists(exp_path):
        errors.append("Missing experiment.json")
    else:
        try:
            with open(exp_path, encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError:
            errors.append("Invalid experiment.json (bad JSON)")

    # Check meta.json
    meta_path = os.path.join(exp_dir, "meta.json")
    if not os.path.exists(meta_path):
        errors.append("Missing meta.json")
    else:
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("registry_version") != REGISTRY_VERSION:
                errors.append(
                    f"Unsupported registry version: {meta.get('registry_version')}"
                )
        except json.JSONDecodeError:
            errors.append("Invalid meta.json (bad JSON)")

    # Check directories exist
    variants_dir = os.path.join(exp_dir, "variants")
    if not os.path.isdir(variants_dir):
        errors.append("Missing variants/ directory")

    results_dir = os.path.join(exp_dir, "results")
    if not os.path.isdir(results_dir):
        errors.append("Missing results/ directory")

    # Check for non-allowed file types (only JSON/MD/diff)
    allowed_ext = {".json", ".md", ".diff", ".jsonl"}
    for root, _dirs, files in os.walk(exp_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext and ext not in allowed_ext:
                rel = os.path.relpath(os.path.join(root, fname), exp_dir)
                errors.append(f"Disallowed file type: {rel} ({ext})")

    return errors
