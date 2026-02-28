"""Structural heuristics: definition/export detection from snippet text.

Regex-based, no AST. Conservative: false positives are worse than
false negatives because they distort ranking.

Language detection uses file extension; falls back to generic rules.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Language detection from file extension
# ---------------------------------------------------------------------------

_EXT_TO_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
}

# Language families for rule selection
_LANG_FAMILY = {
    "python": "python",
    "javascript": "js_ts",
    "typescript": "js_ts",
    "go": "go",
    "java": "java_cs",
    "csharp": "java_cs",
    "kotlin": "java_cs",
    "scala": "java_cs",
    "rust": "rust",
    "c": "c_cpp",
    "cpp": "c_cpp",
    "ruby": "ruby",
    "swift": "swift",
}


def _detect_lang(path: str) -> str:
    """Detect language family from file path extension."""
    path_lower = path.lower().replace("\\", "/")
    for ext, lang in _EXT_TO_LANG.items():
        if path_lower.endswith(ext):
            return _LANG_FAMILY.get(lang, "generic")
    return "generic"


# ---------------------------------------------------------------------------
# Per-family rule sets
# ---------------------------------------------------------------------------

# Each rule: (pattern_template, is_def, is_export, confidence, rule_name)
# {SYM} is replaced with the escaped symbol name at query time.

_RULES_PYTHON = [
    (r"^(?:async\s+)?def\s+{SYM}\s*\(", True, False, 0.95, "py_def"),
    (r"^class\s+{SYM}\b", True, False, 0.95, "py_class"),
    (r"^{SYM}\s*=", True, False, 0.6, "py_assign"),
]

_RULES_JS_TS = [
    (
        r"\bexport\s+(?:default\s+)?"
        r"(?:class|function|const|let|var|type|interface|enum)"
        r"\s+{SYM}\b",
        True,
        True,
        0.95,
        "jsts_export_decl",
    ),
    (
        r"\bexport\s*\{[^}]*\b{SYM}\b",
        False,
        True,
        0.85,
        "jsts_named_export",
    ),
    (r"\b(?:class|function)\s+{SYM}\b", True, False, 0.9, "jsts_decl"),
    (r"\b(?:const|let|var)\s+{SYM}\s*=", True, False, 0.7, "jsts_assign"),
    (r"\binterface\s+{SYM}\b", True, False, 0.9, "jsts_interface"),
    (r"\btype\s+{SYM}\b\s*=", True, False, 0.9, "jsts_type_alias"),
]

_RULES_GO = [
    (r"^func\s+(?:\(\s*\w+\s+\*?\w+\s*\)\s*)?{SYM}\s*\(", True, False, 0.95, "go_func"),
    (r"^type\s+{SYM}\s+(?:struct|interface)\b", True, False, 0.95, "go_type"),
    (r"^type\s+{SYM}\s+", True, False, 0.8, "go_type_alias"),
    (r"^var\s+{SYM}\b", True, False, 0.6, "go_var"),
]

_RULES_JAVA_CS = [
    (
        r"\b(?:class|interface|enum|record)\s+{SYM}\b",
        True,
        False,
        0.95,
        "javacs_type",
    ),
    (
        r"\b(?:public|private|protected|internal)\s+.*\b{SYM}\s*\(",
        True,
        False,
        0.7,
        "javacs_method",
    ),
]

_RULES_RUST = [
    (r"\b(?:pub\s+)?fn\s+{SYM}\b", True, False, 0.95, "rs_fn"),
    (r"\b(?:pub\s+)?struct\s+{SYM}\b", True, False, 0.95, "rs_struct"),
    (r"\b(?:pub\s+)?enum\s+{SYM}\b", True, False, 0.95, "rs_enum"),
    (r"\b(?:pub\s+)?trait\s+{SYM}\b", True, False, 0.95, "rs_trait"),
    (r"\bimpl\b.*\b{SYM}\b", True, False, 0.7, "rs_impl"),
    (r"\b(?:pub\s+)?type\s+{SYM}\b", True, False, 0.8, "rs_type_alias"),
]

_RULES_C_CPP = [
    (r"\b(?:class|struct)\s+{SYM}\b", True, False, 0.9, "c_type"),
    (r"\b\w[\w:*&\s]+\b{SYM}\s*\(", True, False, 0.6, "c_func_sig"),
    (r"^#define\s+{SYM}\b", True, False, 0.8, "c_macro"),
    (r"\btypedef\b.*\b{SYM}\s*;", True, False, 0.8, "c_typedef"),
]

_RULES_GENERIC = [
    (r"\b(?:class|struct|interface|enum)\s+{SYM}\b", True, False, 0.8, "gen_type"),
    (r"\b(?:def|func|function|fn)\s+{SYM}\s*\(", True, False, 0.8, "gen_func"),
    (r"\bexport\b.*\b{SYM}\b", False, True, 0.6, "gen_export"),
]

_FAMILY_RULES = {
    "python": _RULES_PYTHON,
    "js_ts": _RULES_JS_TS,
    "go": _RULES_GO,
    "java_cs": _RULES_JAVA_CS,
    "rust": _RULES_RUST,
    "c_cpp": _RULES_C_CPP,
    "generic": _RULES_GENERIC,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_snippet(
    path: str,
    symbol: str,
    text: str,
    lang_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify whether snippet text likely defines or exports a symbol.

    Args:
        path: File path (used for language detection if lang_hint is None).
        symbol: The symbol name to look for.
        text: Snippet text (typically a few lines around a match).
        lang_hint: Override language family (python, js_ts, go, etc.).

    Returns dict with:
        is_probable_definition: bool
        is_probable_export: bool
        def_conf: float (0..1)
        export_conf: float (0..1)
        matched_rule: str (rule name that fired, or "")
        lang_family: str
    """
    if not symbol or not text:
        return _empty_result(lang_hint or "generic")

    family = lang_hint or _detect_lang(path)
    rules = _FAMILY_RULES.get(family, _RULES_GENERIC)
    escaped = re.escape(symbol)

    best_def_conf = 0.0
    best_export_conf = 0.0
    best_rule = ""
    is_def = False
    is_export = False

    for template, rule_is_def, rule_is_export, conf, rule_name in rules:
        pattern = template.replace("{SYM}", escaped)
        try:
            if re.search(pattern, text, re.MULTILINE):
                if rule_is_def and conf > best_def_conf:
                    best_def_conf = conf
                    is_def = True
                    best_rule = rule_name
                if rule_is_export and conf > best_export_conf:
                    best_export_conf = conf
                    is_export = True
                    if not is_def:
                        best_rule = rule_name
        except re.error:
            continue

    return {
        "is_probable_definition": is_def,
        "is_probable_export": is_export,
        "def_conf": best_def_conf,
        "export_conf": best_export_conf,
        "matched_rule": best_rule,
        "lang_family": family,
    }


def _empty_result(family: str) -> Dict[str, Any]:
    return {
        "is_probable_definition": False,
        "is_probable_export": False,
        "def_conf": 0.0,
        "export_conf": 0.0,
        "matched_rule": "",
        "lang_family": family,
    }
