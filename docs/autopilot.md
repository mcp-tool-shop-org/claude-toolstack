# Autopilot: Bounded Evidence Refinement

When a first-pass evidence bundle has low confidence, autopilot automatically plans and executes refinement passes to improve coverage.

## Usage

```bash
# Up to 2 refinement passes, 30s budget
cts search "handleAuth" --repo org/repo --format sidecar --autopilot 2

# Custom time and slice budgets
cts search "handleAuth" --repo org/repo --format sidecar \
  --autopilot 3 \
  --autopilot-max-seconds 15 \
  --autopilot-max-extra-slices 3
```

## How It Works

1. **Initial search** produces an evidence bundle
2. **Confidence model** scores the bundle (0..1) across 6 signals
3. If score < 0.6 (insufficient), **planner** selects 1-2 refinement actions
4. Actions adjust search parameters and produce a new bundle
5. Repeat until confidence is sufficient or budget is exhausted

## Confidence Signals

| Signal | Weight | Description |
|--------|--------|-------------|
| `top_score_weight` | 0..0.3 | Quality of the best-ranked match |
| `definition_found` | 0 or 0.2 | Whether a probable definition was found |
| `source_diversity` | 0..0.2 | Number of distinct files in results |
| `slice_coverage` | 0..0.15 | Whether context slices were fetched |
| `low_match_penalty` | -0.15..0 | Penalty for too few matches |
| `mode_bonus` | 0 or 0.15 | Mode-specific bonus (trace found, symbols found) |

Threshold: **0.6** — bundles scoring at or above this are considered sufficient.

## Refinement Actions

| Action | Priority | Trigger | Effect |
|--------|----------|---------|--------|
| `widen_search` | 1 | < 5 sources, max_matches <= 50 | Double max_matches (cap 200) |
| `add_slices` | 2 | Low slice coverage | Add 5 more evidence files (cap 15) |
| `try_symbol` | 3 | No definition found, symbol-like query | Trigger ctags lookup |
| `broaden_glob` | 4 | Pass >= 2, < 3 sources, globs present | Remove path glob restrictions |

At most 2 actions per pass to keep execution bounded.

## CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--autopilot N` | 0 (off) | Max refinement passes |
| `--autopilot-max-seconds` | 30 | Wall-clock budget in seconds |
| `--autopilot-max-extra-slices` | 5 | Extra slices per refinement pass |

## Sidecar Pass Records

When autopilot runs, each pass is recorded in `sidecar.passes[]`:

```json
{
  "pass": 1,
  "actions": ["widen_search", "add_slices"],
  "confidence_before": 0.35,
  "reason": "Confidence 0.35 < 0.6 — weak signals: low_match_penalty",
  "elapsed_ms": 450.2,
  "status": "ok"
}
```

The final bundle in `sidecar.final` reflects the best result after all passes.

## Scope

Autopilot currently only refines `default` mode bundles. Error, symbol, and change modes use their initial bundle directly.
