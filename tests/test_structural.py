"""Tests for cts.structural — definition/export detection heuristics."""

from __future__ import annotations

import unittest

from cts.structural import classify_snippet


class TestPythonRules(unittest.TestCase):
    def test_def_function(self):
        r = classify_snippet("auth.py", "login", "def login(user):")
        self.assertTrue(r["is_probable_definition"])
        self.assertGreater(r["def_conf"], 0.9)
        self.assertEqual(r["matched_rule"], "py_def")

    def test_async_def(self):
        r = classify_snippet("auth.py", "login", "async def login(user):")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "py_def")

    def test_class(self):
        r = classify_snippet("model.py", "User", "class User(Base):")
        self.assertTrue(r["is_probable_definition"])
        self.assertGreater(r["def_conf"], 0.9)
        self.assertEqual(r["matched_rule"], "py_class")

    def test_assignment(self):
        r = classify_snippet("config.py", "MAX_RETRIES", "MAX_RETRIES = 5")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "py_assign")
        self.assertLess(r["def_conf"], 0.9)  # lower confidence

    def test_usage_not_def(self):
        r = classify_snippet("handler.py", "login", "    result = login(user)")
        self.assertFalse(r["is_probable_definition"])

    def test_comment_not_def(self):
        r = classify_snippet("auth.py", "login", "# login is deprecated")
        self.assertFalse(r["is_probable_definition"])


