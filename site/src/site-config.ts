import type { SiteConfig } from '@mcptoolshop/site-theme';

export const config: SiteConfig = {
  title: 'Claude ToolStack',
  description: 'Bounded code intelligence for 64-GB Linux hosts — cgroup v2 slices, Compose tool farm, FastAPI gateway, no thrash.',
  logoBadge: 'CT',
  brandName: 'Claude ToolStack',
  repoUrl: 'https://github.com/mcp-tool-shop-org/claude-toolstack',
  footerText: 'MIT Licensed — built by <a href="https://mcp-tool-shop.github.io/" style="color:var(--color-muted);text-decoration:underline">MCP Tool Shop</a>',

  hero: {
    badge: 'v1.0.1',
    headline: 'Bounded code intelligence',
    headlineAccent: 'without the thrash.',
    description: 'Keep Claude Code productive on large, multi-language repositories. Durable indexes in resource-governed containers — stream only the smallest necessary evidence.',
    primaryCta: { href: '#quick-start', label: 'Get started' },
    secondaryCta: { href: '#features', label: 'How it works' },
    previews: [
      { label: 'Bootstrap', code: 'sudo ./scripts/bootstrap.sh' },
      { label: 'Deploy', code: 'docker compose up -d --build' },
      { label: 'Check', code: 'cts doctor && cts perf' },
    ],
  },

  sections: [
    {
      kind: 'features',
      id: 'features',
      title: 'Why ToolStack',
      subtitle: 'Everything Claude Code needs to work on large repos without killing your machine.',
      features: [
        { title: 'No Thrash', desc: 'systemd cgroup v2 slices enforce MemoryHigh/Max per service group. Your SSH session stays responsive during indexing and builds.' },
        { title: 'Exec-Only Docker', desc: 'Gateway delegates to long-running tool containers via docker exec. Socket proxy blocks images, volumes, networks, and system endpoints.' },
        { title: 'Bounded Evidence', desc: '512 KB hard cap on all responses. Path-jailed search, ranked context slices, and 4 bundle modes deliver precisely what Claude needs.' },
      ],
    },
    {
      kind: 'data-table',
      id: 'slices',
      title: 'Resource Governance',
      subtitle: 'systemd slices keep every service category within budget.',
      columns: ['Slice', 'MemoryHigh', 'MemoryMax', 'Purpose'],
      rows: [
        ['claude-gw', '2 GB', '4 GB', 'Gateway + socket proxy'],
        ['claude-index', '6 GB', '10 GB', 'Indexers + search'],
        ['claude-lsp', '8 GB', '16 GB', 'Language servers'],
        ['claude-build', '10 GB', '18 GB', 'Build/test runners'],
        ['claude-vector', '8 GB', '16 GB', 'Vector DB (optional)'],
      ],
    },
    {
      kind: 'code-cards',
      id: 'quick-start',
      title: 'Quick Start',
      cards: [
        { title: '1. Bootstrap host', code: '# Install slices, zram, sysctl, Docker config\nsudo ./scripts/bootstrap.sh' },
        { title: '2. Configure', code: 'cp .env.example .env\n# Set API_KEY, ALLOWED_REPOS' },
        { title: '3. Start stack', code: 'docker compose up -d --build\n\n# Verify\ncts doctor\ncts status' },
        { title: '4. Search', code: '# Text search with guardrails\ncts search "PaymentService" --repo myorg/myrepo\n\n# Evidence bundle for Claude\ncts search "auth" --repo myorg/myrepo --format claude' },
      ],
    },
    {
      kind: 'api',
      id: 'api',
      title: 'Gateway API',
      subtitle: 'All endpoints require x-api-key header. Gateway binds to 127.0.0.1:8088 only.',
      apis: [
        { signature: 'GET /v1/status', description: 'Health + config' },
        { signature: 'POST /v1/search/rg', description: 'Ripgrep with guardrails' },
        { signature: 'POST /v1/file/slice', description: 'Fetch file range (max 800 lines)' },
        { signature: 'POST /v1/index/ctags', description: 'Build ctags index (async)' },
        { signature: 'POST /v1/symbol/ctags', description: 'Query symbol definitions' },
        { signature: 'POST /v1/run/job', description: 'Run allowlisted test/build/lint' },
        { signature: 'GET /v1/metrics', description: 'Prometheus-format counters' },
      ],
    },
    {
      kind: 'features',
      id: 'security',
      title: 'Security Layers',
      subtitle: 'Defense in depth — no single point of failure.',
      features: [
        { title: 'Path Jail', desc: 'realpath + null byte rejection. No repo access outside /workspace/repos. Allowlist/denylist with glob support.' },
        { title: 'Socket Proxy', desc: 'Docker socket never exposed. Tecnativa proxy allows only CONTAINERS + EXEC — 14 higher-risk endpoints explicitly denied.' },
        { title: 'Rate + Audit', desc: 'Token bucket rate limiting per key+ip. JSONL audit log with hashed API keys, rotated automatically.' },
      ],
    },
  ],
};
