from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

ALLOWED = ("#!", "# noqa", "# type:", "# fmt:", "# pragma:", "# ruff:")


def _docstrings(tree: ast.AST) -> list[int]:
    return [
        node.body[0].lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ]


def _comments(source: str) -> list[int]:
    return [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not token.string.startswith(ALLOWED)
    ]


def check(paths: list[str]) -> list[str]:
    findings = []
    for root in paths:
        files = [Path(root)] if Path(root).is_file() else sorted(Path(root).rglob("*.py"))
        for path in files:
            source = path.read_text(encoding="utf-8")
            lines = sorted(_docstrings(ast.parse(source)) + _comments(source))
            findings.extend(
                f"{path}:{line}: comments and docstrings are not allowed" for line in lines
            )
    return findings


if __name__ == "__main__":
    problems = check(sys.argv[1:])
    for problem in problems:
        print(problem)
    raise SystemExit(1 if problems else 0)
