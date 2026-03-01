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
from cts.corpus.apply import (
    RollbackRecord,
    apply_patch_plan,
    check_risk_gate,
    rollback_from_backup,
    rollback_from_record,
)
from cts.corpus.evaluate import (
    compare_kpis,
    evaluate,
    extract_kpis,
)
from cts.corpus.experiment_schema import (
    ExperimentEnvelope,
    VariantSpec,
    create_experiment,
    validate_experiment,
)
from cts.corpus.patch import (
    PatchItem,
    generate_patch_plan,
    render_plan_diff,
    render_plan_json,
    render_plan_text,
)
from cts.corpus.baseline import (
    capture_baseline,
    render_baseline_json,
    render_baseline_markdown,
    render_baseline_text,
)
from cts.corpus.archive import (
    archive_experiment,
    validate_registry_entry,
)
from cts.corpus.registry import (
    filter_entries,
    scan_registry,
    show_experiment,
)
from cts.corpus.experiment_eval import (
    assign_records,
    evaluate_experiment,
    pick_winner,
)
from cts.corpus.tuning_schema import (
    TuningEnvelope,
    TuningRecommendation,
    generate_tuning,
)
from cts.corpus.trends import (
    compute_kpi_trends,
    compute_regressions,
    compute_win_rates,
    compute_winning_knobs,
    extract_data_points,
    generate_dashboard,
    render_dashboard_json,
    render_dashboard_markdown,
    render_dashboard_text,
)
from cts.corpus.variants import (
    generate_variants,
    make_variant_tuning,
    propose_experiment,
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
    "apply_patch_plan",
    "check_risk_gate",
    "rollback_from_backup",
    "rollback_from_record",
    "evaluate",
    "extract_kpis",
    "compare_kpis",
    "CorpusRecord",
    "PassRecord",
    "PatchItem",
    "RollbackRecord",
    "ExperimentEnvelope",
    "VariantSpec",
    "create_experiment",
    "validate_experiment",
    "TuningEnvelope",
    "TuningRecommendation",
    "archive_experiment",
    "validate_registry_entry",
    "scan_registry",
    "filter_entries",
    "show_experiment",
    "assign_records",
    "evaluate_experiment",
    "pick_winner",
    "generate_variants",
    "make_variant_tuning",
    "propose_experiment",
    "extract_data_points",
    "compute_win_rates",
    "compute_kpi_trends",
    "compute_winning_knobs",
    "compute_regressions",
    "generate_dashboard",
    "render_dashboard_markdown",
    "render_dashboard_json",
    "render_dashboard_text",
    "capture_baseline",
    "render_baseline_json",
    "render_baseline_markdown",
    "render_baseline_text",
]
