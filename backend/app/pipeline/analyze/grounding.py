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
    "Susan Collins" without a title. The surname must appear as a whole
    WORD (2026-07 fix): the original bare-substring check grounded
    "Rep. Ford" in any source containing "affordable" and "Sen. Price"
    in "prices" — short surnames were effectively never checkable.
    """
    source_lower = (source or "").lower()

    def _grounded(surname: str) -> bool:
        return re.search(rf"\b{re.escape(surname)}\b", source_lower) is not None

    missing = []
    for m in _TITLED_NAME_RE.finditer(generated or ""):
        surname = m.group(1).split()[-1].lower()
        if not _grounded(surname):
            missing.append(m.group(0))
    for m in _APPOSITIVE_ROLE_RE.finditer(generated or ""):
        name = m.group(1)
        surname = name.split()[-1].lower()
        if not _grounded(surname):
            missing.append(name)
    return sorted(set(missing))


_HEDGE_PHRASE_RE = re.compile(
    r"\b(?:recent\s+)?(?:reports?|coverage|sources?|officials?|developments?|"
    r"discussions?|debates?|experts?|analysts?|observers?|critics?)\s+"
    r"(?:say|says|said|suggest(?:s|ed)?|indicate(?:s|d)?|show(?:s|ed)?|reveal(?:s|ed)?|"
    r"highlight(?:s|ed)?|emphasiz(?:e|es|ed)|stress(?:es|ed)?|note(?:s|d)?|"
    r"focus(?:es|ed)?\s+on|aim(?:s|ed)?\s+to|"
    # 2026-07 audit additions, each from a published live example: "These
    # developments point to a more nuanced landscape" / "This development
    # reflects the broader challenges" / "The coverage from BBC World and
    # PBS NewsHour captured these developments" — same middle-man framing
    # as the original verbs, different verb.
    r"underscore(?:s|d)?|point(?:s|ed)?\s+to|reflect(?:s|ed)?|"
    r"illustrate(?:s|d)?|captur(?:e|es|ed))\b"
    r"|\baccording to (?:reports?|coverage|sources?)\b"
    # "The coverage from BBC World and PBS NewsHour captured these
    # developments" — the noun+verb form above requires adjacency, and the
    # live example puts the source list between them.
    r"|\bcaptur(?:e|es|ed)\s+these\s+developments\b"
    r"|\bthe\s+coverage\s+from\b",
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
    r"|\bmakes sense given\b"
    r"|\breflects?\s+(?:a\s+|an\s+|broader\s+)?efforts?\s+to\b"
    r"|\bin an effort to\b"
    r"|\b(?:aims?|seeks?)\s+to\s+(?:shape|manage|control)\s+(?:public\s+)?perception\b"
    # 2026-07 audit additions, each from a published live example:
    # speculation about future effect ("This shift may influence how
    # political leaders frame their messaging") and unattributed motive/
    # causation claims ("The timing of her announcement was influenced by
    # President Trump's public statements, which shaped the tone and focus
    # of the race") — asserting why an actor did something or what it will
    # accomplish, rather than what was done.
    r"|\b(?:may|could|might)\s+(?:influence|shape|affect)\s+how\b"
    r"|\b(?:influenced|shaped)\s+the\s+(?:timing|tone|focus|narrative)\b"
    r"|\bremains?\s+a\s+point\s+of\s+discussion\b",
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

    The "reflects efforts to" / "in an effort to" / "aims to shape
    perception" patterns were added 2026-07 after a live full story wrote
    that an administration's actions "reflect broader efforts to manage
    public perception around election integrity" — a different flavor of
    editorializing than the original set: not judging whether an action was
    warranted, but asserting the *strategic motive behind* an action as
    established fact rather than reporting only what was said or done.
    """
    return sorted({m.group(0) for m in _EDITORIALIZING_RE.finditer(generated or "")})


# Electoral-contest framing in the GENERATED text — a claim that someone is
# running, being challenged, or competing for an office. High-specificity
# phrases only: bare "opponent"/"challenger" are excluded because they also
# describe opponents of a bill or challengers to a ruling.
_ELECTORAL_CLAIM_RE = re.compile(
    r"\b(?:"
    r"(?:senate|house|gubernatorial|presidential|congressional|governor'?s?)\s+race"
    r"|race\s+for\s+(?:the\s+)?(?:senate|house|white\s+house|governor|governorship|president|presidency|congress)"
    r"|re-?election(?:\s+bid|\s+campaign|\s+race)?"
    r"|primar(?:y|ies)\s+challeng(?:e|er)|primary\s+opponent|primaried"
    r"|general\s+election|on\s+the\s+ballot|up\s+for\s+re-?election"
    r"|running\s+(?:for|against)|run\s+for\s+(?:the\s+)?(?:senate|house|office|president|governor|re-?election)"
    r"|facing\s+(?:competition|a\s+challenge|a\s+challenger|a\s+primary|a\s+re-?election|an?\s+opponent)"
    r"|campaign(?:ing)?\s+(?:against|for\s+re-?election)"
    r"|unseat|electoral\s+challenge|bid\s+for\s+(?:the\s+)?(?:senate|house|governorship|presidency)"
    r")\b",
    re.IGNORECASE,
)

