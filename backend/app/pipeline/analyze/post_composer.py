"""Compose a post by EXTRACTING spans from a source, never by writing prose.

Why this exists rather than another output filter
-------------------------------------------------
The previous design asked a local model for one free-form sentence about
a source document and then tried to detect, afterwards, when that
sentence was unacceptable. Three separate failures on 2026-09-23 showed
why that cannot work:

  * "VOTE VERONICA FERNANDEZ! ... She's better for Jersey than Booker!"
    — an endorsement, published by a non-partisan platform.
  * "Iran War Ends Quickly to Lower Prices" — the source said officials
    had CALLED FOR an end. The war had not ended.
  * "This coverage tracks the TX-4 House race and related election
    developments." — true, grounded, and empty. 60 of 160 race posts
    were this shape.

Each was patched with a hand-written pattern (an endorsement phrase
list, a completed-event verb list, a vacuous-template matcher). That is
a finite blocklist policing an unbounded generative space: every new
phrasing needs a new pattern, and the patterns are exactly the
hand-typed tunables this codebase otherwise refuses (see
calibrate_ranking.py — "nothing in explore_ranking.json is typed by a
human").

The fix is to remove the freedom rather than police it. The model is
asked only to LOCATE two spans in the source; this module checks both
appear verbatim and renders the sentence itself. What that makes
impossible, structurally rather than by detection:

  * Paraphrase. "need for an immediate conclusion" cannot become
    "Ends", because "Ends" is not a span of the source. The Iran class
    of error requires inventing a word, and no invented word survives
    the verbatim check.
  * Imperatives. "VOTE VERONICA FERNANDEZ" has no grammatical subject,
    so there is no actor span to extract, so nothing is composed. An
    endorsement cannot be phrased as actor + predicate.
  * Emptiness. No actor and predicate means no fact, which means no
    post — instead of a sentence whose only content is that coverage
    exists. The pipeline is no longer obliged to say something about
    every eligible item.

The model still chooses WHAT is newsworthy, which is the judgement it is
actually good at. It no longer chooses the words, which is where it was
failing.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Spans shorter than this are not facts — a two-word predicate ("was
# elected") carries no object, and a one-word actor is usually a bare
# surname the source never actually attributes anything to.
MIN_ACTOR_CHARS = 3
MIN_PREDICATE_CHARS = 12

# An actor must read as a named party — a person, body or organisation
# the source names. Anything starting with a pronoun or bare determiner
# is the model summarising rather than pointing at someone ("This
# coverage", "The race", "It"), which is exactly the empty-post shape.
_NON_ACTOR_OPENERS = frozenset({
    "this", "that", "these", "those", "it", "they", "he", "she", "we",
    "there", "the", "a", "an", "his", "her", "their", "its", "coverage",
    "race", "update", "piece", "article", "report", "story", "analysis",
})


def _normalise(text: str) -> str:
    """Collapse whitespace and case so a span matches its source despite
    line wrapping, smart quotes and capitalisation drift. Punctuation is
    kept: dropping it would let "Collins, rejected" match "Collins
    rejected" and admit a relationship the source never stated."""
    swapped = (text or "").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", swapped).strip().lower()


def _is_verbatim(span: str, source: str) -> bool:
    return bool(span) and _normalise(span) in _normalise(source)


# How many words may sit between the actor and its predicate in the
# source and still count as the same assertion. Covers the reporting
# verb a lede normally puts there ("A jury FOUND Donald Trump liable")
# without reaching across a clause boundary into a different subject.
_MAX_GAP_WORDS = 3


