import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _is_main_guard(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def _is_unittest_main(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "main"
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "unittest"
    )


def layout_problems(source):
    """Return problems with a test module's `if __name__ == "__main__":` block."""
    body = ast.parse(source).body
    guards = [i for i, n in enumerate(body) if _is_main_guard(n)]
    if not guards:
        return ["no `if __name__ == \"__main__\":` block"]
    problems = []
    if guards[-1] != len(body) - 1 or len(guards) > 1:
        problems.append("main block is not the final statement")
    g = body[guards[-1]]
    if g.orelse or len(g.body) != 1 or not _is_unittest_main(g.body[0]):
        problems.append("main block must contain only unittest.main()")
    return problems


class MainBlockLast(unittest.TestCase):
    def test_every_test_module_ends_with_main_block(self):
        files = sorted(TESTS.glob("test_*.py"))
        self.assertTrue(files)
        for f in files:
            with self.subTest(file=f.name):
                self.assertEqual(layout_problems(f.read_text()), [])

    def test_detects_class_after_main_block(self):
        src = (
            "import unittest\n"
            "if __name__ == \"__main__\":\n    unittest.main()\n"
            "class Late(unittest.TestCase):\n    pass\n"
        )
        self.assertIn("main block is not the final statement", layout_problems(src))

    def test_detects_extra_statement_in_main_block(self):
        src = "import unittest\nif __name__ == \"__main__\":\n    x = 1\n    unittest.main()\n"
        self.assertIn("main block must contain only unittest.main()", layout_problems(src))


if __name__ == "__main__":
    unittest.main()
