# Experiment Registry

Durable, file-based experiment history that accumulates results
over time and powers trend dashboards — no database required.

## On-disk layout

```
experiments/
  <exp-id>/
    experiment.json      # Immutable experiment envelope
    meta.json            # SHA-256 hashes, registry version, archive timestamp
    variants/
      tuning_A.json      # Variant A tuning config
      tuning_B.json      # Variant B tuning config
      patch_A.diff        # Variant A patch preview
      patch_B.diff        # Variant B patch preview
    results/
      run_<timestamp>/
        result.json       # Evaluation result (verdict, winner, per-variant KPIs)
      run_<timestamp>/
        result.json       # Subsequent re-evaluations append, never overwrite
```

### Allowed file types

Only `.json`, `.md`, `.diff`, and `.jsonl` files are stored in the registry.
No source code, binaries, or slice payloads.

### Immutability contract

- `experiment.json` is written once at archive time and never modified
- Results are append-only: each evaluation creates a new `run_<ts>/` directory
- `meta.json` tracks SHA-256 hashes of all archived artifacts for integrity checks
- Re-archiving the same result (identical content hash) is idempotent — no duplicate runs

## Lifecycle

```
Propose experiment
       │
       ▼
  Run variants (collect corpus data)
       │
       ▼
  Evaluate results
       │
       ▼
  Archive to registry ─────► experiments/<id>/
       │
       ▼
  Accumulate over time
       │
       ▼
  Generate trend dashboard
```

### 1. Create and propose

```bash
cts corpus experiment init --id my-exp --out experiment.json
cts corpus experiment propose \
  --corpus tuning.json \
  --repos-yaml repos.yaml \
  --experiment experiment.json \
  --out-dir ./working/
```

### 2. Evaluate

```bash
cts corpus experiment evaluate \
  --corpus corpus.jsonl \
  --experiment experiment.json \
  --format json --out result.json
```

### 3. Archive

```bash
cts corpus experiment archive \
  --experiment experiment.json \
  --result result.json \
  --root experiments/
```

This copies the experiment envelope, result, and variant artifacts into the
registry under `experiments/<id>/`.

### 4. Query the registry

```bash
# List all experiments
cts corpus experiment list --root experiments/

# Filter by verdict
cts corpus experiment list --root experiments/ --verdict winner

# Filter by time window
cts corpus experiment list --root experiments/ --since 30

# Search by keyword
cts corpus experiment list --root experiments/ --contains "aggressive"

# Show full details for one experiment
cts corpus experiment show EXP123 --root experiments/
```

### 5. Generate trend dashboard

```bash
# Text dashboard (terminal)
cts corpus experiment trend --root experiments/

# Markdown dashboard (reports, CI artifacts)
cts corpus experiment trend --root experiments/ \
  --format markdown --out trend.md

# JSON dashboard (programmatic)
cts corpus experiment trend --root experiments/ \
  --format json --out trend.json

# Filter: last 30 days only
cts corpus experiment trend --root experiments/ --window 30

# Filter: specific KPI
cts corpus experiment trend --root experiments/ \
  --primary-kpi confidence_final_mean
```

## CLI cheat-sheet

| Command | Description |
|---------|-------------|
| `experiment list` | List all experiments (with optional filters) |
| `experiment show <ID>` | Show full details for one experiment |
| `experiment archive` | Archive experiment + result to registry |
| `experiment trend` | Generate trend dashboard from registry |

### List filters

| Flag | Description | Example |
|------|-------------|---------|
| `--root` | Registry root directory | `--root experiments/` |
| `--format` | Output format (text/json/markdown) | `--format markdown` |
| `--verdict` | Filter by verdict | `--verdict winner` |
| `--winner` | Filter by winning variant | `--winner B` |
| `--since` | Only experiments from last N days | `--since 30` |
| `--primary-kpi` | Filter by primary KPI | `--primary-kpi truncation_rate` |
| `--contains` | Free-text search in description/hypothesis | `--contains "aggressive"` |

### Trend flags

| Flag | Description | Example |
|------|-------------|---------|
| `--root` | Registry root directory | `--root experiments/` |
| `--format` | Output format (text/json/markdown) | `--format markdown` |
| `--window` | Only last N days | `--window 30` |
| `--primary-kpi` | Filter by primary KPI | `--primary-kpi confidence_final_mean` |
| `--group-by` | Grouping strategy (future) | `--group-by strategy` |
| `--out` | Write to file | `--out trend.md` |

## Dashboard sections

The markdown dashboard uses stable headings for automated parsing:

```markdown
# Experiment Trend Dashboard
## Summary
## Win rates
## KPI trends
## Common winning changes
## Regressions / constraint failures
## Recent experiments
```

### Win rates

Shows win rates grouped by tuning strategy (conservative, aggressive, focused).
Helps identify which strategies tend to produce winners.

### KPI trends

Rolling average of the primary KPI value for winning variants over time.
Window size defaults to 5 experiments.

### Common winning changes

The most frequently modified tuning targets in winning variants.
Helps identify which knobs are most impactful.

### Regressions

Counts experiments where all variants violated constraints, plus
the overall verdict distribution.

## Integrity

`meta.json` contains SHA-256 hashes for each archived artifact:

```json
{
  "registry_version": 1,
  "archived_at": 1700000000.0,
  "hashes": {
    "experiment": "sha256:abc123...",
    "result": "sha256:def456...",
    "repos_yaml": "sha256:789abc..."
  }
}
```

Use these hashes to verify that archived files have not been
tampered with after archival.
