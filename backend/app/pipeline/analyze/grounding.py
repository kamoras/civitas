"""Deterministic grounding checks for LLM-generated text.

The generation prompts instruct the model to use only information from
its source material, but nothing enforced that: a local model can (and
does) drift — inventing statistics, or attributing actions to officials
the coverage never named. These checks verify the two highest-precision
hallucination signals mechanically, with no model in the loop:

  - Numbers: every digit group in the generated text must appear in the
    source text. Fabricated statistics are the most damaging class of
    hallucination for a data-transparency platform, and digit groups are
    cheap to compare exactly.
  - Titled officials: every "Senator X" / "Rep. Y" style reference must
    name a surname that appears somewhere in the source text.

Both checks compare generated text against ONLY the source material it
was generated from — nothing here encodes what the text should say.
"""

import re

# Digit groups, tolerant of thousands separators and decimals:
# "120,000" -> "120000", "2.5" -> "2.5", "$67M" -> "67".
_DIGIT_GROUP_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

_TITLED_NAME_RE = re.compile(
    r"\b(?:U\.?S\.?\s+)?(?:Senator|Sen\.|Representative|Rep\.|"
    r"Congressman|Congresswoman|Speaker|Justice|Gov(?:ernor)?\.?)\s+"
    r"([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)?)"
)


def _normalize_token(raw: str) -> str:
    tok = raw.replace(",", "").rstrip(".")
    if "." not in tok:
        # "09" (from 2026-07-09) and "9" (from "July 9") are the same
        # number; decimals keep their leading zero ("0.5").
        tok = tok.lstrip("0") or "0"
    return tok


def _number_tokens(text: str) -> set[str]:
    return {
        _normalize_token(m.group(0)) for m in _DIGIT_GROUP_RE.finditer(text or "")
    }


_STAT_CONTEXT = ("$", "%", "percent", "million", "billion", "trillion")


def ungrounded_statistics(generated: str, source: str) -> list[str]:
    """Like ungrounded_numbers, but only for statistic-shaped numbers.

    For long-form prose, checking every digit group over-rejects: contextual
    numbers ("three of the 12 members", ordinal years) are often phrased
    differently than the source without being fabrications. Money,
    percentages, and magnitude-worded figures are the numbers that damage
    credibility when invented — a digit group counts as a statistic when $,
    %, or a magnitude word appears within a few characters of it.
    """
    source_numbers = _number_tokens(source)
    missing = set()
    text = generated or ""
    for m in _DIGIT_GROUP_RE.finditer(text):
        context = text[max(0, m.start() - 3):m.end() + 12].lower()
        if not any(k in context for k in _STAT_CONTEXT):
            continue
        tok = _normalize_token(m.group(0))
        if tok not in source_numbers:
            missing.add(tok)
    return sorted(missing)


def ungrounded_numbers(generated: str, source: str) -> list[str]:
    """Digit groups in ``generated`` that never appear in ``source``."""
    source_numbers = _number_tokens(source)
    return sorted(
        n for n in _number_tokens(generated) if n not in source_numbers
    )


def ungrounded_titled_names(generated: str, source: str) -> list[str]:
    """Titled-official references in ``generated`` whose surname is absent
    from ``source``.

    Only the surname (last token of the captured name) is required to
    appear, so "Sen. Collins" is grounded by source text that says
    "Susan Collins" without a title.
    """
    source_lower = (source or "").lower()
    missing = []
    for m in _TITLED_NAME_RE.finditer(generated or ""):
        surname = m.group(1).split()[-1].lower()
        if surname not in source_lower:
            missing.append(m.group(0))
    return sorted(set(missing))


def grounding_violations(generated: str, source: str) -> list[str]:
    """Human-readable list of grounding failures, empty when clean."""
    problems = []
    numbers = ungrounded_numbers(generated, source)
    if numbers:
        problems.append(f"numbers not in source: {', '.join(numbers)}")
    names = ungrounded_titled_names(generated, source)
    if names:
        problems.append(f"officials not in source: {', '.join(names)}")
    return problems
