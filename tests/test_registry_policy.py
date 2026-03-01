"""Policy-lint tests for the experiment registry.

These tests enforce structural invariants:
1. Dashboard output contains no code/diff/slice payloads (size < 10 KB).
2. Registry directories only contain allowed file types.
3. Dashboard markdown uses stable headings (golden test).
"""

from __future__ import annotations

import json
import os
import time

from cts.corpus.trends import (
    generate_dashboard,
    render_dashboard_json,
    render_dashboard_markdown,
    render_dashboard_text,
)
from cts.corpus.archive import validate_registry_entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS = {".json", ".md", ".diff", ".jsonl"}

STABLE_HEADINGS = [
    "# Experiment Trend Dashboard",
    "## Summary",
    "## Win rates",
    "## KPI trends",
    "## Common winning changes",
    "## Regressions / constraint failures",
    "## Recent experiments",
]


def _build_registry(root: str, n_experiments: int = 5) -> None:
    """Build a realistic multi-experiment registry for policy tests."""
    strategies = ["conservative", "aggressive", "focused"]
    for i in range(n_experiments):
        exp_id = f"policy-exp-{i}"
        exp_dir = os.path.join(root, exp_id)
        os.makedirs(exp_dir, exist_ok=True)

        exp_data = {
            "id": exp_id,
            "created_at": time.time() - (i * 86400),
            "description": f"Policy test experiment {i}",
            "hypothesis": "Verify policy compliance",
            "variants": [{"name": "A"}, {"name": "B"}],
            "assignment": {"mode": "manual"},
            "decision_rule": {
                "primary_kpi": "confidence_final_mean",
                "constraints": [
                    {"kpi": "truncation_rate", "operator": "<=", "threshold": 0.05}
                ],
            },
        }
        with open(os.path.join(exp_dir, "experiment.json"), "w") as f:
            json.dump(exp_data, f)

        # Variant tuning files
        variants_dir = os.path.join(exp_dir, "variants")
        os.makedirs(variants_dir, exist_ok=True)
        strategy = strategies[i % len(strategies)]
        for vname in ("A", "B"):
            tuning = {
                "variant_metadata": {
                    "strategy": strategy if vname == "B" else "conservative",
                },
                "recommendations": [
                    {"target": "max_tokens", "current": 4000, "recommended": 3500},
                    {"target": "temperature", "current": 0.7, "recommended": 0.6},
                ],
            }
            with open(os.path.join(variants_dir, f"tuning_{vname}.json"), "w") as f:
                json.dump(tuning, f)

        # Result
        run_dir = os.path.join(exp_dir, "results", "run_001")
        os.makedirs(run_dir, exist_ok=True)
        winner = "B" if i % 3 != 2 else None
        verdict = "winner" if winner else "tie"
        result = {
            "verdict": verdict,
            "winner": winner,
            "reasoning": f"Test reasoning for exp {i}",
            "per_variant": {
                "A": {"kpis": {"confidence_final_mean": 0.70 + i * 0.005}},
                "B": {"kpis": {"confidence_final_mean": 0.72 + i * 0.005}},
            },
        }
        with open(os.path.join(run_dir, "result.json"), "w") as f:
            json.dump(result, f)

        # Meta
        meta = {
            "registry_version": 1,
            "archived_at": time.time(),
            "hashes": {},
        }
        with open(os.path.join(exp_dir, "meta.json"), "w") as f:
            json.dump(meta, f)


# ---------------------------------------------------------------------------
# Policy 1: Dashboard output size < 10 KB (no code/diff/slice payloads)
# ---------------------------------------------------------------------------


class TestDashboardSizePolicy:
    """Dashboard output must be lightweight — no embedded code or data blobs."""

    MAX_BYTES = 10 * 1024  # 10 KB

    def test_markdown_under_size_limit(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=10)

        dashboard = generate_dashboard(root)
        md = render_dashboard_markdown(dashboard)
        assert len(md.encode("utf-8")) < self.MAX_BYTES, (
            f"Markdown dashboard is {len(md.encode('utf-8'))} bytes, "
            f"exceeds {self.MAX_BYTES} byte limit"
        )

    def test_json_under_size_limit(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=10)

        dashboard = generate_dashboard(root)
        js = render_dashboard_json(dashboard)
        assert len(js.encode("utf-8")) < self.MAX_BYTES, (
            f"JSON dashboard is {len(js.encode('utf-8'))} bytes, "
            f"exceeds {self.MAX_BYTES} byte limit"
        )

    def test_text_under_size_limit(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=10)

        dashboard = generate_dashboard(root)
        txt = render_dashboard_text(dashboard)
        assert len(txt.encode("utf-8")) < self.MAX_BYTES, (
            f"Text dashboard is {len(txt.encode('utf-8'))} bytes, "
            f"exceeds {self.MAX_BYTES} byte limit"
        )

    def test_no_code_blocks_in_dashboard(self, tmp_path: str) -> None:
        """Dashboard must not contain fenced code blocks (```).
        Code/diff payloads belong in the registry, not the dashboard."""
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=5)

        dashboard = generate_dashboard(root)
        md = render_dashboard_markdown(dashboard)
        assert "```" not in md, (
            "Dashboard contains fenced code block — policy violation"
        )

    def test_no_diff_content_in_dashboard(self, tmp_path: str) -> None:
        """Dashboard must not contain raw diff hunks."""
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=5)

        dashboard = generate_dashboard(root)
        md = render_dashboard_markdown(dashboard)
        # Diff hunks start with @@ or +++ / ---
        lines = md.split("\n")
        for line in lines:
            stripped = line.strip()
            assert not stripped.startswith("@@"), (
                f"Dashboard contains diff hunk: {stripped[:60]}"
            )
            assert not (stripped.startswith("+++") and "/" in stripped), (
                f"Dashboard contains diff header: {stripped[:60]}"
            )


