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

import functools
import hashlib
import json
import logging
import pathlib
import re

import numpy as np

from app.pipeline import vector_store

logger = logging.getLogger(__name__)

# Spans shorter than this are not facts — a two-word predicate ("was
# elected") carries no object, and a one-word actor is usually a bare
# surname the source never actually attributes anything to.
MIN_ACTOR_CHARS = 3
MIN_PREDICATE_CHARS = 12

# Whether a span names a party that can act — a person, institution,
# official body or group of officials — is decided in two tiers
# (is_acting_party).
#
# 1. The rule below: one capitalised word, and no leading determiner or
#    pronoun before a lowercase word. It admits named parties. On its own it
#    also refused every party a story DESCRIBES rather than names — "a
#    federal judge", "federal agents", "a bipartisan group of senators": 33
#    of the 136 claims compose() rejected in a live sample (2026-10-11..15).
# 2. What the rule refuses gets a similarity-weighted vote among its nearest
#    hand-labelled spans (app/data/claim_actor_examples.json, labelled per
#    its _rubric; kNN, Cover & Hart 1967) on the similarity model, with k
#    and the threshold fitted by scripts/calibrate_claim_actor.py
#    (claim_actor_calibration.json) under one hard constraint: leave one
#    out, it admits no labelled non-actor the rule refused, so the two tiers
#    are at least as precise as the rule was alone.
#
#    Measured leave one out on the labelled set (970 spans): precision 0.80
#    against the rule's 0.77, recall 0.85 against 0.71; on the replayed
#    production spans alone 0.89 against 0.88, and 0.96 against 0.86.
#    Replayed through compose() on the 264 recorded claim calls, each span's
#    vote leaving its own spelling out, 15 of the rule's 33 rejections
#    compose and nothing the rule admitted is lost; every new claim names
#    the party its source says acted, in the same clause.
#
# The vote does not replace the rule, because measured alone it cannot. The
# best vote-only setting meeting the same constraint refuses 233 of the 401
# labelled actors the rule admits — the feed's most frequent actor among
# them — and on the same replay it composed 82 claims to the rule's 128
# (13 gained, 59 lost). The calibration file's "vote_alone" holds that
# measurement. Named parties are exactly where a short span's embedding says
# least: a surname, an acronym or an organisation called "Common Cause" sits
# nearer the things it does than the people who do them.
_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / "data"
ACTOR_EXAMPLES_PATH = _DATA_DIR / "claim_actor_examples.json"
ACTOR_CALIBRATION_PATH = _DATA_DIR / "claim_actor_calibration.json"
_actor_examples_cache: list[dict] | None = None
_actor_calibration_cache: dict | None = None
_actor_reference_cache: dict[int, np.ndarray] = {}


def actor_examples() -> list[dict]:
    global _actor_examples_cache
    if _actor_examples_cache is None:
        _actor_examples_cache = json.loads(ACTOR_EXAMPLES_PATH.read_text())["examples"]
    return _actor_examples_cache


def actor_calibration() -> dict:
    global _actor_calibration_cache
    if _actor_calibration_cache is None:
        _actor_calibration_cache = json.loads(ACTOR_CALIBRATION_PATH.read_text())
    return _actor_calibration_cache


def actor_examples_hash() -> str:
    """Identifies the labelled set a calibration was fitted to."""
    payload = json.dumps([[e["text"], e["actor"]] for e in actor_examples()])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def embed_spans(spans: list[str]) -> np.ndarray:
    return vector_store.get_similarity_model().encode(spans, show_progress_bar=False, normalize_embeddings=True)


def actor_votes(span_embeddings: np.ndarray, reference: np.ndarray, labels: np.ndarray, k: int) -> np.ndarray:
    """Similarity-weighted share of actors among each span's k nearest
    labelled spans (unit vectors, so the dot product is the cosine).
    Negative similarities weigh nothing."""
    sims = span_embeddings @ reference.T
    k = min(k, reference.shape[0])
    nearest = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    weights = np.take_along_axis(sims, nearest, axis=1).clip(min=0.0)
    return (weights * labels[nearest]).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)


