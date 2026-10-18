"""The scorecard's "show the math" details and notes are site prose, and
the site uses no em dashes (#803; frontend/src/lib/noEmDash.test.ts keeps
the frontend to it). #803 left the scorers for after the branches then open
on them merged; this keeps them, and the donor-vote connection's
description, to it from here on.

Every string constant in the modules is checked except docstrings and
the arguments of logger calls, which are never shown on the site.

The public API's spec is site prose too: /developers renders its summaries
and descriptions, which come from the route docstrings and Field
descriptions in app/api/public.py."""

import ast
import json
import pathlib
import re

from app.api.public import openapi_spec_dict

MODULES = [
    "app/pipeline/analyze/score_calculator.py",
    "app/pipeline/analyze/president_scorer.py",
    # The donor-vote connection's description (LobbyingMatches on the scorecard).
    "app/pipeline/analyze/policy_alignment.py",
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


def test_no_em_dash_in_the_public_api_spec():
    text = json.dumps(openapi_spec_dict(), ensure_ascii=False)
    offenders = [text[max(0, i - 60):i + 20] for i, ch in enumerate(text) if ch == "—"]
    assert offenders == [], "\n".join(offenders)


def test_the_public_api_spec_is_evergreen():
    """No Congress named by number and no internal dated note in what
    /developers renders: "the 119th Congress's lines" goes stale at the next
    Congress, and "went out without (2026-10-11)" is a note to ourselves."""
    text = json.dumps(openapi_spec_dict(), ensure_ascii=False)
    stale = re.findall(r".{0,40}(?:\b\d{2,3}(?:st|nd|rd|th) Congress|\(\d{4}-\d{2}-\d{2}\)).{0,20}", text)
    assert stale == [], "\n".join(stale)
