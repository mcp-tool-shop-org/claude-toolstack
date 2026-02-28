# CI Artifacts Guide

## Overview

Claude Toolstack produces **sidecar artifacts** — structured JSON files that
capture everything an evidence search found: ranked sources, matches, slices,
and refinement passes. CI pipelines validate these artifacts, scan for secrets,
and upload them for review.

## Pipeline Architecture

```
cts search → sidecar JSON → validate → secrets-scan → upload artifact
                                                     ↓ (PRs only)
                                              PR comment summary
```

### Jobs

| Job | Trigger | Purpose |
|-----|---------|---------|
| `sidecar-artifacts` | push + PR | Generate, validate, scan, upload |
| PR comment | PR only | Post markdown summary to PR |

## Generating Artifacts

### From a live gateway

```bash
cts search "login" --repo org/repo --format sidecar --emit evidence.json
```

### From the test fixture (CI)

```bash
python scripts/gen_test_sidecar.py ci-artifact.json
```

The test fixture produces a valid sidecar envelope with synthetic data.
Use it to verify the pipeline works without a running gateway.

## Validation

```bash
cts sidecar validate artifact.json
```

Checks:
- All required envelope keys present (`bundle_schema_version`, `tool`, `final`, etc.)
- Correct types for each key
- Schema version matches current (`BUNDLE_SCHEMA_VERSION`)
- Valid mode (`default`, `error`, `symbol`, `change`)
- Tool structure has `name` and `cli_version`
- Final bundle has `version` and `mode`

Exit code 1 on any error. Warnings (unexpected top-level keys) print to
stderr but don't fail the build.

## Secrets Scanning

```bash
cts sidecar secrets-scan artifact.json --fail --report
```

Scans the `final` bundle, `passes`, and `_debug` sections for 14 patterns:

| Pattern | Example Match |
|---------|---------------|
| GitHub Token | `ghp_...`, `gho_...` |
| GitHub Fine-Grained | `github_pat_...` |
| AWS Access Key | `AKIA...` |
| AWS Secret Key | `aws_secret_access_key = ...` |
| Slack Token | `xoxb-...` |
| Slack Webhook | `https://hooks.slack.com/services/...` |
| Private Key Block | `-----BEGIN RSA PRIVATE KEY-----` |
| Generic API Key | `api_key = ...` |
| Generic Secret | `secret = ...`, `password = ...` |
| JWT Token | `eyJ...` |
| GCP Service Account | `"type": "service_account"` |
| Anthropic API Key | `sk-ant-...` |
| OpenAI API Key | `sk-...` |
| npm Token | `npm_...` |

Flags:
- `--fail`: exit code 1 if any findings (use in CI gates)
- `--report`: print each finding with redacted preview to stderr

### Ignoring false positives

Pass `ignore_patterns` (programmatic API) to skip repo-specific patterns:

```python
from cts.sidecar import secrets_scan

findings = secrets_scan(data, ignore_patterns=[r"AKIA_TEST_EXAMPLE"])
```

## PR Comments

The `pr_comment_sidecar.py` script posts a markdown summary to the PR:

```bash
python scripts/pr_comment_sidecar.py artifact.json --pr 42
```

Features:
- Auto-detects PR number from `GITHUB_EVENT_PATH` or `GITHUB_REF`
- Uses a hidden HTML marker (`<!-- cts-sidecar-summary -->`) to find and
  update existing comments instead of creating duplicates
- Requires `gh` CLI authenticated with `GITHUB_TOKEN`

## Artifact Retention

| Context | Retention | Notes |
|---------|-----------|-------|
| CI artifacts | 30 days | Configurable via `retention-days` in workflow |
| PR comments | Permanent | Updated on each push, not duplicated |
| Local `--emit` | User-managed | Written to the path you specify |

## Rollout Checklist

1. Merge the CI workflow changes
2. Open a test PR to verify:
   - `sidecar-artifacts` job runs and uploads
   - PR comment appears with markdown summary
   - Secrets scan passes (no false positives)
3. For production use, replace `gen_test_sidecar.py` with actual
   `cts search --format sidecar --emit` calls against your gateway

## Policy Integration

The `policy-lint` CI job validates infrastructure compliance. To add
sidecar-specific policy checks, extend the job with:

- Schema version pinning (reject bundles from incompatible versions)
- Mandatory secrets scan on all uploaded artifacts
- Size limits on artifact uploads

These checks ensure that evidence bundles meet governance requirements
before they're stored or shared.
