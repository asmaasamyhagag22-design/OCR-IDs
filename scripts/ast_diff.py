"""Stage 7: which changed Python files differ from a git revision in more than layout.

    python scripts/ast_diff.py [REV]          # REV defaults to HEAD; checks the files docker/app_code_files.txt lists

For each shipped .py file that differs from REV, the two syntax trees are compared without
line and column positions. Formatting, import-order changes that keep the same statements and
comments leave the tree equal; anything else is listed for review. Docstrings are part of the
tree, so a docstring edit is listed too (by design: it is reviewed, not assumed harmless).
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _StripTyping(ast.NodeTransformer):
    """Remove what only a type checker reads: annotations, `cast(T, x)` (kept as `x`), `if TYPE_CHECKING:`
    blocks and import statements (import edits are reviewed on their own)."""

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.annotation = None
        return node

    def visit_FunctionDef(self, node):  # noqa: N802
        node.returns = None
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):  # noqa: N802
        if node.value is None:
            return None
        return ast.Assign(targets=[node.target], value=self.visit(node.value))

    def visit_Call(self, node):  # noqa: N802
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "cast" and len(node.args) == 2:
            return node.args[1]
        return node

    def visit_If(self, node):  # noqa: N802
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            return None
        self.generic_visit(node)
        return node

    def visit_Import(self, node):  # noqa: N802
        return None

    visit_ImportFrom = visit_Import


def tree(source: str, strip_typing: bool = False) -> str:
    parsed = ast.parse(source)
    if strip_typing:
        parsed = _StripTyping().visit(parsed)
    return ast.dump(parsed, include_attributes=False)


def main(argv: list[str]) -> int:
    strip = "--strip-typing" in argv
    argv = [a for a in argv if a != "--strip-typing"]
    rev = argv[1] if len(argv) > 1 else "HEAD"
    files = [f for f in (ROOT / "docker" / "app_code_files.txt").read_text(encoding="utf-8").splitlines()
             if f.endswith(".py")]
    same, differs, unchanged = [], [], 0
    for rel in files:
        now = (ROOT / rel).read_text(encoding="utf-8")
        try:
            before = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{rel}"], capture_output=True,
                                    check=True, encoding="utf-8").stdout
        except subprocess.CalledProcessError:
            differs.append((rel, "new file"))
            continue
        if before.replace("\r\n", "\n") == now.replace("\r\n", "\n"):
            unchanged += 1
        elif tree(before, strip) == tree(now, strip):
            same.append(rel)
        else:
            differs.append((rel, "syntax tree differs"))
    print(f"unchanged: {unchanged}; layout only (tree equal): {len(same)}; tree differs: {len(differs)}")
    for rel, why in differs:
        print(f"  REVIEW {rel}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
