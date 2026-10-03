"""The scorecard's "show the math" details and notes are site prose, and
the site uses no em dashes (#803; frontend/src/lib/noEmDash.test.ts keeps
the frontend to it). #803 left these two modules for after the branches
then open on them merged; this keeps them to it from here on.

Every string constant in the modules is checked except docstrings and
the arguments of logger calls, which are never shown on the site."""

import ast
import pathlib

MODULES = [
    "app/pipeline/analyze/score_calculator.py",
    "app/pipeline/analyze/president_scorer.py",
]
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _shown_strings(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                skip.add(id(first.value))
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"
        ):
            for sub in ast.walk(node):
                skip.add(id(sub))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
    ]


def test_no_em_dash_in_text_the_scorecard_shows():
    offenders = [
        f"{module}:{line}: {text[:70]!r}"
        for module in MODULES
        for line, text in _shown_strings(ROOT / module)
        if "—" in text
    ]
    assert offenders == [], "\n".join(offenders)
