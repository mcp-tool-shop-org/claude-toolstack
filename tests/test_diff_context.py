"""Tests for cts.diff_context — diff parsing and change-aware signals."""

from __future__ import annotations

import unittest

from cts.diff_context import build_diff_context, is_in_hunk

SAMPLE_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index abc1234..def5678 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@ class AuthHandler:
     def __init__(self):
         self.store = {}

+    def login(self, user, password):
+        return self.store.get(user) == password
+
     def logout(self):
         pass
diff --git a/src/utils.py b/src/utils.py
index 1111111..2222222 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,5 @@
+import hashlib
+
 def hash_password(pw):
-    return pw
+    return hashlib.sha256(pw.encode()).hexdigest()
"""


class TestBuildDiffContext(unittest.TestCase):
    def test_changed_files(self):
        ctx = build_diff_context(SAMPLE_DIFF)
        self.assertEqual(ctx["changed_files"], {"src/auth.py", "src/utils.py"})

    def test_hunk_ranges(self):
        ctx = build_diff_context(SAMPLE_DIFF)
        self.assertIn("src/auth.py", ctx["hunk_ranges"])
        ranges = ctx["hunk_ranges"]["src/auth.py"]
        self.assertEqual(len(ranges), 1)
        # @@ -10,6 +10,8 @$ -> start=10, count=8, end=17
        self.assertEqual(ranges[0], (10, 17))

    def test_changed_identifiers(self):
        ctx = build_diff_context(SAMPLE_DIFF)
        idents = ctx["changed_identifiers"]
        self.assertIn("login", idents)
        self.assertIn("hashlib", idents)
        self.assertIn("sha256", idents)

    def test_empty_diff(self):
        ctx = build_diff_context("")
        self.assertEqual(ctx["changed_files"], set())
        self.assertEqual(ctx["hunk_ranges"], {})
        self.assertEqual(ctx["changed_identifiers"], set())

    def test_single_line_hunk(self):
        diff = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -5 +5 @@
-old
+new_value
"""
        ctx = build_diff_context(diff)
        ranges = ctx["hunk_ranges"]["f.py"]
        self.assertEqual(ranges[0], (5, 5))

    def test_multiple_hunks_same_file(self):
        diff = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -10,5 +10,7 @@
+first
@@ -50,3 +52,5 @@
+second
"""
        ctx = build_diff_context(diff)
        ranges = ctx["hunk_ranges"]["f.py"]
        self.assertEqual(len(ranges), 2)


class TestIsInHunk(unittest.TestCase):
    def test_within_range(self):
        hunks = {"src/auth.py": [(10, 17)]}
        self.assertTrue(is_in_hunk("src/auth.py", 12, hunks))

    def test_within_tolerance(self):
        hunks = {"src/auth.py": [(10, 17)]}
        # 5 lines before hunk start
        self.assertTrue(is_in_hunk("src/auth.py", 5, hunks))
        # 5 lines after hunk end
        self.assertTrue(is_in_hunk("src/auth.py", 22, hunks))

    def test_outside_tolerance(self):
        hunks = {"src/auth.py": [(10, 17)]}
        self.assertFalse(is_in_hunk("src/auth.py", 3, hunks))
        self.assertFalse(is_in_hunk("src/auth.py", 24, hunks))

    def test_different_file(self):
        hunks = {"src/auth.py": [(10, 17)]}
        self.assertFalse(is_in_hunk("src/other.py", 12, hunks))

    def test_empty_hunks(self):
        self.assertFalse(is_in_hunk("src/auth.py", 12, {}))


if __name__ == "__main__":
    unittest.main()
