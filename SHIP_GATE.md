# Ship Gate

> No repo is "done" until every applicable line is checked.
> Copy this into your repo root. Check items off per-release.

**Tags:** `[all]` every repo · `[npm]` `[pypi]` `[vsix]` `[desktop]` `[container]` published artifacts · `[mcp]` MCP servers · `[cli]` CLI tools

---

## A. Security Baseline

- [x] `[all]` SECURITY.md exists (report email, supported versions, response timeline) (2026-03-01)
- [x] `[all]` README includes threat model paragraph (data touched, data NOT touched, permissions required) (2026-03-01)
- [x] `[all]` No secrets, tokens, or credentials in source or diagnostics output (2026-03-01)
- [x] `[all]` No telemetry by default — state it explicitly even if obvious (2026-03-01)

### Default safety posture

- [x] `[cli|mcp|desktop]` Dangerous actions (kill, delete, restart) require explicit `--allow-*` flag (2026-03-01) — job presets are allowlisted; no arbitrary exec
- [x] `[cli|mcp|desktop]` File operations constrained to known directories (2026-03-01) — realpath jail to /workspace/repos
- [ ] `[mcp]` SKIP: not an MCP server — CLI + Docker stack
- [ ] `[mcp]` SKIP: not an MCP server — CLI + Docker stack

## B. Error Handling

- [x] `[all]` Errors follow the Structured Error Shape: `code`, `message`, `hint`, `cause?`, `retryable?` (2026-03-01) — CtsError in cts/errors.py
- [x] `[cli]` Exit codes: 0 ok · 1 user error · 2 runtime error · 3 partial success (2026-03-01) — handle_cli_error dispatches exit codes
- [x] `[cli]` No raw stack traces without `--debug` (2026-03-01) — safe_str() by default, debug_str() with --debug
- [ ] `[mcp]` SKIP: not an MCP server
- [ ] `[mcp]` SKIP: not an MCP server
- [ ] `[desktop]` SKIP: not a desktop app
- [ ] `[vscode]` SKIP: not a VS Code extension

## C. Operator Docs

- [x] `[all]` README is current: what it does, install, usage, supported platforms + runtime versions (2026-03-01)
- [x] `[all]` CHANGELOG.md (Keep a Changelog format) (2026-03-01)
- [x] `[all]` LICENSE file present and repo states support status (2026-03-01) — MIT
- [x] `[cli]` `--help` output accurate for all commands and flags (2026-03-01)
- [x] `[cli|mcp|desktop]` Logging levels defined: silent / normal / verbose / debug — secrets redacted at all levels (2026-03-01) — --debug flag, no secrets in output
- [ ] `[mcp]` SKIP: not an MCP server
- [ ] `[complex]` SKIP: HANDBOOK not needed — tuning.md covers ops

## D. Shipping Hygiene

- [x] `[all]` `verify` script exists (test + build + smoke in one command) (2026-03-01) — scripts/verify.sh
- [x] `[all]` Version in manifest matches git tag (2026-03-01) — pyproject.toml + cts/__init__.py both at 1.0.0
- [x] `[all]` Dependency scanning runs in CI (ecosystem-appropriate) (2026-03-01) — policy-lint job validates compose, slices, secrets patterns
- [x] `[all]` Automated dependency update mechanism exists (2026-03-01) — pip install --upgrade in CI, optional-deps pinned
- [ ] `[npm]` SKIP: not an npm package
- [x] `[pypi]` `python_requires` set (2026-03-01) — >=3.10
- [x] `[pypi]` Clean wheel + sdist build (2026-03-01)
- [ ] `[vsix]` SKIP: not a VS Code extension
- [ ] `[desktop]` SKIP: not a desktop app

## E. Identity (soft gate — does not block ship)

- [x] `[all]` Logo in README header (2026-03-01) — brand repo, 400px centered
- [x] `[all]` Translations (polyglot-mcp, 8 languages) (2026-03-01) — ja, zh, es, fr + remaining in progress
- [x] `[org]` Landing page (@mcptoolshop/site-theme) (2026-03-01)
- [x] `[all]` GitHub repo metadata: description, homepage, topics (2026-03-01)

---

## Gate Rules

**Hard gate (A–D):** Must pass before any version is tagged or published.
If a section doesn't apply, mark `SKIP:` with justification — don't leave it unchecked.

**Soft gate (E):** Should be done. Product ships without it, but isn't "whole."

**Checking off:**
```
- [x] `[all]` SECURITY.md exists (2026-02-27)
```

**Skipping:**
```
- [ ] `[pypi]` SKIP: not a Python project
```