def _actor_reference() -> np.ndarray:
    """The labelled spans' embeddings, once per similarity model."""
    key = id(vector_store.get_similarity_model())
    if key not in _actor_reference_cache:
        _actor_reference_cache[key] = embed_spans([e["text"] for e in actor_examples()])
    return _actor_reference_cache[key]


def is_acting_party(span: str) -> bool:
    """True when SPAN names or describes a party that can act or speak:
    the rule's verdict, or else — unless a pronoun leads it — the labelled
    spans' vote."""
    if _looks_like_an_actor(span):
        return True
    if _names_no_one(span):
        return False
    try:
        return _actor_vote(span) >= actor_calibration()["threshold"]
    except Exception:
        # The vote only ever admits what the rule refused, so failing closed
        # leaves the rule's verdict: the claim is refused, not the run.
        logger.exception("Actor vote failed for %r — refused, as the rule alone would", span)
        return False


@functools.lru_cache(maxsize=4096)
def _actor_vote(span: str) -> float:
    """Cached: a claim's spans are composed more than once a run (the call
    that records the model's answer, then extraction)."""
    labels = np.array([float(e["actor"]) for e in actor_examples()])
    return float(actor_votes(embed_spans([span]), _actor_reference(), labels, actor_calibration()["k"])[0])


# Tier 1 of is_acting_party. Anything starting with a pronoun or bare
# determiner and going on in lowercase is the model summarising rather than
# pointing at someone ("This coverage", "The race", "It") — the empty-post
# shape — unless the vote finds a party there ("a federal judge").
#
# A pronoun-led span never goes to the vote. Grammatical function words, not
# a classification (as _RELATIVE_PRONOUNS below): "its spokesman" or "their
# lawyers" points at a party only through someone the span doesn't name, so
# the rendered claim could not say whose. The embedding can't see that — it
# reads "spokesman" and votes actor (0.89 left out, higher than any other
# non-actor the rule refuses; claim_actor_examples.json labels them
# non-actors per its rubric).
_PRONOUN_OPENERS = frozenset({
    "this", "that", "these", "those", "it", "they", "he", "she", "we",
    "there", "his", "her", "their", "its",
})
_NON_ACTOR_OPENERS = _PRONOUN_OPENERS | {
    "the", "a", "an", "coverage",
    "race", "update", "piece", "article", "report", "story", "analysis",
}


def _names_no_one(span: str) -> bool:
    words = (span or "").split()
    return not words or words[0].lower() in _PRONOUN_OPENERS


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


_QUOTE_MARK = re.compile(r"""(['"])""")


