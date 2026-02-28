"""Corpus analytics: ingest sidecar artifacts into structured JSONL.

Provides scan → load → extract → store pipeline for turning
CI-produced sidecar artifacts into a normalized corpus suitable
for aggregate reporting and tuning analysis.
"""

from cts.corpus.extract import extract_passes, extract_record
from cts.corpus.load import load_artifact
from cts.corpus.model import CorpusRecord, PassRecord
from cts.corpus.report import generate_report
from cts.corpus.scan import scan_dir
from cts.corpus.store import write_corpus, write_passes
from cts.corpus.patch import (
    PatchItem,
    generate_patch_plan,
    render_plan_diff,
    render_plan_json,
    render_plan_text,
)
from cts.corpus.tuning_schema import (
    TuningEnvelope,
    TuningRecommendation,
    generate_tuning,
)

__all__ = [
    "scan_dir",
    "load_artifact",
    "extract_record",
    "extract_passes",
    "write_corpus",
    "write_passes",
    "generate_report",
    "generate_tuning",
    "generate_patch_plan",
    "render_plan_json",
    "render_plan_diff",
    "render_plan_text",
    "CorpusRecord",
    "PassRecord",
    "PatchItem",
    "TuningEnvelope",
    "TuningRecommendation",
]
