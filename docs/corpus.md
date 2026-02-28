# Corpus Analytics

Turn CI-produced sidecar artifacts into aggregate reports that reveal
which actions lift confidence, where truncation hurts, and when the
autopilot wastes passes.

## Quick start

```bash
# 1. Download sidecar artifacts from CI
gh run download <run-id> -n 'sidecar-evidence-*' -D artifacts/

# 2. Ingest into corpus JSONL
cts corpus ingest artifacts/ --out corpus.jsonl

# 3. Generate report
cts corpus report corpus.jsonl --format markdown --out report.md
```

## Downloading artifacts from GitHub Actions

Each CI run uploads a sidecar artifact named
`sidecar-evidence-<sha>`. To download the last 10 runs:

```bash
# List recent runs
gh run list --limit 10 --json databaseId,headSha,status

# Download a specific run's artifact
gh run download <run-id> -n 'sidecar-evidence-*' -D artifacts/

# Or download all artifacts from recent runs
for id in $(gh run list --limit 10 --json databaseId -q '.[].databaseId'); do
  gh run download "$id" -D "artifacts/$id/" 2>/dev/null || true
done
```

## Ingestion

```bash
cts corpus ingest <dir> --out corpus.jsonl
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--out PATH` | `corpus.jsonl` | Output JSONL path |
| `--fail-on-invalid` | off | Exit 1 on first invalid artifact |
| `--max-files N` | unlimited | Cap scan at N files |
| `--since DAYS` | no filter | Only ingest artifacts within N days |
| `--include-passes` | off | Also write `corpus_passes.jsonl` |

### What gets extracted

Each ingested sidecar becomes one JSONL record containing:

- **Identity:** schema version, repo, mode, request ID, timestamp
- **Confidence:** pass1, final, delta (recomputed from the final bundle)
- **Actions:** ordered list with trigger reasons and target counts
- **Sizes:** total bundle bytes + per-section byte sizes
- **Truncation:** whether the bundle was truncated, and which sections
- **Timings:** per-lap milliseconds (from `_debug`, when available)
- **Missing fields:** tracks what couldn't be extracted (for visibility)

## Reporting

```bash
cts corpus report corpus.jsonl --format markdown --out report.md
```

### Formats

| Format | Use |
|--------|-----|
| `markdown` | PR comments, wiki pages, reviews |
| `text` | Terminal / quick inspection |
| `json` | Programmatic consumption, dashboards |

### Filters

```bash
# Only symbol-mode artifacts
cts corpus report corpus.jsonl --mode symbol

# Only a specific repo
cts corpus report corpus.jsonl --repo org/my-repo

# Only artifacts that used force_trace_slices
cts corpus report corpus.jsonl --action force_trace_slices
```

## Interpreting the report

### Mode distribution

Shows which bundle modes are used most. If `error` dominates, the
tool is primarily used for stack trace investigation. If `default`
dominates, most queries are general searches.

### Action effectiveness (confidence lift)

The most actionable section. Each action shows its mean and median
confidence delta. Actions with near-zero lift on high sample counts
are candidates for disabling or retargeting.

**Per-mode pass1 vs final** shows how much confidence improves
per mode. Modes with low final confidence need tuning.

**Bucketed deltas** show the distribution shape:
- `<0`: confidence went down (rare, indicates planner regression)
- `0-0.1`: negligible lift (planner cost without benefit)
- `0.1-0.25`: moderate improvement
- `>0.25`: strong improvement

### Truncation hot spots

Identifies which modes and which sections (slices, matches, diff)
trigger truncation most. High truncation in `slices` suggests the
context window is too small; in `diff` suggests large changesets.

### Low-lift autopilot cases

Runs where autopilot was enabled but delta < 0.05. These represent
wasted compute. Look at the dominant actions — if the same action
appears repeatedly with low lift, its trigger logic is too loose.

### Recommendations

Automatic hints based on detected patterns:
- High truncation rate → increase `max_bytes` or reduce context
- Low final confidence per mode → tune mode-specific signals
- Near-zero action lift → disable or revise trigger logic
- High fraction of low-lift runs → raise threshold or tighten planner

## Tuning automation

The full tuning pipeline turns corpus insights into reviewable
config changes with safety guardrails.

### Generate tuning recommendations

```bash
cts corpus report corpus.jsonl \
  --format json \
  --emit-tuning tuning.json
```

The `tuning.json` envelope contains machine-readable recommendations
with stable IDs, scopes, change types, evidence, risk levels, and
rollback instructions.

### Preview patch plan (dry-run)

```bash
# Human-readable summary
cts corpus patch tuning.json --repos-yaml repos.yaml

# Unified diff preview
cts corpus patch tuning.json --repos-yaml repos.yaml --format diff

# Machine-readable plan
cts corpus patch tuning.json --repos-yaml repos.yaml --format json \
  --out patch-plan.json
```

### Apply changes

```bash
# Dry run first
cts corpus apply tuning.json --repos-yaml repos.yaml --dry-run

# Apply for real (creates backup + rollback.json)
cts corpus apply tuning.json --repos-yaml repos.yaml

# High-risk patches are blocked by default
cts corpus apply tuning.json --repos-yaml repos.yaml --allow-high-risk
```

Safety measures:
- Timestamped backup (`repos.yaml.bak.<ts>`) before any edit
- High-risk patches blocked unless `--allow-high-risk`
- Atomic write (tmp + rename)
- Rollback artifact (`rollback.json`) for full restoration

### Rollback

```bash
cts corpus rollback rollback.json
```

### Evaluate impact

Compare a baseline corpus (before tuning) with a new corpus
(after tuning) to prove the changes helped:

```bash
cts corpus evaluate baseline.jsonl after.jsonl --format markdown
```

KPIs tracked:
- `confidence_final_mean` (higher = better)
- `confidence_delta_mean` (higher = better)
- `truncation_rate` (lower = better)
- `autopilot_low_lift_rate` (lower = better)
- `bundle_bytes_p90` (lower = better)
- `should_autopilot_count` (lower = better)

Verdicts: `improved`, `regressed`, `mixed`, `unchanged`, `no_data`

## CI integration

The `corpus-report` workflow runs manually and supports the full
tuning pipeline. It ingests sidecar artifacts, generates reports,
and optionally produces tuning recommendations and evaluations.

```bash
# Basic report
gh workflow run corpus-report.yml

# With tuning recommendations
gh workflow run corpus-report.yml \
  -f emit_tuning=true

# With evaluation against a baseline
gh workflow run corpus-report.yml \
  -f emit_tuning=true \
  -f evaluate_baseline=baseline.jsonl
```

The workflow uploads:
- `corpus.jsonl` — raw corpus for further analysis
- `report.md` — formatted report ready for wiki/PR
- `tuning.json` — machine-readable recommendations
- `patch-plan.json` — concrete config edits
- `tuning.diff` — unified diff preview
- `evaluation.json` / `evaluation.md` — before/after comparison