def _source_spelling(span: str, source: str) -> str:
    """SPAN as the source spells it, where the two differ only in quotation
    marks; otherwise SPAN unchanged.

    The model answers in JSON, where a double quotation mark has to be
    escaped, so it copies a quoted phrase with single ones — and drops the
    period or comma American style puts inside the closing mark. The source's
    'called the committee “demonstrably biased.”' came back as "called the
    committee 'demonstrably biased'" and failed the verbatim check, though
    the model had found exactly the right words: 6 of 160 rejected claims in
    a live sample (2026-10-11..15). Only the marks are matched loosely; every
    other character must still be the source's.

    What is returned is the SOURCE's text, so everything after this checks
    and renders a span of the source, as before. A closing mark at the end
    of the span is left out of it, with the punctuation before it, and the
    sentence is closed from the source by _close_quotation — the path a
    span ending inside a quotation always takes.
    """
    if not span or _is_verbatim(span, source):
        return span
    pieces = _QUOTE_MARK.split(_flatten(span))
    pattern = ""
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            pattern += re.escape(piece)
        elif i == len(pieces) - 2 and not pieces[-1]:
            pattern += r"""(?=[.,!?]?['"])"""
        else:
            pattern += r"""[.,!?]?['"]"""
    match = re.search(pattern, _flatten(source), re.IGNORECASE)
    return match.group(0) if match else span


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
        # NEXT sentence, whose subject is somebody else. One inside a
        # parenthetical doesn't end anything: "Sen. Jane Roe (D-Conn.)
        # said" and "Sen. Elizabeth Warren (Mass.) on Wednesday blasted"
        # are one sentence each, and the house style of the outlets most
        # of this coverage comes from. 20 of 160 rejected claims in a live
        # sample (2026-10-11..15) were lost to that dot alone.
        if "." in _PARENTHETICAL.sub("", gap):
            continue
        # A relative pronoun in the gap means the predicate is a relative
        # clause hung on the actor, not the source's main assertion, and
        # the span reads as a fragment: the Action Center published the
        # summary "Christa Pike, who survived despite being subject to two
        # doses of lethal injection." (live, 2026-10-03).
        if _RELATIVE_PRONOUNS & {w.strip(",;:'\"’”").lower() for w in gap.split()}:
            continue
        # The actor as the object of a preposition is not who acted: "The
        # botched execution of Christa Pike is renewing scrutiny" composed
        # as "Christa Pike is renewing scrutiny..." (live, 2026-10-03),
        # making the woman executed the one renewing it.
        before = haystack[:match.start()].split()
        if before and before[-1].strip(",;:'\"‘“(").lower() in _OBJECT_PREPOSITIONS:
            continue
        # The actor inside a quotation is the speaker's assertion, not the
        # outlet's: "'...our media vendor mistakenly placed $25,000 in
        # advertising...,' a spokesperson said" composed as "Media vendor
        # mistakenly placed $25,000..." — a party's own account of its
        # mistake, published as fact with its speaker gone (replay of
        # 2026-10-11..15). And an actor straight after a quotation closed on
        # a comma is the quotation's attribution: its predicate is "said in
        # a statement" and what was said is behind it ("'...,' House
        # Democratic leaders said in a statement."). Marks are counted after
        # _flatten, which makes every double mark a straight one; single
        # marks are left out, since an apostrophe is one too.
        opening = haystack[:match.start()]
        if opening.count('"') % 2 or re.search(r"""[,!?]"\s*$""", opening):
            continue
        return tail, hit
    return None


# A bracketed aside with no bracket inside it: "(D-Conn.)", "(Mass.)".
_PARENTHETICAL = re.compile(r"\([^()]*\)")


# Grammatical function words, not a classification: what they mark is
# sentence structure (_locate_assertion).
# "that" is left out: after a reporting verb it opens a complement
# ("said that"), which is the main assertion.
_RELATIVE_PRONOUNS = frozenset({"who", "whom", "whose", "which"})
_OBJECT_PREPOSITIONS = frozenset({
    "of", "by", "for", "with", "against", "to", "from", "on", "at", "in",
    "about", "over", "under", "between", "toward", "towards", "into",
    "including", "than", "behind", "without", "upon",
})


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
# render "cuts $1." as the fact. A "." is a sentence end only where the
# next thing is not a lowercase word or more of the same token: "U.S.
# energy infrastructure" and "a D.C. judge" run on. Treating those dots as
# ends made the completion below give up on "will invest up to $200
# billion in U.S. energy infrastructure" (live, 2026-09-30), and the
# claim went out as "...will invest up to $200 billion." with what it
# was invested in gone.
_CLAUSE_END = re.compile(r"[;:!?]|,(?!\d)|\.(?=\s*$|\s+[^a-z\s]|[\"'’”)\]])")


def _ends_at_clause_boundary(
    predicate: str, source: str, allow_trailing_modifier: bool = True,
) -> bool:
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
    # Case kept: whether a "." ends the sentence depends on whether the
    # next word is capitalised (_CLAUSE_END).
    haystack = _flatten(source)
    needle = _flatten(predicate)
    for match in re.finditer(re.escape(needle), haystack, re.IGNORECASE):
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
        nxt = rest.split(" ", 1)[0].strip(".,;:!?").lower()
        if allow_trailing_modifier and nxt in _TRAILING_MODIFIER_OPENERS:
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
    # A clause end inside a bracket the span opened is not the clause's
    # end: "berated Sen. Kim Lee (R-Ark.) over the vote" stopped at the
    # "." before ")" and composed "...Sen. Kim Lee (R-Ark." (2026-10-08).
    while _unbalanced(hit.group(0) + rest[:end.start()]):
        end = _CLAUSE_END.search(rest, end.end())
        if end is None:
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