# Electoral vocabulary in the SOURCE material. Still the permissive side of
# the check (a match silences the whole guard), but no longer *vacuously*
# permissive (2026-07 fix): the original list included words whose dominant
# sense in civic prose is NON-electoral — "constituents" (constituent
# service), "poll(s)" (approval polling), bare "campaign" (pressure
# campaign, campaign finance), bare "race" (incl. the demographic sense in
# civil-rights coverage), and "elected" (the boilerplate "elected
# officials" appears in a large share of all political text) — so nearly
# any source disarmed the check and the fabricated-election guard almost
# never fired. Those senses are now excluded; genuinely electoral sources
# remain covered by the compound forms and the many unambiguous terms
# below. "vote" stays excluded on purpose — it is overwhelmingly a
# legislative floor-vote word in this domain — while "voters"/"ballot" are
# kept.
_ELECTORAL_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"(?:senate|house|gubernatorial|presidential|congressional|governor'?s?)\s+race"
    r"|race\s+for\b|elect(?:ion|ions|oral)\b|re-?election"
    r"|campaign\s+(?:trail|rally|ad|stop|event)|ballot|"
    r"primar(?:y|ies)|challenger|opponent|candidate|candidacy|midterms?|"
    r"unseat|incumbent|voters?|"
    r"running\s+for|contest(?:ed|ing|s)?|nominee|nomination)\b",
    re.IGNORECASE,
)


def ungrounded_electoral_claims(generated: str, source: str) -> list[str]:
    """Electoral-contest claims in ``generated`` with no electoral basis in
    ``source``.

    A recurring, high-damage hallucination class for a civic platform is the
    model inventing an *electoral contest* between two officials who both
    appear in the source material for an unrelated reason — e.g. a post about
    Sen. Graham's death that stated he "was facing competition from Susan
    Collins for his senate race" (2026-07). Both surnames were grounded and no
    number was fabricated, so neither ungrounded_titled_names nor
    ungrounded_numbers caught it: the fabrication was the *relationship*,
    framed as a campaign that never existed.

    High precision by construction — the claim is flagged only when the source
    contains NO electoral vocabulary whatsoever. A post summarizing a genuine
    race is grounded by the election coverage it draws from and passes
    untouched; only electoral framing with zero electoral basis in the source
    is rejected. Like the rest of this module, nothing here encodes what the
    text *should* say — only that a campaign asserted out of nowhere is not.
    """
    if _ELECTORAL_CONTEXT_RE.search(source or ""):
        return []
    return sorted({m.group(0).strip() for m in _ELECTORAL_CLAIM_RE.finditer(generated or "")})


# Unfilled template tokens: "[date]", "[name]", "[specific date]". Every
# other check in this module is digit- or name-based, which is exactly why
# this class reached production unchecked (2026-07 audit: a published fact
# read "Thune announced the tribute details on [date]." and the Bluesky
# post shipped with the literal "[date]" in it — no digits, no fabricated
# name, nothing else fired). Alphabetic-only content keeps this from
# flagging legitimate bracketed material like vote tallies "[216-212]" or
# citations, which generated civic prose shouldn't contain anyway but a
# narrow pattern costs nothing.
_PLACEHOLDER_TOKEN_RE = re.compile(r"\[[A-Za-z][A-Za-z ]{0,24}\]")


def placeholder_tokens(generated: str) -> list[str]:
    """Literal unfilled placeholders ("[date]", "[name]") in generated text."""
    return sorted({m.group(0) for m in _PLACEHOLDER_TOKEN_RE.finditer(generated or "")})


# Family/personal-relationship claims in the GENERATED text. Same fabrication
# class as ungrounded_electoral_claims — the model inventing a *relationship*
# between two grounded people, with no fabricated number or titled name for
# the other checks to catch (2026-07 audit: a published fact stated a
# candidate announced "for the seat left by her brother" — a family tie
# asserted as fact with nothing in the pipeline able to check it).
_RELATIONSHIP_CLAIM_RE = re.compile(
    r"\b(?:his|her|their)\s+(?:brother|sister|father|mother|son|daughter|"
    r"husband|wife|widow|widower|cousin|uncle|aunt|nephew|niece|"
    r"grandfather|grandmother|grandson|granddaughter)\b"
    r"|\b(?:brother|sister|son|daughter|widow|widower|father|mother)\s+of\b",
    re.IGNORECASE,
)

