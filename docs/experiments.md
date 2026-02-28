# A/B Tuning Experiments

Run controlled experiments to compare tuning strategies before
committing to production changes.

## Overview

The experiment system builds on top of the tuning pipeline
(see [corpus.md](corpus.md)) by adding:

- **Experiment envelope** — versioned schema that tracks variants,
  assignment modes, decision rules, and audit info
- **Variant generator** — produces per-variant tuning configs using
  conservative, aggressive, or focused strategies
- **Assignment modes** — maps corpus records to variants by repo
  partition, time window, or manual tagging
- **Decision engine** — picks a winner based on primary KPI,
  constraint checks, noise thresholds, and tie-breakers

## Quick start

```bash
# 1. Generate tuning recommendations from corpus
cts corpus report corpus.jsonl \
  --format json \
  --emit-tuning tuning.json

# 2. Create experiment envelope
cts corpus experiment init \
  --id my-experiment \
  --description "Compare conservative vs aggressive tuning" \
  --hypothesis "Aggressive tuning improves confidence without worsening truncation" \
  --constraint "truncation_rate<=+0.02" \
  --out experiment.json

# 3. Generate per-variant artifacts
cts corpus experiment propose \
  --corpus tuning.json \
  --repos-yaml repos.yaml \
  --experiment experiment.json \
  --out-dir experiments/my-experiment/

# 4. (Run both variants, collect corpus data)

# 5. Evaluate results
cts corpus experiment evaluate \
  --corpus combined_corpus.jsonl \
  --experiment experiments/my-experiment/experiment.json \
  --format markdown
```

## Concepts

### Experiment envelope

A versioned JSON document that defines an experiment:

```json
{
  "experiment_schema_version": 1,
  "id": "exp-abc123",
  "created_at": 1700000000.0,
  "description": "...",
  "hypothesis": "...",
  "kpis": [
    "confidence_final_mean",
    "confidence_delta_mean",
    "truncation_rate",
    "autopilot_low_lift_rate",
    "bundle_bytes_p90",
    "should_autopilot_count"
  ],
  "variants": [
    {"name": "A", "tuning_ref": "tuning_A.json", "patch_ref": "patch_A.diff"},
    {"name": "B", "tuning_ref": "tuning_B.json", "patch_ref": "patch_B.diff"}
  ],
  "assignment": {"mode": "repo_partition", "details": {"A": [...], "B": [...]}},
  "decision_rule": {
    "primary_kpi": "confidence_final_mean",
    "constraints": [{"kpi": "truncation_rate", "operator": "<=", "threshold": 0.02}],
    "tie_breakers": ["bundle_bytes_p90"]
  }
}
```

### Variant strategies

When proposing an experiment, each variant gets a different tuning
strategy applied to the base recommendations:

| Strategy | Delta mult | Cap mult | Threshold adj | Description |
|----------|-----------|---------|--------------|-------------|
| `conservative` | 0.6x | 0.8x | +0.05 | Smaller changes, tighter caps |
| `aggressive` | 1.2x | 1.3x | -0.05 | Larger changes, looser caps |
| `focused` | 1.0x | 1.0x | 0.0 | Only high-evidence recs (count >= 5) |

Default mapping for 2 variants: A = conservative, B = aggressive.
For 3+ variants: first = conservative, middle = focused, last = aggressive.

Override with `--strategy`:

```bash
cts corpus experiment propose \
  --corpus tuning.json \
  --repos-yaml repos.yaml \
  --strategy "A=focused" \
  --strategy "B=aggressive"
```

### Assignment modes

How corpus records are assigned to variants for evaluation:

| Mode | Description | Details format |
|------|-------------|---------------|
| `manual` | Records have a `variant` field | — |
| `repo_partition` | Map repos to variants | `{"A": ["org/r1"], "B": ["org/r2"]}` |
| `time_window` | Map timestamp ranges | `{"A": [start, end], "B": [start, end]}` |

### Decision rules

The decision engine picks a winner in this order:

1. **Constraint check** — eliminate variants that violate constraints
   (unless all variants violate the same constraint)
2. **Primary KPI** — compare the designated KPI across surviving
   variants, accounting for noise thresholds
