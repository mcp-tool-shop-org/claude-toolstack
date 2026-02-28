"""Tests for cts.ctags — kind normalization, weights, and ctags_info building."""

from __future__ import annotations

import unittest

from cts.ctags import KIND_WEIGHTS, kind_weight, normalize_kind


class TestNormalizeKind(unittest.TestCase):
    def test_single_char_class(self):
        self.assertEqual(normalize_kind("c"), "class")

    def test_single_char_function(self):
        self.assertEqual(normalize_kind("f"), "function")

    def test_single_char_method(self):
        self.assertEqual(normalize_kind("m"), "method")

    def test_single_char_variable(self):
        self.assertEqual(normalize_kind("v"), "variable")

    def test_single_char_struct(self):
        self.assertEqual(normalize_kind("s"), "struct")

    def test_single_char_interface(self):
        self.assertEqual(normalize_kind("i"), "interface")

    def test_single_char_namespace(self):
        self.assertEqual(normalize_kind("n"), "namespace")

    def test_full_name_passthrough(self):
        self.assertEqual(normalize_kind("class"), "class")
        self.assertEqual(normalize_kind("function"), "function")

    def test_full_name_case_insensitive(self):
        self.assertEqual(normalize_kind("Class"), "class")
        self.assertEqual(normalize_kind("FUNCTION"), "function")

    def test_empty(self):
        self.assertEqual(normalize_kind(""), "")

    def test_unknown_char(self):
        self.assertEqual(normalize_kind("z"), "z")

    def test_unknown_full_name(self):
        self.assertEqual(normalize_kind("widget"), "widget")


class TestKindWeight(unittest.TestCase):
    def test_class_weight(self):
        self.assertEqual(kind_weight("class"), 0.6)

    def test_function_weight(self):
        self.assertEqual(kind_weight("function"), 0.5)

    def test_variable_weight(self):
        self.assertEqual(kind_weight("variable"), 0.2)

    def test_namespace_weight(self):
        self.assertEqual(kind_weight("namespace"), 0.3)

    def test_unknown_default(self):
        self.assertEqual(kind_weight("unknown_thing"), 0.1)

    def test_local_zero(self):
        self.assertEqual(kind_weight("local"), 0.0)

    def test_all_known_kinds_have_weights(self):
        for kind in KIND_WEIGHTS:
            self.assertIsInstance(kind_weight(kind), float)


class TestBuildCtagsInfo(unittest.TestCase):
    """Test _build_ctags_info helper from bundle module."""

    def test_basic(self):
        from cts.bundle import _build_ctags_info

        defs = [
            {"name": "MyClass", "file": "src/model.py", "kind": "c"},
            {"name": "MyClass", "file": "src/alt.py", "kind": "f"},
        ]
        info = _build_ctags_info(defs)
        self.assertEqual(info["def_files"], {"src/model.py", "src/alt.py"})
        self.assertEqual(info["best_kind"], "class")
        self.assertEqual(info["kind_weight"], 0.6)

    def test_empty_defs(self):
        from cts.bundle import _build_ctags_info

        info = _build_ctags_info([])
        self.assertEqual(info["def_files"], set())
        self.assertEqual(info["kind_weight"], 0.0)
        self.assertEqual(info["best_kind"], "")

    def test_function_only(self):
        from cts.bundle import _build_ctags_info

        defs = [{"name": "handler", "file": "src/api.py", "kind": "f"}]
        info = _build_ctags_info(defs)
        self.assertEqual(info["best_kind"], "function")
        self.assertEqual(info["kind_weight"], 0.5)

    def test_missing_kind(self):
        from cts.bundle import _build_ctags_info

        defs = [{"name": "x", "file": "a.py", "kind": None}]
        info = _build_ctags_info(defs)
        self.assertEqual(info["best_kind"], "")
        self.assertEqual(info["kind_weight"], 0.0)


if __name__ == "__main__":
    unittest.main()
