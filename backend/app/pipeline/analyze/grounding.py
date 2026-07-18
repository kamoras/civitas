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

# Appositive form: a role description set off by commas rather than a title
# word directly prefixing the name — "the Senate Republican leader, Chuck
# Schumer, has said ...". _TITLED_NAME_RE requires the title word immediately
# before the name and doesn't cover this at all, which is exactly how a
# fabricated name reached production ungrounded (2026-07: a full-story
# generation invented "The Senate Republican leader, Chuck Schumer, has said
# Graham's death has made a hard month harder for the Senate agenda" — no
# Schumer mention anywhere in the source material, and the only grounding
# check run on full-story text was for fabricated statistics). A short
# unclaimed span between the role keyword and the comma keeps this from
# crossing into an unrelated clause; requiring the trailing comma (not just
# any capitalized word) keeps it from matching an ordinary sentence start.
_APPOSITIVE_ROLE_RE = re.compile(
    r"\b(?:leader|chairman|chairwoman|chair|whip|speaker|majority|minority|"
    r"secretary|director|governor|president)\b[^,\n]{0,40},\s*"
    r"([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)?),",
    re.IGNORECASE,
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


_STAT_CONTEXT = (
    "$", "%", "percent", "million", "billion", "trillion",
    "degree", "point", "ton", "acre", "death", "case", "vote",
    "mile", "foot", "feet", "pound", "gallon", "barrel", "year-old",
)

# A bare 4-digit number in this range reads as a calendar year regardless
# of nearby words ("signed in 2023", "the 2023 ruling") — this is exactly
# where a model asked to write a specific-sounding sentence about a thin
# fact set fabricates a plausible year from its training data rather than
# leaving the date out (observed 2026-07: a story about an unrelated
# fact set stated a fictional AI-export-ban was "lifted on July 15, 2023"
# — no date of any kind was in the source material).
_YEAR_RANGE = range(1900, 2100)


def ungrounded_statistics(generated: str, source: str) -> list[str]:
    """Like ungrounded_numbers, but only for statistic-shaped numbers.

    For long-form prose, checking every digit group over-rejects: contextual
    numbers ("three of the 12 members", ordinal years) are often phrased
    differently than the source without being fabrications. Money,
    percentages, magnitude-worded figures, and bare years are the numbers
    that damage credibility when invented — a digit group counts as a
    statistic when $, %, a magnitude word appears within a few characters
    of it, or the digit group is itself a plausible calendar year.
    """
    source_numbers = _number_tokens(source)
    missing = set()
    text = generated or ""
    for m in _DIGIT_GROUP_RE.finditer(text):
        tok = _normalize_token(m.group(0))
        context = text[max(0, m.start() - 3):m.end() + 12].lower()
        is_year = tok.isdigit() and len(tok) == 4 and int(tok) in _YEAR_RANGE
        if not is_year and not any(k in context for k in _STAT_CONTEXT):
            continue
        if tok not in source_numbers:
            missing.add(tok)
    return sorted(missing)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def repeated_sentences(generated: str, min_words: int = 6) -> list[str]:
    """Sentences of at least ``min_words`` that appear more than once.

    Small local models asked to hit a word-count floor past the point
    where the source material runs out sometimes loop: the same one or
    two sentences reappear verbatim later in the text instead of the
    model stopping (observed 2026-07: two consecutive full-story
    generations each repeated their closing two sentences word-for-word).
    Short sentences are excluded so legitimate short transitions
    ("He said no.") don't false-positive.
    """
    seen: dict[str, int] = {}
    for raw in _SENTENCE_SPLIT_RE.split(generated or ""):
        sentence = " ".join(raw.split())  # normalize whitespace
        if len(sentence.split()) < min_words:
            continue
        key = sentence.lower().rstrip(".!?")
        seen[key] = seen.get(key, 0) + 1
    return sorted(s for s, count in seen.items() if count > 1)


def ungrounded_numbers(generated: str, source: str) -> list[str]:
    """Digit groups in ``generated`` that never appear in ``source``."""
    source_numbers = _number_tokens(source)
    return sorted(
        n for n in _number_tokens(generated) if n not in source_numbers
    )


def ungrounded_titled_names(generated: str, source: str) -> list[str]:
    """Titled-official references in ``generated`` whose surname is absent
    from ``source``.

    Covers both a title word directly prefixing a name ("Sen. Collins") and
    a role description set off by commas ("the Senate Republican leader,
    Chuck Schumer,") — see _APPOSITIVE_ROLE_RE for why the second form
    matters.

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
    for m in _APPOSITIVE_ROLE_RE.finditer(generated or ""):
        name = m.group(1)
        surname = name.split()[-1].lower()
        if surname not in source_lower:
            missing.append(name)
    return sorted(set(missing))


_HEDGE_PHRASE_RE = re.compile(
    r"\b(?:recent\s+)?(?:reports?|coverage|sources?|officials?|developments?|"
    r"discussions?|debates?|experts?|analysts?|observers?|critics?)\s+"
    r"(?:say|says|said|suggest(?:s|ed)?|indicate(?:s|d)?|show(?:s|ed)?|reveal(?:s|ed)?|"
    r"highlight(?:s|ed)?|emphasiz(?:e|es|ed)|stress(?:es|ed)?|note(?:s|d)?|"
    r"focus(?:es|ed)?\s+on|aim(?:s|ed)?\s+to)\b"
    r"|\baccording to (?:reports?|coverage|sources?)\b",
    re.IGNORECASE,
)


def hedge_language(generated: str) -> list[str]:
    """Attribution-hedge phrases ("recent reports say," "coverage indicates,"
    "sources suggest," "according to reports," "recent discussions highlight,"
    "officials stress") that talk about the news instead of reporting it
    directly.

    The generation prompt already instructs the model to report events
    directly rather than through this kind of middle-man phrasing, but a
    locally-run model doesn't reliably follow that instruction (observed
    2026-07 on a newer local model). Unlike the other checks in this module,
    this doesn't compare against source material — it only looks at how the
    generated text itself is phrased.

    The noun/verb lists were widened 2026-07 after live Bluesky posts kept
    hedging with words just outside the original narrow list — "recent
    discussions emphasize," "officials stress," "recent reports highlight" —
    each individually distinct from the phrases the original regex covered
    ("reports say," "coverage shows") but doing the exact same middle-man
    framing. This is inherently a whack-a-mole list, not a closed set; treat
    any newly observed hedge phrase the same way.
    """
    return sorted({m.group(0) for m in _HEDGE_PHRASE_RE.finditer(generated or "")})


_EDITORIALIZING_RE = re.compile(
    r"\b(?:is|was|are|were)\s+(?:warranted|justified)\b"
    r"|\b(?:rightly|understandably)\b"
    r"|\bhelps?\s+(?:advance|move|push)\s+(?:the\s+)?(?:legislation|bill|agenda)\b"
    r"|\bmakes sense given\b",
    re.IGNORECASE,
)


def editorializing_language(generated: str) -> list[str]:
    """Phrases that pass judgment on whether an action was legitimate,
    justified, or well-motivated, rather than only reporting what happened.

    Civitas doesn't take a position on whether an actor's stated rationale
    for an action holds up. This isn't a factual error the other checks
    would catch (no fabricated number or name) but a violation of the
    "report, don't opine" instruction that — like the hedge-phrase pattern
    above — prompting alone hasn't reliably prevented on a newer local
    model (observed 2026-07: a full story framed a senator's speech as
    "warranted" by referencing the speaker's own disputed claims, and
    editorialized about how the speech "helps move legislation"). This is
    deliberately narrow, matching this module's high-precision philosophy —
    it catches clear-cut legitimizing language, not every possible way a
    model could editorialize.
    """
    return sorted({m.group(0) for m in _EDITORIALIZING_RE.finditer(generated or "")})


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


def hedge_and_editorializing_violations(generated: str) -> list[str]:
    """Human-readable list of hedge-phrase and editorializing findings, empty
    when clean.

    Every LLM generation path that publishes text (Bluesky posts, action-
    center issue summaries/facts, full stories) needs both checks, and
    duplicating the "if hedges: ... if editorial: ..." formatting at each
    call site was itself how one path — the Bluesky poster — went
    unchecked for months after the checks were added elsewhere (2026-07).
    Centralizing the formatting here means a future third check only needs
    to be added in one place.
    """
    problems = []
    hedges = hedge_language(generated)
    if hedges:
        problems.append(f"hedging attribution phrases ({', '.join(hedges)})")
    editorial = editorializing_language(generated)
    if editorial:
        problems.append(
            f"language evaluating whether an action was justified ({', '.join(editorial)})"
        )
    return problems