# ---------------------------------------------------------------------------
# Policy 2: Registry dir only contains allowed file types
# ---------------------------------------------------------------------------


class TestRegistryFileTypePolicy:
    """Only .json, .md, .diff, and .jsonl files in the registry."""

    def test_build_registry_has_only_allowed_types(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=3)

        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                assert ext in ALLOWED_EXTENSIONS, (
                    f"Disallowed file type '{ext}' in registry: "
                    f"{os.path.join(dirpath, fname)}"
                )

    def test_validate_rejects_disallowed_files(self, tmp_path: str) -> None:
        """validate_registry_entry should flag non-allowed file types."""
        exp_dir = str(tmp_path / "bad-exp")
        os.makedirs(exp_dir, exist_ok=True)

        # Write minimal required files
        with open(os.path.join(exp_dir, "experiment.json"), "w") as f:
            json.dump({"id": "bad", "variants": []}, f)

        # Plant a disallowed file
        with open(os.path.join(exp_dir, "code.py"), "w") as f:
            f.write("import os\n")

        errors = validate_registry_entry(exp_dir)
        # Should report the .py file as disallowed
        py_errors = [e for e in errors if ".py" in e]
        assert len(py_errors) > 0, (
            f"Expected validation error for .py file, got: {errors}"
        )

    def test_validate_accepts_clean_registry(self, tmp_path: str) -> None:
        """A properly built registry entry should pass validation."""
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=1)

        exp_dirs = [
            os.path.join(root, d)
            for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
        ]
        assert len(exp_dirs) == 1
        errors = validate_registry_entry(exp_dirs[0])
        assert errors == [], f"Clean registry entry has errors: {errors}"


# ---------------------------------------------------------------------------
# Policy 3: Stable headings golden test
# ---------------------------------------------------------------------------


class TestStableHeadingsPolicy:
    """Dashboard markdown must use exact stable heading strings.
    Downstream tools and CI parse these headings — any change is breaking."""

    def test_all_stable_headings_present(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=3)

        dashboard = generate_dashboard(root)
        md = render_dashboard_markdown(dashboard)

        for heading in STABLE_HEADINGS:
            assert heading in md, (
                f"Missing stable heading '{heading}' in dashboard output"
            )

    def test_headings_appear_in_order(self, tmp_path: str) -> None:
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=3)

        dashboard = generate_dashboard(root)
        md = render_dashboard_markdown(dashboard)

        positions = [md.index(h) for h in STABLE_HEADINGS]
        assert positions == sorted(positions), (
            "Stable headings are out of order in dashboard output"
        )

    def test_empty_dashboard_still_has_stable_headings(self) -> None:
        """Even with zero data, all stable headings must appear."""
        dashboard = {
            "total_experiments": 0,
            "with_results": 0,
            "win_rates": {"by_strategy": {}},
            "kpi_trends": {"values": []},
            "winning_knobs": [],
            "regressions": {"regression_count": 0, "verdict_counts": {}},
            "recent": [],
        }
        md = render_dashboard_markdown(dashboard)

        for heading in STABLE_HEADINGS:
            assert heading in md, (
                f"Missing stable heading '{heading}' in empty dashboard"
            )

    def test_headings_are_exact_match(self, tmp_path: str) -> None:
        """Headings must be exact — no extra whitespace or decoration."""
        root = str(tmp_path / "experiments")
        os.makedirs(root)
        _build_registry(root, n_experiments=1)

        dashboard = generate_dashboard(root)
        md = render_dashboard_markdown(dashboard)
        lines = md.split("\n")

        heading_lines = [line for line in lines if line.startswith("#")]
        for heading in STABLE_HEADINGS:
            assert heading in heading_lines, (
                f"Heading '{heading}' not found as an exact line. "
                f"Found heading lines: {heading_lines}"
            )
