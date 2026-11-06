import ast
import io
import os
import subprocess
import sys
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "contract.py")
DEPLOY = os.path.join(ROOT, "contract_deploy.py")
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

import make_deploy  # noqa: E402

HEADER = make_deploy.HEADER


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestDeployFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(DEPLOY)
        cls.lines = cls.text.splitlines()

    def test_header_is_exactly_two_lines_then_code(self):
        self.assertEqual(tuple(self.lines[:2]), HEADER)
        self.assertEqual(self.lines[2], "from genlayer import *")

    def test_no_other_comment_lines(self):
        comments = [t for t in tokenize.generate_tokens(io.StringIO(self.text).readline) if t.type == tokenize.COMMENT]
        self.assertEqual(len(comments), 2)
        self.assertEqual({c.start[0] for c in comments}, {1, 2})

    def test_no_docstrings_remain(self):
        for node in ast.walk(ast.parse(self.text)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                self.assertIsNone(ast.get_docstring(node, clean=False), getattr(node, "name", "module"))

    def test_no_bare_string_statements(self):
        for node in ast.walk(ast.parse(self.text)):
            self.assertFalse(make_deploy._is_string_statement(node) if isinstance(node, ast.Expr) else False)

    def test_syntax_tree_equals_the_documented_source(self):
        body = "\n".join(read(SOURCE).splitlines()[2:]) + "\n"
        expected = make_deploy.remove_docstrings_from_tree(ast.parse(body))
        self.assertEqual(ast.dump(expected), ast.dump(ast.parse("\n".join(self.lines[2:]) + "\n")))

    def test_deploy_file_is_ascii_and_has_no_tabs_or_trailing_spaces(self):
        self.text.encode("ascii")
        self.assertNotIn("\t", self.text)
        for number, line in enumerate(self.lines, 1):
            self.assertEqual(line, line.rstrip(), number)

    def test_no_runs_of_blank_lines(self):
        self.assertNotIn("\n\n\n\n", self.text)

    def test_it_is_up_to_date(self):
        result = subprocess.run(
            [sys.executable, "-I", os.path.join(TOOLS, "make_deploy.py"), "--check"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_it_is_smaller_than_the_documented_source(self):
        self.assertLess(len(self.lines), len(read(SOURCE).splitlines()))


class TestStripper(unittest.TestCase):
    def strip(self, source):
        return make_deploy.strip_source(source)

    def test_strips_docstrings_and_comments(self):
        out = self.strip('def f():\n    """doc"""\n    return 1  # note\n')
        self.assertEqual(out, "def f():\n    return 1\n")

    def test_keeps_hash_characters_inside_strings(self):
        self.assertIn('"# not a comment"', self.strip('X = "# not a comment"  # real\n'))

    def test_docstring_only_body_becomes_pass(self):
        out = self.strip('class A:\n    """only"""\n')
        self.assertEqual(out, "class A:\n    pass\n")

    def test_multiline_docstrings_are_removed_whole(self):
        out = self.strip('def f():\n    """a\n    b\n    c"""\n    return 2\n')
        self.assertEqual(out, "def f():\n    return 2\n")

    def test_collapses_blank_runs(self):
        self.assertEqual(self.strip("a = 1\n\n\n\n\nb = 2\n"), "a = 1\n\nb = 2\n")

    def test_multiline_string_values_are_kept(self):
        out = self.strip('X = """line1\n# inside\nline3"""\n')
        self.assertIn("# inside", out)

    def test_code_is_never_changed_by_the_strip(self):
        source = 'def f(x):\n    """d"""\n    # c\n    return {"a": [x, 1]}  # t\n'
        stripped = self.strip(source)
        expected = make_deploy.remove_docstrings_from_tree(ast.parse(source))
        self.assertEqual(ast.dump(expected), ast.dump(ast.parse(stripped)))


class TestSmokeRunAgainstTheDeployFile(unittest.TestCase):
    def test_the_whole_suite_core_flow_runs_on_the_stripped_file(self):
        env = dict(os.environ, RB_CONTRACT=DEPLOY)
        result = subprocess.run(
            [sys.executable, "-I", "-m", "unittest", "discover", "-s", os.path.join(ROOT, "tests"), "-p", "test_settlement.py"],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