def _asserted_together(actor: str, predicate: str, source: str) -> bool:
    """True only where the source says the PREDICATE of the ACTOR.

    Checking each span verbatim but separately is not enough, and this
    is the exact hole that produced issue #376. Given

        "A jury found Donald Trump liable for sexual abuse and
         defamation in the case brought by E. Jean Carroll."

    both "E. Jean Carroll" and "liable for sexual abuse and defamation"
    are genuine verbatim spans, so a separate-span check happily
    composes "E. Jean Carroll liable for sexual abuse and defamation" —
    naming the plaintiff as the party found liable. Two true fragments,
    one false sentence.

    Requiring the predicate to FOLLOW the actor inside the same
    sentence, within a few words, is what makes the composition inherit
    the source's own direction instead of inventing one. It is also what
    stops a relationship being assembled out of unrelated halves, the
    class `ungrounded_relationship_claims` demonstrably misses (a
    published story called Donald Trump Jr. Hunter Biden's son).
    """
    haystack = _normalise(source)
    needle_actor = _normalise(actor)
    needle_predicate = _normalise(predicate)
    for match in re.finditer(re.escape(needle_actor), haystack):
        tail = haystack[match.end():match.end() + 400]
        found = tail.find(needle_predicate)
        if found == -1:
            continue
        # Only the GAP between them is constrained. The predicate itself
        # may legitimately contain a period — "takes a selfie with
        # Maryland Sens. Chris Van Hollen" is one assertion, and an
        # earlier version of this check truncated the clause at the "."
        # in "Sens." and rejected it.
        gap = tail[:found]
        if len(gap.split()) > _MAX_GAP_WORDS:
            continue
        # A period inside the gap means the predicate belongs to the
        # NEXT sentence, whose subject is somebody else.
        if "." in gap:
            continue
        return True
    return False


def _looks_like_an_actor(span: str) -> bool:
    words = (span or "").split()
    if not words:
        return False
    if words[0].lower() in _NON_ACTOR_OPENERS:
        return False
    # At least one capitalised token: a named party, not a common noun.
    return any(w[:1].isupper() for w in words)


# A predicate ending on one of these was cut mid-phrase: the model
# located the right span but stopped before its object. Measured live
# against real coverage, this produced "New Jersey Sen. Cory Booker
# takes a selfie with." — verbatim, grounded, and not a sentence.
_DANGLING_TAIL = frozenset({
    "with", "to", "of", "and", "or", "in", "for", "on", "by", "at",
    "from", "as", "that", "than", "into", "over", "after", "before",
    "about", "against", "between", "during", "the", "a", "an", "his",
    "her", "their", "its",
})


def compose(actor: str, predicate: str, source: str) -> str | None:
    """A sentence built from two verbatim source spans, or None.

    None means "there is nothing here to say" and is a normal, healthy
    outcome — the caller must not post. Every rejection is fail-closed
    on purpose: this runs unsupervised against a public account.
    """
    actor = (actor or "").strip().strip(",;:")
    predicate = (predicate or "").strip().strip(",;:")

    if len(actor) < MIN_ACTOR_CHARS or len(predicate) < MIN_PREDICATE_CHARS:
        return None
    if not _looks_like_an_actor(actor):
        return None
    if not _is_verbatim(actor, source) or not _is_verbatim(predicate, source):
        return None
    # Both spans real is not enough — the source has to assert one OF the
    # other. See _asserted_together for the two-true-fragments-one-false-
    # sentence case this closes.
    if not _asserted_together(actor, predicate, source):
        return None
    # A predicate that restates its own actor is malformed, not a fact:
    # "Veronica Fernandez" + "VOTE VERONICA FERNANDEZ" are both verbatim
    # spans of a real source, and concatenating them yields an imperative
    # wearing a subject. A well-formed predicate says what the actor did
    # WITHOUT naming them again, so containment anywhere — not just at
    # the start — disqualifies it.
    if _normalise(actor) in _normalise(predicate):
        return None
    tail = re.sub(r"[^\w]", "", predicate.split()[-1]).lower()
    if tail in _DANGLING_TAIL:
        return None
    # A bare participle is a fragment, not a clause: measured live, the
    # model returned "unveiling legislation" for a source whose own
    # sentence was "Sanders unveils a bill", giving "Sen. Bernie Sanders
    # unveiling legislation." An auxiliary ("is unveiling") is fine, so
    # only a LEADING -ing word is rejected.
    head = re.sub(r"[^\w]", "", predicate.split()[0]).lower()
    if head.endswith("ing"):
        return None

    sentence = f"{actor} {predicate}"
    sentence = re.sub(r"\s+", " ", sentence).strip().rstrip(".")
    return sentence + "."
