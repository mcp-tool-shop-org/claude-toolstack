# Sidecar JSON Schema

The sidecar format wraps evidence bundles with stable metadata for downstream consumers (editors, CI pipelines, other tools).

## Schema Version

`bundle_schema_version: 1` — bumped only on breaking changes. Non-breaking additions (new keys) do not bump the version.

Consumers should check this field and reject payloads with unsupported versions.

## Usage

```bash
# Write sidecar JSON to a file
cts search "login" --repo org/repo --format claude --emit bundle.json

# Print sidecar JSON to stdout
cts search "login" --repo org/repo --format sidecar

# Sidecar with debug telemetry included
cts search "login" --repo org/repo --format sidecar --debug-bundle
```

## Top-Level Structure

```json
{
  "bundle_schema_version": 1,
  "created_at": 1700000000.0,
  "tool": {
    "name": "cts",
    "cli_version": "0.2.0",
    "gateway_version": "1.5.0"
  },
  "request_id": "uuid-here",
  "repo": "org/repo",
  "mode": "default",
  "query": "login",
  "inputs": {
    "query": "login",
    "max": 50,
    "bundle_mode": "default",
    "evidence_files": 5,
    "context": 30
  },
  "debug": false,
  "passes": [],
  "final": { ... }
}
```

## Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `bundle_schema_version` | int | Schema version (always first key) |
| `created_at` | float | Unix timestamp |
| `tool.name` | string | Always `"cts"` |
| `tool.cli_version` | string | CLI version |
| `tool.gateway_version` | string? | Gateway version (omitted if not available) |
| `request_id` | string | Request identifier |
| `repo` | string | Repository identifier |
| `mode` | string | Bundle mode: default, error, symbol, change |
| `query` | string? | Original query (omitted if none) |
| `inputs` | object? | Original CLI parameters (omitted if none) |
| `debug` | bool | Whether debug telemetry is included |
| `passes` | array | Autopilot refinement pass records (empty if no autopilot) |
| `final` | object | The evidence bundle payload |

## --emit (Atomic File Output)

`--emit PATH` writes the sidecar JSON to a file using atomic write (write to temp, then rename). Safe for concurrent readers.

Can be combined with any format — when used with `--format claude`, both human-readable output and the sidecar file are produced.

## Stability Contract

- `bundle_schema_version` is always the first key
- `final` is always the last key
- New optional keys may be added without bumping the version
- Existing keys will not be removed or renamed within the same version
