"""Lightweight ctags query helper with in-memory caching.

Wraps the gateway's /v1/symbol/ctags endpoint and provides query
helpers for ranking enrichment. Caches results per CLI invocation
to avoid redundant API calls.

e-ctags kind codes (single character):
  c = class, s = struct, i = interface, e = enum, u = union,
  f = function, m = method/member, p = prototype,
  v = variable, d = macro/define, t = typedef, n = namespace,
  g = enum value, l = local variable
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from cts import http

# Map e-ctags single-char kind codes to human-readable names
KIND_MAP = {
    "c": "class",
    "s": "struct",
    "i": "interface",
    "e": "enum",
    "u": "union",
    "f": "function",
    "m": "method",
    "p": "prototype",
    "v": "variable",
    "d": "macro",
    "t": "typedef",
    "n": "namespace",
    "g": "enum_value",
    "l": "local",
    "a": "alias",
    "M": "module",
    "P": "package",
}

# Ranking weights per kind category
KIND_WEIGHTS: Dict[str, float] = {
    "class": 0.6,
    "struct": 0.6,
    "interface": 0.6,
    "enum": 0.5,
    "function": 0.5,
    "method": 0.5,
    "module": 0.3,
    "package": 0.3,
    "namespace": 0.3,
    "typedef": 0.3,
    "alias": 0.3,
    "variable": 0.2,
    "macro": 0.2,
    "prototype": 0.1,
    "enum_value": 0.1,
    "local": 0.0,
}


class SymbolCache:
    """In-memory cache for ctags queries within a single CLI invocation."""

    def __init__(self, repo: str, request_id: Optional[str] = None) -> None:
        self.repo = repo
        self.request_id = request_id
        self._cache: Dict[str, List[Dict[str, Any]]] = {}

    def query(self, symbol: str) -> List[Dict[str, Any]]:
        """Query defs for a symbol, caching the result."""
        if symbol in self._cache:
            return self._cache[symbol]

        try:
            data = http.post(
                "/v1/symbol/ctags",
                {"repo": self.repo, "symbol": symbol},
                request_id=self.request_id,
            )
            defs = data.get("defs", [])
        except SystemExit:
            defs = []

        self._cache[symbol] = defs
        return defs

    def defs_in_file(self, path: str) -> List[Dict[str, Any]]:
        """Return all cached defs that are in a given file path."""
        results = []
        for defs in self._cache.values():
            for d in defs:
                if d.get("file", "") == path:
                    results.append(d)
        return results

    def kinds_for_symbol(self, symbol: str) -> Set[str]:
        """Return the set of kind names for a symbol."""
        defs = self.query(symbol)
        kinds = set()
        for d in defs:
            raw_kind = d.get("kind") or ""
            kind_name = normalize_kind(raw_kind)
            if kind_name:
                kinds.add(kind_name)
        return kinds

    def files_defining(self, symbol: str) -> Set[str]:
        """Return the set of file paths that define a symbol."""
        defs = self.query(symbol)
        return {d.get("file", "") for d in defs if d.get("file")}


def normalize_kind(raw: str) -> str:
    """Normalize a ctags kind code to a human-readable name.

    Handles both single-char codes ('c') and full names ('class').
    """
    if not raw:
        return ""
    # Single char code
    if len(raw) == 1:
        return KIND_MAP.get(raw, raw)
    # Already a full name — lowercase and return if known
    lower = raw.lower()
    if lower in KIND_WEIGHTS:
        return lower
    return lower


def kind_weight(kind_name: str) -> float:
    """Return the ranking weight for a normalized kind name."""
    return KIND_WEIGHTS.get(kind_name, 0.1)