def _unbalanced(text: str) -> bool:
    """True when TEXT opens or closes a parenthesis or bracket it doesn't
    match: a span cut inside a parenthetical, or begun inside one."""
    return text.count("(") != text.count(")") or text.count("[") != text.count("]")


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
    # A trailing period is the sentence's own end, copied with the span. Left
    # on, the span read as stopping short of a clause end (the next thing in
    # the source is the next sentence), so it was "completed" into that
    # sentence: "...for violating sanctions laws. The Treasury's Financial
    # Crimes Enforcement Network (FinCEN) fined Bank of America." (replay of
    # 2026-10-11..15). Without it, the boundary check finds the period.
    predicate = (predicate or "").strip().strip(",;:").rstrip(".")

    if len(actor) < MIN_ACTOR_CHARS or not predicate:
        return None
    actor = _source_spelling(actor, source)
    predicate = _source_spelling(predicate, source)
    if not _is_verbatim(actor, source) or not _is_verbatim(predicate, source):
        return None
    if not is_acting_party(actor):
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
    # Tried whenever the span stops short of the source's own punctuation,
    # not only when it stops somewhere ungrammatical: a span stopped
    # before a trailing modifier is a clause, but the modifier may be the
    # part that says what happened ("will invest up to $200 billion" /
    # "in U.S. energy infrastructure"). When the clause can't be
    # completed, the shorter span is still judged by the boundary rule.
    tail_word = re.sub(r"[^\w]", "", predicate.split()[-1]).lower()
    if (
        len(predicate) < MIN_PREDICATE_CHARS or tail_word in _DANGLING_TAIL
        or not _ends_at_clause_boundary(predicate, source, allow_trailing_modifier=False)
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
    # An actor named inside a parenthetical leaves its ")" in the sentence:
    # "Food and Drug Administration (FDA) won't publish" located as "FDA" +
    # "won't publish" composed "FDA) won't publish..." (2026-10-08). The
    # subject is the words before the bracket, which the span left out.
    if _unbalanced(sentence):
        return None
    # The span can open mid-sentence ("...but the Senate passes the bill",
    # "A federal court" located without its article); the rendered
    # sentence starts with a capital like any other. Case only — the
    # verbatim check is case-blind already (_normalise).
    sentence = sentence[:1].upper() + sentence[1:]
    return _close_quotation(sentence, predicate, source)


_OPEN_QUOTES = ('"', "\u201c")


def _close_quotation(sentence: str, predicate: str, source: str) -> str | None:
    """`sentence` ended with a period, and with the closing quotation mark
    the source puts after the span when the span opened a quotation it
    didn't close; None when the source closes it nowhere there. A span that
    ends at the quoted sentence's own period leaves its mark behind:
    published 2026-10, 'it "looked like a public service announcement.'

    A comma before the closing mark is the source's sentence running on,
    so the rendered sentence still ends with a period, as a span ending at
    a comma outside quotes does: 'called the candidate "terrible,"' went
    out as an election post with no end (2026-10-08)."""
    marks = sum(sentence.count(q) for q in ('"', "\u201c", "\u201d"))
    if marks % 2 == 0:
        closed = re.search(r"([.,!?])([\"”])$", sentence)
        if closed:
            end = "." if closed.group(1) == "," else closed.group(1)
            return f"{sentence[:closed.start()]}{end}{closed.group(2)}"
        return sentence + "."
    haystack, needle = _flatten(source), _flatten(predicate)
    for match in re.finditer(re.escape(needle), haystack, re.IGNORECASE):
        closing = re.match(r"\s*([.,!?]?)\s*([\"\u201d])", haystack[match.end():])
        if closing:
            end = closing.group(1) if closing.group(1) in ("!", "?") else "."
            return f"{sentence.rstrip(',')}{end}{closing.group(2)}"
    return None