class TestJsTsRules(unittest.TestCase):
    def test_export_class(self):
        r = classify_snippet("model.ts", "User", "export class User {")
        self.assertTrue(r["is_probable_definition"])
        self.assertTrue(r["is_probable_export"])
        self.assertEqual(r["matched_rule"], "jsts_export_decl")

    def test_export_default_function(self):
        r = classify_snippet("api.js", "handler", "export default function handler() {")
        self.assertTrue(r["is_probable_definition"])
        self.assertTrue(r["is_probable_export"])

    def test_export_const(self):
        r = classify_snippet("config.ts", "CONFIG", "export const CONFIG = {")
        self.assertTrue(r["is_probable_definition"])
        self.assertTrue(r["is_probable_export"])

    def test_named_export(self):
        r = classify_snippet("index.ts", "User", "export { User, Admin }")
        self.assertTrue(r["is_probable_export"])
        self.assertEqual(r["matched_rule"], "jsts_named_export")

    def test_class_no_export(self):
        r = classify_snippet("model.ts", "User", "class User {")
        self.assertTrue(r["is_probable_definition"])
        self.assertFalse(r["is_probable_export"])

    def test_interface(self):
        r = classify_snippet("types.ts", "Props", "interface Props {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "jsts_interface")

    def test_type_alias(self):
        r = classify_snippet("types.ts", "Config", "type Config = {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "jsts_type_alias")

    def test_const_assign(self):
        r = classify_snippet("utils.js", "helper", "const helper = () => {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "jsts_assign")

    def test_import_not_def(self):
        r = classify_snippet("app.ts", "User", "import { User } from './model'")
        self.assertFalse(r["is_probable_definition"])


class TestGoRules(unittest.TestCase):
    def test_func(self):
        snippet = "func HandleAuth(w http.ResponseWriter) {"
        r = classify_snippet("handler.go", "HandleAuth", snippet)
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "go_func")

    def test_method(self):
        r = classify_snippet("server.go", "Start", "func (s *Server) Start() error {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "go_func")

    def test_type_struct(self):
        r = classify_snippet("model.go", "Config", "type Config struct {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "go_type")

    def test_type_interface(self):
        r = classify_snippet("service.go", "Store", "type Store interface {")
        self.assertTrue(r["is_probable_definition"])

    def test_call_not_def(self):
        r = classify_snippet("main.go", "HandleAuth", "    HandleAuth(w, r)")
        self.assertFalse(r["is_probable_definition"])


class TestJavaCsRules(unittest.TestCase):
    def test_class(self):
        r = classify_snippet("User.java", "User", "public class User {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "javacs_type")

    def test_interface(self):
        r = classify_snippet("IStore.cs", "IStore", "public interface IStore {")
        self.assertTrue(r["is_probable_definition"])

    def test_enum(self):
        r = classify_snippet("Status.java", "Status", "enum Status {")
        self.assertTrue(r["is_probable_definition"])

    def test_method(self):
        r = classify_snippet("Service.java", "process", "    public void process() {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "javacs_method")


class TestRustRules(unittest.TestCase):
    def test_fn(self):
        snippet = "fn process(data: &[u8]) -> Result<()> {"
        r = classify_snippet("lib.rs", "process", snippet)
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "rs_fn")

    def test_pub_fn(self):
        r = classify_snippet("lib.rs", "handle", "pub fn handle(req: Request) {")
        self.assertTrue(r["is_probable_definition"])

    def test_struct(self):
        r = classify_snippet("model.rs", "Config", "pub struct Config {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "rs_struct")

    def test_enum(self):
        r = classify_snippet("types.rs", "Status", "enum Status {")
        self.assertTrue(r["is_probable_definition"])

    def test_trait(self):
        r = classify_snippet("traits.rs", "Store", "pub trait Store {")
        self.assertTrue(r["is_probable_definition"])

    def test_impl(self):
        r = classify_snippet("model.rs", "Config", "impl Config {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "rs_impl")
        self.assertLess(r["def_conf"], 0.9)  # lower confidence for impl


class TestCCppRules(unittest.TestCase):
    def test_class(self):
        r = classify_snippet("model.hpp", "Config", "class Config {")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "c_type")

    def test_struct(self):
        r = classify_snippet("types.h", "Node", "struct Node {")
        self.assertTrue(r["is_probable_definition"])

    def test_macro(self):
        r = classify_snippet("defs.h", "MAX_SIZE", "#define MAX_SIZE 1024")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["matched_rule"], "c_macro")


class TestGenericFallback(unittest.TestCase):
    def test_unknown_extension(self):
        r = classify_snippet("script.lua", "MyFunc", "function MyFunc()")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["lang_family"], "generic")

    def test_no_match(self):
        r = classify_snippet("script.lua", "MyFunc", "-- just a comment about MyFunc")
        self.assertFalse(r["is_probable_definition"])


class TestCallSiteDetection(unittest.TestCase):
    def test_function_call(self):
        r = classify_snippet("app.py", "login", "    result = login(user)")
        self.assertFalse(r["is_probable_definition"])
        self.assertTrue(r["is_probable_call_site"])
        self.assertGreater(r["call_conf"], 0.0)

    def test_method_call(self):
        r = classify_snippet("app.py", "login", "    auth.login(user)")
        self.assertTrue(r["is_probable_call_site"])

    def test_constructor_call(self):
        snippet = "    const user = new User(data)"
        r = classify_snippet("app.ts", "User", snippet)
        self.assertTrue(r["is_probable_call_site"])
        self.assertGreaterEqual(r["call_conf"], 0.8)

    def test_rust_path_access(self):
        r = classify_snippet("main.rs", "Config", "    Config::new()")
        self.assertTrue(r["is_probable_call_site"])

    def test_comment_not_call_site(self):
        r = classify_snippet("app.py", "login", "# login is deprecated")
        self.assertFalse(r["is_probable_call_site"])

    def test_import_not_call_site(self):
        snippet = "from auth import login"
        r = classify_snippet("app.py", "login", snippet)
        self.assertFalse(r["is_probable_call_site"])

    def test_js_import_not_call_site(self):
        snippet = "import { User } from './model'"
        r = classify_snippet("app.ts", "User", snippet)
        self.assertFalse(r["is_probable_call_site"])

    def test_definition_suppresses_call(self):
        """If snippet IS a def, call_site should be False."""
        r = classify_snippet("auth.py", "login", "def login(user):")
        self.assertTrue(r["is_probable_definition"])
        self.assertFalse(r["is_probable_call_site"])
        self.assertEqual(r["call_conf"], 0.0)

    def test_multiline_call(self):
        text = """import os

    result = process(data)
    print(result)
"""
        r = classify_snippet("app.py", "process", text)
        self.assertTrue(r["is_probable_call_site"])

    def test_empty_result_includes_call_fields(self):
        r = classify_snippet("foo.py", "bar", "")
        self.assertIn("is_probable_call_site", r)
        self.assertIn("call_conf", r)
        self.assertFalse(r["is_probable_call_site"])
        self.assertEqual(r["call_conf"], 0.0)


class TestEdgeCases(unittest.TestCase):
    def test_empty_text(self):
        r = classify_snippet("foo.py", "bar", "")
        self.assertFalse(r["is_probable_definition"])
        self.assertEqual(r["def_conf"], 0.0)

    def test_empty_symbol(self):
        r = classify_snippet("foo.py", "", "def something():")
        self.assertFalse(r["is_probable_definition"])

    def test_special_chars_in_symbol(self):
        r = classify_snippet("foo.py", "a+b", "a+b = 5")
        # Should not crash (regex escape)
        self.assertIsInstance(r["is_probable_definition"], bool)

    def test_lang_hint_override(self):
        r = classify_snippet("weird.txt", "handler", "func handler() {", lang_hint="go")
        self.assertTrue(r["is_probable_definition"])
        self.assertEqual(r["lang_family"], "go")

    def test_multiline_snippet(self):
        text = """import os

def process(data):
    return data.strip()
"""
        r = classify_snippet("utils.py", "process", text)
        self.assertTrue(r["is_probable_definition"])


class TestLangDetection(unittest.TestCase):
    def test_python(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("src/auth.py"), "python")

    def test_typescript(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("src/model.ts"), "js_ts")

    def test_go(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("cmd/server.go"), "go")

    def test_rust(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("src/lib.rs"), "rust")

    def test_java(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("User.java"), "java_cs")

    def test_backslash_path(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("src\\auth.py"), "python")

    def test_unknown(self):
        from cts.structural import _detect_lang

        self.assertEqual(_detect_lang("README.md"), "generic")


if __name__ == "__main__":
    unittest.main()
