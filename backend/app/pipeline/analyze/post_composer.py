"""Compose a post by EXTRACTING spans from a source, never by writing prose.

Why this exists rather than another output filter
-------------------------------------------------
The previous design asked a local model for one free-form sentence about
a source document and then tried to detect, afterwards, when that
sentence was unacceptable. Three separate failures on 2026-09-23 showed
why that cannot work:

  * A candidate's name in capitals after "VOTE", with a line saying she
    was better for the state than the incumbent — an endorsement,
    published by a non-partisan platform.
  * A headline saying a war "Ends Quickly" — the source said officials
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
  * Imperatives. "VOTE JANE DOE" has no grammatical subject,
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
    return _flatten(text).lower()


def _flatten(text: str) -> str:
    """_normalise without the lowercasing, for text that gets rendered."""
    swapped = (text or "").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", swapped).strip()


def headline_source(title: str | None, summary: str | None) -> str:
    """The source text for a headline and its summary, with the headline
    closed as the sentence it is.

    Headlines carry no closing period, so "title\nsummary" read as one
    sentence once whitespace was collapsed: a predicate ending exactly at
    the headline's end ran on into the summary's first word and failed
    _ends_at_clause_boundary, and an actor at the end of a headline could
    be joined to a predicate opening the summary. Measured on a live run
    (2026-09-27): 11 of 40 articles lost a claim that ended at its
    headline — a court ruling on a state's congressional map among them — and most
    clusters then fell short of the two claims an issue needs. The break
    is marked here rather than by treating every newline as a boundary,
    because a wrapped line inside one sentence is a real input too.
    """
    title = (title or "").strip()
    summary = (summary or "").strip()
    if title and title[-1] not in ".?!:":
        title += "."
    return f"{title}\n{summary}".strip()


def _is_verbatim(span: str, source: str) -> bool:
    return bool(span) and _normalise(span) in _normalise(source)


# How many words may sit between the actor and its predicate in the
# source and still count as the same assertion. Covers the reporting
# verb a lede normally puts there ("A jury FOUND the defendant liable")
# without reaching across a clause boundary into a different subject.
_MAX_GAP_WORDS = 3


def _asserted_together(actor: str, predicate: str, source: str) -> str | None:
    """The source's own text between ACTOR and PREDICATE where it says the
    predicate of the actor, else None.

    Checking each span verbatim but separately is not enough, and this
    is the exact hole that produced issue #376. Given

        "A jury found the company liable for fraud in the case
         brought by a former employee."

    both "a former employee" and "liable for fraud" are genuine
    verbatim spans, so a separate-span check happily composes
    "a former employee liable for fraud" —
    naming the plaintiff as the party found liable. Two true fragments,
    one false sentence.

    Requiring the predicate to FOLLOW the actor inside the same
    sentence, within a few words, is what makes the composition inherit
    the source's own direction instead of inventing one. It is also what
    stops a relationship being assembled out of unrelated halves, the
    class `ungrounded_relationship_claims` demonstrably misses (a
    published story made one public figure another's son because the
    two names sat in the same sentence).
    """
    found = _locate_assertion(actor, predicate, source)
    if found is None:
        return None
    # The gap is RETURNED so compose renders it. Dropping it changed who
    # acted: "OpenAI agent made unauthorized attempts" composed as "OpenAI
    # made unauthorized attempts" (live, 2026-09-27), and "the senator's
    # lawyer argued" would compose as "the senator argued". The published sentence is
    # now one contiguous span of the source.
    tail, hit = found
    return tail[:hit.start()]


def _locate_assertion(actor: str, predicate: str, source: str) -> tuple[str, re.Match] | None:
    """(the flattened source after ACTOR, the PREDICATE's match in it) at
    the first place the source says the predicate of the actor — the
    occurrence _asserted_together and _complete_predicate both read."""
    haystack = _flatten(source)
    needle_predicate = re.compile(re.escape(_flatten(predicate)), re.IGNORECASE)
    for match in re.finditer(re.escape(_flatten(actor)), haystack, re.IGNORECASE):
        tail = haystack[match.end():match.end() + 400]
        hit = needle_predicate.search(tail)
        if hit is None:
            continue
        # Only the GAP between them is constrained. The predicate itself
        # may legitimately contain a period — "takes a selfie with
        # Maryland Sens. Sam Lee and Dana Cruz" is one assertion, and an
        # earlier version of this check truncated the clause at the "."
        # in "Sens." and rejected it.
        gap = tail[:hit.start()]
        if len(gap.split()) > _MAX_GAP_WORDS:
            continue
        # A period inside the gap means the predicate belongs to the
        # NEXT sentence, whose subject is somebody else.
        if "." in gap:
            continue
        return tail, hit
    return None


def _looks_like_an_actor(span: str) -> bool:
    words = (span or "").split()
    if not words:
        return False
    # A leading determiner is only a problem when what follows is a
    # common noun — "The coverage", "The race". "The Senate", "The
    # Pentagon" and "The White House" are named parties and among the
    # most common actors in civic reporting; an earlier version of this
    # rule rejected every one of them, which a test written for a
    # different purpose surfaced.
    if words[0].lower() in _NON_ACTOR_OPENERS:
        rest = words[1:]
        if not rest or not rest[0][:1].isupper():
            return False
    # At least one capitalised token: a named party, not a common noun.
    return any(w[:1].isupper() for w in words)


# A predicate ending on one of these was cut mid-phrase: the model
# located the right span but stopped before its object. Measured live
# against real coverage, this produced "<a senator> takes a
# selfie with." — verbatim, grounded, and not a sentence.
_DANGLING_TAIL = frozenset({
    "with", "to", "of", "and", "or", "in", "for", "on", "by", "at",
    "from", "as", "that", "than", "into", "over", "after", "before",
    "about", "against", "between", "during", "the", "a", "an", "his",
    "her", "their", "its",
})


# Words that can begin a trailing modifier the span is allowed to omit.
_TRAILING_MODIFIER_OPENERS = frozenset({
    "in", "on", "at", "for", "by", "with", "from", "after", "before",
    "during", "of", "over", "under", "since", "while", "as", "and",
    "but", "which", "who", "that", "amid", "despite", "including",
})


# Punctuation that ends a clause. A "." or "," followed by a digit is
# inside a number — "$1.5 billion", "1,000 jobs" — and ending there would
# render "cuts $1." as the fact.
_CLAUSE_END = re.compile(r"[;:!?]|[.,](?!\d)")


def _ends_at_clause_boundary(predicate: str, source: str) -> bool:
    """True when the predicate runs to a natural break in the source.

    _DANGLING_TAIL catches a span cut before a preposition ("takes a
    selfie with"). It cannot catch one cut after a transitive verb, and
    that shipped: the Action Center published the fact "White House
    repeatedly violated." from a BBC headline reading "White House
    'repeatedly violated' court order to restore press access". Verbatim,
    correctly attributed, and not a sentence — violated WHAT.

    The general rule behind both cases is the same and does not need a
    word list: a well-formed span ends where the source's own clause
    ends. Anything still running when the span stops means the model cut
    it short.
    """
    haystack = _normalise(source)
    needle = _normalise(predicate)
    for match in re.finditer(re.escape(needle), haystack):
        rest = haystack[match.end():].lstrip(" '\"’”)")
        if not rest or _CLAUSE_END.match(rest):
            return True
        # A span may legitimately stop before a trailing modifier: the
        # source may run on "...liable for fraud IN THE CASE
        # brought by a former employee", and the shorter span
        # is a complete assertion. Requiring punctuation alone rejected
        # that — a real claim — so what follows is allowed to be a
        # preposition or conjunction starting a new phrase. It may not
        # be the object the predicate was still reaching for, which is
        # what "repeatedly violated" + "court order" is.
        nxt = rest.split(" ", 1)[0].strip(".,;:!?")
        if nxt in _TRAILING_MODIFIER_OPENERS:
            return True
    return False


# The longest run-on a completed predicate may take on: a headline's
# clause is a few words, a lede sentence rarely more than twenty. Longer,
# and the "clause" is more likely two run together than one.
_MAX_COMPLETION_WORDS = 25


def _complete_predicate(actor: str, predicate: str, source: str) -> str | None:
    """PREDICATE run on, in the source's own words, to where its clause ends.

    The model reliably finds who did it and the verb, and as reliably
    stops there: replayed on a live hour (2026-10-01), 7 of 10 located
    claims were rejected as too short or cut mid-phrase — a senator's
    surname + "sues" from a headline naming who was sued and why, "Supreme
    Court" + "grants review of" — though the prompt
    asks for the whole phrase. The rest of the phrase is right there,
    after the verb, in the same sentence the source says it of that
    actor (_locate_assertion), so it is copied, never written: the result
    is still one contiguous span of the source, and compose then puts it
    through every check a whole predicate gets.

    It stops at the first clause punctuation, so it can't run into the
    next clause or sentence; None when there is none within reach
    (_MAX_COMPLETION_WORDS) — a claim this can't complete is dropped, as
    before.
    """
    found = _locate_assertion(actor, predicate, source)
    if found is None:
        return None
    tail, hit = found
    rest = tail[hit.end():]
    end = _CLAUSE_END.search(rest)
    if end is None:
        # The 400-character window ran out before any punctuation: no
        # boundary seen, so nothing is assumed.
        return None
    completion = rest[:end.start()]
    if len(completion.split()) > _MAX_COMPLETION_WORDS:
        return None
    if end.group(0) == "." and _may_be_abbreviation((hit.group(0) + completion).split()[-1]):
        # "takes a selfie with Maryland Sens. Sam Lee": that
        # period ends an abbreviation, not the clause. Which one it is
        # can't be told from the text alone, so no completion is
        # attempted — the claim is dropped, as it was before.
        return None
    return (hit.group(0) + completion).strip()


def _may_be_abbreviation(word: str) -> bool:
    """A word a following period could be abbreviating: a short
    capitalised token ("Sens", "Dr", "Gov") or one already dotted
    ("D.C", "U.S")."""
    return "." in word or (word[:1].isupper() and len(word) <= 4)


def compose(actor: str, predicate: str, source: str) -> str | None:
    """A sentence built from two verbatim source spans, or None.

    None means "there is nothing here to say" and is a normal, healthy
    outcome — the caller must not post. Every rejection is fail-closed
    on purpose: this runs unsupervised against a public account.
    """
    actor = (actor or "").strip().strip(",;:")
    predicate = (predicate or "").strip().strip(",;:")

    if len(actor) < MIN_ACTOR_CHARS or not predicate:
        return None
    if not _looks_like_an_actor(actor):
        return None
    if not _is_verbatim(actor, source) or not _is_verbatim(predicate, source):
        return None
    # Both spans real is not enough — the source has to assert one OF the
    # other. See _asserted_together for the two-true-fragments-one-false-
    # sentence case this closes.
    gap = _asserted_together(actor, predicate, source)
    if gap is None:
        return None
    # A predicate cut short — a bare verb, a phrase stopped before its
    # object — is completed from the source (_complete_predicate) and
    # then held to every check below, like any other.
    tail_word = re.sub(r"[^\w]", "", predicate.split()[-1]).lower()
    if (
        len(predicate) < MIN_PREDICATE_CHARS or tail_word in _DANGLING_TAIL
        or not _ends_at_clause_boundary(predicate, source)
    ):
        predicate = _complete_predicate(actor, predicate, source) or predicate
    if len(predicate) < MIN_PREDICATE_CHARS:
        return None
    # A predicate that restates its own actor is malformed, not a fact:
    # "Jane Doe" + "VOTE JANE DOE" are both verbatim
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
    # sentence was "<senator> unveils a bill", giving "Sen. <senator>
    # unveiling legislation." An auxiliary ("is unveiling") is fine, so
    # only a LEADING -ing word is rejected.
    head = re.sub(r"[^\w]", "", predicate.split()[0]).lower()
    if head.endswith("ing"):
        return None
    if not _ends_at_clause_boundary(predicate, source):
        return None

    sentence = f"{actor}{gap or ' '}{predicate}"
    sentence = re.sub(r"\s+", " ", sentence).strip().rstrip(".")
    return sentence + "."