# Relationship vocabulary in the SOURCE — a single family word grounds the
# claim (permissive side, same design as _ELECTORAL_CONTEXT_RE: only a
# relationship asserted out of NOWHERE is flagged).
_RELATIONSHIP_CONTEXT_RE = re.compile(
    r"\b(?:brother|sister|father|mother|son|daughter|husband|wife|widow|"
    r"widower|cousin|uncle|aunt|nephew|niece|grandfather|grandmother|"
    r"grandson|granddaughter|family|sibling)\b",
    re.IGNORECASE,
)


def ungrounded_relationship_claims(generated: str, source: str) -> list[str]:
    """Family-relationship claims in ``generated`` with no family vocabulary
    anywhere in ``source`` — see _RELATIONSHIP_CLAIM_RE for the live case."""
    if _RELATIONSHIP_CONTEXT_RE.search(source or ""):
        return []
    return sorted({m.group(0).strip() for m in _RELATIONSHIP_CLAIM_RE.finditer(generated or "")})


# "Former <office>" status claims in the GENERATED text. This is the
# stale-training-data hallucination class: the local model's weights encode
# who held an office as of its training cutoff, so it silently "corrects" a
# sitting official's title to match its outdated world knowledge (2026-07:
# a live Bluesky post described "former President Donald Trump" while the
# source material said "President Trump" — no fabricated number, the
# surname was grounded, no electoral or family claim, and "President"
# isn't a _TITLED_NAME_RE title, so nothing fired). Like the electoral and
# relationship guards, nothing here encodes who actually holds an office —
# the check is purely: the source never called this office "former," so
# the generated text may not either.
_FORMER_CLAIM_RE = re.compile(
    r"\b(?:former|ex)[-\s]+(?:(?:vice|deputy|acting)\s+)?"
    r"(president|senator|sen\.?|representative|rep\.?|congressman|"
    r"congresswoman|governor|gov\.?|speaker|justice|secretary)\b",
    re.IGNORECASE,
)

# Equivalent surface forms per title word, so "former Sen. Smith" in the
# source grounds "former Senator Smith" in the generated text and vice
# versa. Titles without an abbreviated form fall through to re.escape.
_TITLE_FORMS = {
    "sen": r"sen(?:ator)?",
    "senator": r"sen(?:ator)?",
    "rep": r"rep(?:resentative)?",
    "representative": r"rep(?:resentative)?",
    "gov": r"gov(?:ernor)?",
    "governor": r"gov(?:ernor)?",
}


def ungrounded_former_official_claims(generated: str, source: str) -> list[str]:
    """"Former <office>" references in ``generated`` where ``source`` never
    applies "former"/"ex" to that office.

    Permissive side (same design as the electoral and relationship guards):
    the source grounds the claim if it says "former"/"formerly"/"ex" within
    a couple of words of the same office title — "the former president,"
    "former U.S. Senator," "formerly served as governor" all count. Only a
    former-status claim with zero basis in the source is flagged; a post
    genuinely about a former office-holder is grounded by the coverage it
    draws from and passes untouched.
    """
    source_text = source or ""
    missing = []
    for m in _FORMER_CLAIM_RE.finditer(generated or ""):
        title = m.group(1).rstrip(".").lower()
        title_re = _TITLE_FORMS.get(title, re.escape(title))
        grounded = re.search(
            rf"\b(?:former(?:ly)?|ex)[-\s]+(?:[\w.]+[-\s]+){{0,2}}{title_re}\b",
            source_text,
            re.IGNORECASE,
        )
        if not grounded:
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
    electoral = ungrounded_electoral_claims(generated, source)
    if electoral:
        problems.append(
            f"electoral contest not in source: {', '.join(electoral)}"
        )
    relationships = ungrounded_relationship_claims(generated, source)
    if relationships:
        problems.append(
            f"family relationship not in source: {', '.join(relationships)}"
        )
    former = ungrounded_former_official_claims(generated, source)
    if former:
        problems.append(
            f"'former' office-holder status not in source: {', '.join(former)}"
        )
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
    placeholders = placeholder_tokens(generated)
    if placeholders:
        problems.append(
            f"literal unfilled placeholder tokens ({', '.join(placeholders)})"
        )
    return problems
