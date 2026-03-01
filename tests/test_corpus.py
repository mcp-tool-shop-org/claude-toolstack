"""Tests for cts.corpus — corpus analytics pipeline."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from cts.corpus.extract import extract_passes, extract_record
from cts.corpus.load import load_artifact
from cts.corpus.report import (
    _aggregate,
    _bucket_deltas,
    _mean,
    _median,
    _percentile,
    generate_report,
    load_corpus,
)
from cts.corpus.scan import scan_dir
from cts.corpus.store import write_corpus, write_passes
from cts.schema import wrap_bundle


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_bundle(
    mode: str = "default",
    truncated: bool = False,
    with_debug: bool = False,
) -> dict:
    """Create a valid inner evidence bundle."""
    bundle: dict = {
        "version": 2,
        "mode": mode,
        "repo": "org/repo",
        "request_id": "test-r1",
        "query": "login",
        "ranked_sources": [
            {"path": "src/auth.py", "line": 10, "score": 1.5, "in_trace": True},
            {"path": "src/session.py", "line": 5, "score": 0.8},
            {"path": "lib/utils.py", "line": 20, "score": 0.4},
        ],
        "matches": [
            {"path": "src/auth.py", "line": 10, "snippet": "def login():"},
            {"path": "src/session.py", "line": 5, "snippet": "login(user)"},
            {"path": "lib/utils.py", "line": 20, "snippet": "validate(login)"},
        ],
        "slices": [
            {"path": "src/auth.py", "start": 1, "lines": ["..."] * 30},
            {"path": "src/session.py", "start": 1, "lines": ["..."] * 30},
            {"path": "lib/utils.py", "start": 1, "lines": ["..."] * 30},
        ],
        "symbols": [],
        "diff": "",
        "suggested_commands": [],
        "notes": [],
        "truncated": truncated,
    }
    if with_debug:
        bundle["_debug"] = {
            "timings": {"ranking": 12.5, "slice_fetch": 45.2, "diff_parse": 3.1},
            "score_cards": [
                {
                    "path": "src/auth.py",
                    "score_total": 1.5,
                    "features": {"is_prob_def": True, "is_def_file": False},
                }
            ],
        }
    return bundle


def _make_sidecar(
    mode: str = "default",
    request_id: str = "r1",
    repo: str = "org/repo",
    passes: list | None = None,
    truncated: bool = False,
    with_debug: bool = False,
) -> dict:
    """Create a valid sidecar envelope."""
    bundle = _base_bundle(mode=mode, truncated=truncated, with_debug=with_debug)
    return wrap_bundle(
        bundle,
        mode=mode,
        request_id=request_id,
        cli_version="0.2.0",
        repo=repo,
        query="login",
        debug=with_debug,
        passes=passes or [],
    )


def _sample_passes() -> list:
    """Create sample autopilot pass records."""
    return [
        {
            "pass": 1,
            "actions": ["force_trace_slices", "add_slices"],
            "action_details": [
                {
                    "name": "force_trace_slices",
                    "trigger_reason": "1 trace file(s) missing slices",
                    "trace_targets_count": 1,
                    "trace_targets": ["src/handler.py"],
                },
                {
                    "name": "add_slices",
                    "trigger_reason": "slice_coverage=0.050 < 0.1",
                },
            ],
            "confidence_before": 0.35,
            "reason": "Confidence 0.35 < 0.6 — weak signals: low_match_penalty",
            "elapsed_ms": 50.1,
            "status": "ok",
        }
    ]


def _write_sidecar(tmpdir: str, sidecar: dict, name: str = "artifact.json") -> str:
    """Write a sidecar dict to a temp file."""
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f)
    return path


# ===================================================================
# Scan tests
# ===================================================================


class TestScanDir(unittest.TestCase):
    def test_finds_json_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_sidecar(tmpdir, _make_sidecar(), "a.json")
            _write_sidecar(tmpdir, _make_sidecar(), "b.json")
            found = scan_dir(tmpdir)
            self.assertEqual(len(found), 2)

    def test_skips_junk_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_sidecar(tmpdir, _make_sidecar(), "good.json")
            node_dir = os.path.join(tmpdir, "node_modules")
            os.makedirs(node_dir)
            _write_sidecar(tmpdir, _make_sidecar(), "node_modules/bad.json")
            found = scan_dir(tmpdir)
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0].endswith("good.json"))

    def test_max_files_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                _write_sidecar(tmpdir, _make_sidecar(), f"a{i}.json")
            found = scan_dir(tmpdir, max_files=2)
            self.assertEqual(len(found), 2)

    def test_nonexistent_dir_returns_empty(self):
        found = scan_dir("/nonexistent/path/that/does/not/exist")
        self.assertEqual(found, [])

    def test_custom_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_sidecar(tmpdir, _make_sidecar(), "data.json")
            _write_sidecar(tmpdir, _make_sidecar(), "data.sidecar.json")
            found = scan_dir(tmpdir, patterns=["*.sidecar.json"])
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0].endswith(".sidecar.json"))

    def test_sorted_by_mtime_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_sidecar(tmpdir, _make_sidecar(), "old.json")
            time.sleep(0.05)
            _write_sidecar(tmpdir, _make_sidecar(), "new.json")
            found = scan_dir(tmpdir)
            self.assertTrue(found[0].endswith("new.json"))


# ===================================================================
# Load tests
# ===================================================================


class TestLoadArtifact(unittest.TestCase):
    def test_valid_sidecar_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_sidecar(tmpdir, _make_sidecar())
            data, errors = load_artifact(path)
            self.assertEqual(errors, [])
            self.assertIsNotNone(data)
            self.assertEqual(data["bundle_schema_version"], 1)

    def test_invalid_json_returns_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bad.json")
            with open(path, "w") as f:
                f.write("{invalid json")
            data, errors = load_artifact(path)
            self.assertIsNone(data)
            self.assertTrue(any("parse error" in e for e in errors))

    def test_non_sidecar_json_returns_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "not_sidecar.json")
            with open(path, "w") as f:
                json.dump({"hello": "world"}, f)
            data, errors = load_artifact(path)
            self.assertTrue(len(errors) > 0)

    def test_missing_file_returns_errors(self):
        data, errors = load_artifact("/does/not/exist.json")
        self.assertIsNone(data)
        self.assertTrue(any("read error" in e for e in errors))

    def test_json_array_returns_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "array.json")
            with open(path, "w") as f:
                json.dump([1, 2, 3], f)
            data, errors = load_artifact(path)
            self.assertIsNone(data)
            self.assertTrue(any("expected JSON object" in e for e in errors))


# ===================================================================
# Extract tests
# ===================================================================


class TestExtractRecord(unittest.TestCase):
    def test_valid_sidecar_extracts_identity(self):
        sidecar = _make_sidecar(mode="error", request_id="r42", repo="org/myrepo")
        rec = extract_record(sidecar, source_path="/tmp/test.json")
        self.assertEqual(rec.schema_version, 1)
        self.assertEqual(rec.repo, "org/myrepo")
        self.assertEqual(rec.mode, "error")
        self.assertEqual(rec.request_id, "r42")
        self.assertEqual(rec.source_path, "/tmp/test.json")

    def test_confidence_computed_for_no_passes(self):
        sidecar = _make_sidecar()
        rec = extract_record(sidecar)
        self.assertIsNotNone(rec.confidence_final)
        # With no passes, pass1 == final
        self.assertEqual(rec.confidence_pass1, rec.confidence_final)
        self.assertEqual(rec.confidence_delta, 0.0)

    def test_confidence_with_passes(self):
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        rec = extract_record(sidecar)
        self.assertEqual(rec.confidence_pass1, 0.35)
        self.assertIsNotNone(rec.confidence_final)
        self.assertIsNotNone(rec.confidence_delta)
        self.assertGreater(rec.confidence_delta, 0)

    def test_actions_extracted_from_passes(self):
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        rec = extract_record(sidecar)
        self.assertEqual(len(rec.actions), 2)
        self.assertEqual(rec.actions[0]["name"], "force_trace_slices")
        self.assertIn("trigger_reason", rec.actions[0])
        # Verify targets are counts only, no file paths
        self.assertIn("trace_targets_count", rec.actions[0])
        self.assertNotIn("trace_targets", rec.actions[0])

    def test_section_bytes_computed(self):
        sidecar = _make_sidecar()
        rec = extract_record(sidecar)
        self.assertGreater(rec.bundle_bytes_final, 0)
        self.assertIn("ranked_sources", rec.section_bytes)
        self.assertIn("slices", rec.section_bytes)
        self.assertGreater(rec.section_bytes["slices"], 0)

    def test_truncation_flags_extracted(self):
        sidecar = _make_sidecar(truncated=True)
        rec = extract_record(sidecar)
        self.assertTrue(rec.truncation_flags.get("truncated"))

    def test_truncation_flags_false_when_not_truncated(self):
        sidecar = _make_sidecar(truncated=False)
        rec = extract_record(sidecar)
        self.assertFalse(rec.truncation_flags.get("truncated", False))

    def test_timings_extracted_from_debug(self):
        sidecar = _make_sidecar(with_debug=True)
        rec = extract_record(sidecar)
        self.assertIn("ranking", rec.timings_ms)
        self.assertAlmostEqual(rec.timings_ms["ranking"], 12.5)

    def test_missing_debug_tracked(self):
        sidecar = _make_sidecar(with_debug=False)
        rec = extract_record(sidecar)
        self.assertIn("_debug", rec.missing_fields)

    def test_missing_debug_not_tracked_when_present(self):
        sidecar = _make_sidecar(with_debug=True)
        rec = extract_record(sidecar)
        self.assertNotIn("_debug", rec.missing_fields)

    def test_score_cards_improve_confidence(self):
        """With debug score_cards, definition_found signal should fire."""
        sidecar_no_debug = _make_sidecar(with_debug=False)
        sidecar_debug = _make_sidecar(with_debug=True)
        rec_no = extract_record(sidecar_no_debug)
        rec_yes = extract_record(sidecar_debug)
        # With score_cards that have is_prob_def=True, confidence should be higher
        self.assertGreaterEqual(rec_yes.confidence_final, rec_no.confidence_final)


class TestExtractPasses(unittest.TestCase):
    def test_no_passes_returns_empty(self):
        sidecar = _make_sidecar()
        records = extract_passes(sidecar)
        self.assertEqual(records, [])

    def test_pass_records_extracted(self):
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        records = extract_passes(sidecar)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].pass_index, 0)
        self.assertEqual(records[0].confidence, 0.35)
        expected_actions = ["force_trace_slices", "add_slices"]
        self.assertEqual(records[0].actions_this_pass, expected_actions)
        self.assertEqual(records[0].status, "ok")
        self.assertAlmostEqual(records[0].elapsed_ms, 50.1)

    def test_pass_details_sanitized(self):
        """Action details should contain counts but not file path lists."""
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        records = extract_passes(sidecar)
        details = records[0].action_details
        self.assertEqual(details[0]["name"], "force_trace_slices")
        self.assertIn("trace_targets_count", details[0])
        # File paths should NOT appear in sanitized details
        self.assertNotIn("trace_targets", details[0])

    def test_request_id_propagated(self):
        sidecar = _make_sidecar(request_id="my-req-99")
        sidecar["passes"] = _sample_passes()
        records = extract_passes(sidecar)
        self.assertEqual(records[0].request_id, "my-req-99")


# ===================================================================
# Store tests
# ===================================================================


class TestStore(unittest.TestCase):
    def test_write_and_read_corpus(self):
        rec = extract_record(_make_sidecar())
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "corpus.jsonl")
            written = write_corpus([rec], path)
            self.assertEqual(written, 1)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            obj = json.loads(lines[0])
            self.assertEqual(obj["repo"], "org/repo")

    def test_write_passes(self):
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        pass_recs = extract_passes(sidecar)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "passes.jsonl")
            written = write_passes(pass_recs, path)
            self.assertEqual(written, 1)
            with open(path) as f:
                obj = json.loads(f.readline())
            expected = ["force_trace_slices", "add_slices"]
            self.assertEqual(obj["actions_this_pass"], expected)

    def test_append_mode(self):
        rec1 = extract_record(_make_sidecar(request_id="r1"))
        rec2 = extract_record(_make_sidecar(request_id="r2"))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "corpus.jsonl")
            write_corpus([rec1], path)
            write_corpus([rec2], path, append=True)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)

    def test_to_dict_roundtrip(self):
        rec = extract_record(_make_sidecar(with_debug=True))
        d = rec.to_dict()
        # Verify all expected keys present
        for key in [
            "schema_version",
            "repo",
            "mode",
            "created_at",
            "request_id",
            "passes_count",
            "confidence_pass1",
            "confidence_final",
            "confidence_delta",
            "actions",
            "bundle_bytes_final",
            "section_bytes",
            "truncation_flags",
            "timings_ms",
            "missing_fields",
        ]:
            self.assertIn(key, d, f"Missing key: {key}")
        # Verify JSON-serializable
        json.dumps(d, default=str)


# ===================================================================
# Report tests
# ===================================================================


def _corpus_records() -> list:
    """Create a list of corpus record dicts for report testing."""
    records = []
    for mode in ["default", "error", "symbol", "change"]:
        sidecar = _make_sidecar(mode=mode, request_id=f"r-{mode}")
        rec = extract_record(sidecar)
        records.append(rec.to_dict())

    # Add one with autopilot passes
    sidecar_ap = _make_sidecar(mode="error", request_id="r-ap", passes=_sample_passes())
    rec_ap = extract_record(sidecar_ap)
    records.append(rec_ap.to_dict())

    # Add one truncated
    sidecar_tr = _make_sidecar(mode="change", request_id="r-tr", truncated=True)
    rec_tr = extract_record(sidecar_tr)
    records.append(rec_tr.to_dict())

    return records


class TestStatsHelpers(unittest.TestCase):
    def test_percentile_empty(self):
        self.assertEqual(_percentile([], 50), 0.0)

    def test_percentile_single(self):
        self.assertEqual(_percentile([5.0], 50), 5.0)

    def test_percentile_median(self):
        self.assertAlmostEqual(_percentile([1.0, 2.0, 3.0], 50), 2.0)

    def test_percentile_p90(self):
        vals = list(range(1, 101))
        p90 = _percentile([float(v) for v in vals], 90)
        self.assertAlmostEqual(p90, 90.1, places=1)

    def test_mean_empty(self):
        self.assertEqual(_mean([]), 0.0)

    def test_mean_values(self):
        self.assertAlmostEqual(_mean([2.0, 4.0, 6.0]), 4.0)

    def test_median_odd(self):
        self.assertAlmostEqual(_median([1.0, 3.0, 5.0]), 3.0)

    def test_bucket_deltas(self):
        deltas = [-0.1, 0.0, 0.05, 0.15, 0.3]
        buckets = _bucket_deltas(deltas)
        self.assertEqual(buckets["<0"], 1)
        self.assertEqual(buckets["0-0.1"], 2)
        self.assertEqual(buckets["0.1-0.25"], 1)
        self.assertEqual(buckets[">0.25"], 1)


class TestAggregate(unittest.TestCase):
    def test_total_count(self):
        records = _corpus_records()
        agg = _aggregate(records)
        self.assertEqual(agg["total"], len(records))

    def test_mode_distribution(self):
        records = _corpus_records()
        agg = _aggregate(records)
        self.assertIn("error", agg["mode_counts"])
        # Two error records (one base + one with autopilot)
        self.assertEqual(agg["mode_counts"]["error"], 2)

    def test_autopilot_counts(self):
        records = _corpus_records()
        agg = _aggregate(records)
        # Only the autopilot record has passes
        self.assertEqual(agg["autopilot_enabled"], 1)
        self.assertEqual(agg["autopilot_disabled"], 5)

    def test_action_counts(self):
        records = _corpus_records()
        agg = _aggregate(records)
        self.assertIn("force_trace_slices", agg["action_counts"])
        self.assertEqual(agg["action_counts"]["force_trace_slices"], 1)

    def test_confidence_stats(self):
        records = _corpus_records()
        agg = _aggregate(records)
        ds = agg["delta_stats"]
        self.assertIn("mean", ds)
        self.assertIn("median", ds)
        self.assertIn("buckets", ds)

    def test_truncation_count(self):
        records = _corpus_records()
        agg = _aggregate(records)
        self.assertEqual(agg["truncated_count"], 1)

    def test_size_stats_present(self):
        records = _corpus_records()
        agg = _aggregate(records)
        self.assertIn("p50", agg["size_stats"])
        self.assertIn("p90", agg["size_stats"])
        self.assertGreater(agg["size_stats"]["p50"], 0)

    def test_mode_filter(self):
        records = _corpus_records()
        agg = _aggregate(records, mode_filter="symbol")
        self.assertEqual(agg["total"], 1)
        self.assertEqual(agg["mode_counts"], {"symbol": 1})

    def test_repo_filter(self):
        records = _corpus_records()
        agg = _aggregate(records, repo_filter="org/repo")
        self.assertEqual(agg["total"], len(records))  # all same repo

    def test_action_filter(self):
        records = _corpus_records()
        agg = _aggregate(records, action_filter="force_trace_slices")
        self.assertEqual(agg["total"], 1)

    def test_empty_records(self):
        agg = _aggregate([])
        self.assertEqual(agg["total"], 0)


class TestGenerateReport(unittest.TestCase):
    def test_markdown_format(self):
        records = _corpus_records()
        report = generate_report(records, format="markdown")
        self.assertIn("# Sidecar Corpus Report", report)
        self.assertIn("## Coverage", report)
        self.assertIn("## Mode distribution", report)

    def test_text_format(self):
        records = _corpus_records()
        report = generate_report(records, format="text")
        self.assertIn("Sidecar Corpus Report", report)
        self.assertIn("Artifacts:", report)

    def test_json_format(self):
        records = _corpus_records()
        report = generate_report(records, format="json")
        data = json.loads(report)
        self.assertEqual(data["total"], len(records))

    def test_empty_corpus(self):
        report = generate_report([], format="markdown")
        self.assertIn("No records to report", report)

    def test_markdown_headings_stable(self):
        """Golden test: verify all expected section headings are present."""
        records = _corpus_records()
        report = generate_report(records, format="markdown")
        expected_headings = [
            "# Sidecar Corpus Report",
            "## Coverage",
            "## Mode distribution",
            "## Autopilot usage",
            "## Action effectiveness (confidence lift)",
            "## Trigger reasons (top)",
            "## Truncation hot spots",
            "## Bundle size distribution",
            "## Timing hot spots",
            "## Low-lift autopilot cases",
            "## Recommendations",
        ]
        for heading in expected_headings:
            self.assertIn(heading, report, f"Missing heading: {heading}")

    def test_report_with_filters(self):
        records = _corpus_records()
        report = generate_report(records, format="markdown", mode_filter="error")
        self.assertIn("# Sidecar Corpus Report", report)


class TestLoadCorpus(unittest.TestCase):
    def test_load_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "corpus.jsonl")
            records = _corpus_records()
            with open(path, "w") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
            loaded = load_corpus(path)
            self.assertEqual(len(loaded), len(records))

    def test_load_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "corpus.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"repo": "a"}) + "\n")
                f.write("\n")
                f.write(json.dumps({"repo": "b"}) + "\n")
            loaded = load_corpus(path)
            self.assertEqual(len(loaded), 2)


# ===================================================================
# Policy-lint / guardrail tests
# ===================================================================


class TestCorpusSecurityGuardrails(unittest.TestCase):
    """Ensure corpus output never leaks secrets or raw content."""

    def test_extract_record_contains_no_file_content(self):
        """Corpus records must not include slice content, diff text, or snippets."""
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        rec = extract_record(sidecar)
        d = rec.to_dict()
        serialized = json.dumps(d)
        # Should not contain slice line content
        self.assertNotIn('"lines"', serialized)
        # Should not contain diff text
        self.assertNotIn("+++ b/", serialized)
        # Should not contain match snippets
        self.assertNotIn("def login", serialized)

    def test_extract_passes_no_file_paths_in_targets(self):
        """Pass records should not include raw file paths from targets."""
        sidecar = _make_sidecar(mode="error", passes=_sample_passes())
        records = extract_passes(sidecar)
        for rec in records:
            d = rec.to_dict()
            serialized = json.dumps(d)
            # trace_targets (file path list) should be stripped
            self.assertNotIn("src/handler.py", serialized)
            # Count keys are fine
            if rec.action_details:
                for detail in rec.action_details:
                    self.assertNotIn("trace_targets", detail)

    def test_report_output_no_private_key_markers(self):
        """Report must never include private key markers."""
        records = _corpus_records()
        for fmt in ("markdown", "text", "json"):
            report = generate_report(records, format=fmt)
            self.assertNotIn("PRIVATE KEY", report)
            self.assertNotIn("BEGIN RSA", report)
            self.assertNotIn("sk-ant-", report)

    def test_report_output_no_snippets(self):
        """Report must not contain code snippets from the bundle."""
        records = _corpus_records()
        report = generate_report(records, format="markdown")
        self.assertNotIn("def login", report)
        self.assertNotIn("validate(login)", report)

    def test_corpus_record_only_has_safe_fields(self):
        """Verify the to_dict schema contains only expected keys."""
        rec = extract_record(_make_sidecar())
        d = rec.to_dict()
        allowed_keys = {
            "schema_version",
            "repo",
            "mode",
            "created_at",
            "request_id",
            "source_path",
            "passes_count",
            "confidence_pass1",
            "confidence_final",
            "confidence_delta",
            "actions",
            "bundle_bytes_final",
            "section_bytes",
            "truncation_flags",
            "timings_ms",
            "missing_fields",
            # Semantic augmentation (Phase 4)
            "semantic_invoked",
            "semantic_time_ms",
            "semantic_hit_count",
            "semantic_action_fired",
            "semantic_lift",
            # Candidate narrowing (Phase 4.2)
            "semantic_candidate_strategy",
            "semantic_candidate_files",
            "semantic_candidate_chunks",
            "semantic_candidate_fallback_used",
        }
        self.assertEqual(set(d.keys()), allowed_keys)


# ===================================================================
# End-to-end pipeline test
# ===================================================================


class TestEndToEndPipeline(unittest.TestCase):
    def test_scan_load_extract_store_report(self):
        """Full pipeline: scan → load → extract → store → report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create artifacts
            for i, mode in enumerate(["default", "error", "symbol"]):
                sc = _make_sidecar(mode=mode, request_id=f"e2e-{i}")
                _write_sidecar(tmpdir, sc, f"art{i}.json")

            # Also create a non-sidecar file (should be skipped)
            with open(os.path.join(tmpdir, "package.json"), "w") as f:
                json.dump({"name": "test"}, f)

            # Scan
            candidates = scan_dir(tmpdir)
            self.assertEqual(len(candidates), 4)  # 3 sidecars + 1 non-sidecar

            # Load + extract
            records = []
            invalid = 0
            for path in candidates:
                data, errors = load_artifact(path)
                if errors:
                    invalid += 1
                    continue
                records.append(extract_record(data, source_path=path))

            self.assertEqual(len(records), 3)
            self.assertEqual(invalid, 1)

            # Store
            corpus_path = os.path.join(tmpdir, "corpus.jsonl")
            write_corpus(records, corpus_path)

            # Load corpus
            loaded = load_corpus(corpus_path)
            self.assertEqual(len(loaded), 3)

            # Report
            report = generate_report(loaded, format="markdown")
            self.assertIn("# Sidecar Corpus Report", report)
            self.assertIn("## Coverage", report)
            self.assertIn("3", report)  # 3 artifacts


if __name__ == "__main__":
    unittest.main()