3. **Tie-breakers** — if primary KPI is within noise, fall through
   to tie-breaker KPIs in order
4. **Tie** — if still unresolved, declare a tie

Noise thresholds prevent false positives from random variation:

| KPI | Noise threshold |
|-----|----------------|
| `confidence_final_mean` | 0.02 |
| `confidence_delta_mean` | 0.02 |
| `truncation_rate` | 0.02 |
| `autopilot_low_lift_rate` | 0.05 |
| `bundle_bytes_p90` | 5000 |
| `should_autopilot_count` | 1 |

### Verdicts

| Verdict | Meaning |
|---------|---------|
| `winner` | Clear winner on primary KPI (or tie-breaker) |
| `winner_by_elimination` | Only one variant passed constraints |
| `tie` | All variants within noise threshold |
| `no_data` | One or more variants have no records |
| `insufficient_variants` | Fewer than 2 variants |
| `*_all_constraints_violated` | All variants violated constraints |

## Workflows

### Local: time-window A/B

1. Apply variant A's config for week 1, collect corpus
2. Apply variant B's config for week 2, collect corpus
3. Combine both corpora into one JSONL with tagged timestamps
4. Set experiment assignment mode to `time_window`
5. Run evaluation

```bash
# Week 1: apply conservative tuning
cts corpus apply experiments/exp1/tuning_A.json \
  --repos-yaml repos.yaml

# (... collect corpus_week1.jsonl ...)

# Week 2: apply aggressive tuning
cts corpus apply experiments/exp1/tuning_B.json \
  --repos-yaml repos.yaml

# (... collect corpus_week2.jsonl ...)

# Combine and evaluate
cat corpus_week1.jsonl corpus_week2.jsonl > combined.jsonl
cts corpus experiment evaluate \
  --corpus combined.jsonl \
  --experiment experiments/exp1/experiment.json \
  --format markdown
```

### CI: repo-partition A/B

1. Partition repos into two groups in the experiment config
2. Apply variant A's config to group 1, variant B's to group 2
3. Run CI on both groups, collect corpus
4. Evaluate with `repo_partition` assignment mode

```bash
# 1. Create experiment with repo partition
cts corpus experiment init \
  --id repo-ab-test \
  --out experiment.json

# 2. Propose variants
cts corpus experiment propose \
  --corpus tuning.json \
  --repos-yaml repos.yaml \
  --out-dir experiments/repo-ab-test/

# 3. (edit experiment.json to set repo_partition details)

# 4. Evaluate after collecting data
cts corpus experiment evaluate \
  --corpus corpus.jsonl \
  --experiment experiments/repo-ab-test/experiment.json \
  --format markdown \
  --out evaluation.md
```

## CI integration

The `corpus-report` workflow supports experiments via dispatch inputs:

```bash
# Run experiment pipeline
gh workflow run corpus-report.yml \
  -f emit_tuning=true \
  -f run_experiment=true \
  -f experiment_id=my-exp

# With evaluation baseline
gh workflow run corpus-report.yml \
  -f emit_tuning=true \
  -f run_experiment=true \
  -f evaluate_baseline=baseline.jsonl
```

The workflow produces these experiment artifacts:

- `experiment.json` — experiment envelope
- `experiment/tuning_A.json` — variant A tuning
- `experiment/tuning_B.json` — variant B tuning
- `experiment/patch_A.diff` — variant A patch preview
- `experiment/patch_B.diff` — variant B patch preview

## Output formats

All experiment commands support `--format text|json|markdown`:

```bash
# Text (terminal)
cts corpus experiment evaluate --corpus c.jsonl --experiment e.json

# JSON (programmatic)
cts corpus experiment evaluate --corpus c.jsonl --experiment e.json \
  --format json --out result.json

# Markdown (reports)
cts corpus experiment evaluate --corpus c.jsonl --experiment e.json \
  --format markdown --out result.md
```

## Validation

The experiment schema is validated on creation:

- Experiment ID must be non-empty
- Schema version must match current (1)
- At least 2 variants required
- No duplicate variant names
- Primary KPI must be specified in decision rule

```bash
# Validation errors are printed to stderr
cts corpus experiment init --id "" --out test.json
# Error: Missing experiment id
```
