---
title: Handbook
description: The complete operational reference for Claude ToolStack.
sidebar:
  order: 0
---

Welcome to the Claude ToolStack Handbook — the complete operational reference for deploying and running bounded code intelligence on 64-GB Linux workstations.

## What is Claude ToolStack?

Claude ToolStack is a Linux-first execution environment that keeps Claude Code productive on large, multi-language repositories without thrashing your workstation. Instead of loading an entire codebase into context, ToolStack maintains durable indexes in resource-governed containers and streams only the smallest necessary evidence back through a thin HTTP gateway.

The architecture has three layers:

- **Gateway** — A FastAPI server (7 endpoints, port 8088) that accepts bounded queries and returns bounded evidence with a 512 KB hard cap.
- **Tool Farm** — Long-running Docker containers (ctags indexer, build runner, optional language servers) managed through an exec-only model.
- **Resource Governance** — systemd cgroup v2 slices that enforce per-category memory budgets, so your SSH session stays responsive during indexing and builds.

## Who is this for?

ToolStack is designed for developers and teams who:

- Run Claude Code against repositories with 50k+ lines of code
- Work on 64-GB Linux workstations (Ubuntu 22.04, Fedora 38+)
- Need their machine to remain responsive while Claude indexes, searches, and builds
- Want defense-in-depth security without managing complex infrastructure

## Handbook sections

- **[Getting Started](/claude-toolstack/handbook/getting-started/)** — Bootstrap your host, configure the stack, clone repos, start services, and verify everything works.
- **[Usage](/claude-toolstack/handbook/usage/)** — Learn the `cts` CLI, evidence bundles, semantic search, and curl examples for direct gateway access.
- **[Reference](/claude-toolstack/handbook/reference/)** — Gateway API endpoints, resource governance details, the security and threat model, environment variables, and directory structure.

## Quick links

- [GitHub Repository](https://github.com/mcp-tool-shop-org/claude-toolstack)
- [PyPI Package](https://pypi.org/project/claude-toolstack-cli/)
- [Landing Page](https://mcp-tool-shop-org.github.io/claude-toolstack/)
