"""Tests for post_composer — the extract-and-render redesign.

Each test names the published failure it makes unreachable. The point of
this module is that these are STRUCTURAL impossibilities, not patterns
being matched: see post_composer's own docstring for why the previous
filter-the-output approach could not hold.
"""

import pytest

from app.pipeline.analyze.post_composer import compose, headline_source

IRAN = (
    "Recent statements emphasize the need for an immediate conclusion to the border "
    "conflict to alleviate rising energy costs. Officials from Michigan and Iowa "
    "have called for measures such as temporary export restrictions."
)
ENDORSEMENT = (
    "VOTE JANE DOE!  I didn't know a thing about her until I saw her on "
    "the ballot. she is better for the state then the incumbent!"
)
REAL_NEWS = (
    "Democrat Pat Morgan loses to younger primary challenger in Connecticut. "
    "Morgan conceded the race on Tuesday night after trailing all evening."
)


class TestParaphraseIsImpossible:
    """"Border War Ends Quickly to Lower Prices" over a source that said
    officials had CALLED FOR an end. Asserting the event required
    inventing the word "Ends", and an invented word is not a span."""

    def test_an_invented_verb_cannot_be_published(self):
        assert compose("Border War", "Ends Quickly to Lower Prices", IRAN) is None

    def test_the_advocacy_the_source_actually_reported_survives(self):
        got = compose(
            "Officials from Michigan and Iowa",
            "have called for measures such as temporary export restrictions",
            IRAN,
        )
        assert got == (
            "Officials from Michigan and Iowa have called for measures such as "
            "temporary export restrictions."
        )

    def test_a_plausible_but_absent_claim_is_refused(self):
        assert compose("Pat Morgan", "won the election decisively", REAL_NEWS) is None


class TestEndorsementIsImpossible:
    """"VOTE JANE DOE!" — an imperative has no grammatical
    subject, so there is no actor to extract and nothing to compose."""

    def test_an_imperative_yields_no_actor(self):
        assert compose("", "VOTE JANE DOE", ENDORSEMENT) is None

    def test_an_imperative_smuggled_in_as_a_predicate_is_refused(self):
        """Both spans here are genuinely verbatim, so the verbatim rule
        alone does not catch it — a predicate that restates its own actor
        is malformed, and that is what disqualifies it."""
        assert compose("Jane Doe", "VOTE JANE DOE", ENDORSEMENT) is None


class TestEmptinessIsImpossible:
    """60 of 160 live posts said only that they were coverage. With no
    fact extracted there is simply no post."""

    @pytest.mark.parametrize("actor", ["This coverage", "The race", "It", "They"])
    def test_a_non_actor_opener_is_not_an_actor(self, actor):
        assert compose(actor, "tracks the TX-4 House race", "This coverage tracks the TX-4 House race") is None

    def test_no_extraction_means_no_post(self):
        # Whatever the source (a real story, or a listing with nobody doing
        # anything), empty spans are refused before the source is read.
        assert compose("", "", REAL_NEWS) is None


class TestWellFormedness:
    def test_a_real_fact_composes(self):
        assert compose(
            "Pat Morgan", "loses to younger primary challenger in Connecticut", REAL_NEWS
        ) == "Pat Morgan loses to younger primary challenger in Connecticut."

    def test_a_predicate_cut_before_its_object_is_refused(self):
        """Measured live: the model located the right span but stopped
        early, yielding "Alex Rivera takes a selfie with." — verbatim,
        grounded, and not a sentence."""
        src = ("From left, New Jersey Sen. Alex Rivera takes a selfie with Maryland "
               "Sens. Sam Lee and Dana Cruz.")
        assert compose("New Jersey Sen. Alex Rivera", "takes a selfie with", src) is None
        # The whole predicate composes. "Sens." must not read as the end of
        # the sentence — an earlier version truncated there and rejected
        # this valid composition.
        assert compose(
            "New Jersey Sen. Alex Rivera",
            "takes a selfie with Maryland Sens. Sam Lee and Dana Cruz",
            src,
        ) is not None

    def test_whitespace_and_smart_quotes_do_not_break_a_real_span(self):
        src = "Decision Desk HQ shifted the Florida governor’s race\n   to a toss-up."
        assert compose("Decision Desk HQ", "shifted the Florida governor's race to a toss-up", src)


class TestBothSpansMustBeAssertedTogether:
    """Two true fragments can make one false sentence.

    Verbatim-ness alone does NOT preserve who-did-what, which the first
    version of this module got wrong: both "Jordan Ellis" and "liable
    for fraud and negligence" are genuine spans of the source
    below, so checking them separately composed the plaintiff as the
    party found liable — reproducing issue #376 exactly, in the module
    written to make that class impossible.
    """

    LAWSUIT = (
        "A jury found Acme Corp liable for fraud and negligence in the "
        "case brought by Jordan Ellis. Ellis sued Acme Corp in 2022."
    )
    TESTIMONY = (
        "Sam Carter confirmed he would sit next to Chris Dale Jr. for testimony "
        "before Congress. Dale Jr. is the son of the governor."
    )

    def test_the_party_the_source_actually_names_composes(self):
        assert compose(
            "Acme Corp", "liable for fraud and negligence", self.LAWSUIT
        ) == "Acme Corp liable for fraud and negligence."

    def test_the_reversed_party_is_refused(self):
        assert compose(
            "Jordan Ellis", "liable for fraud and negligence", self.LAWSUIT
        ) is None

    def test_a_relationship_spliced_from_two_sentences_is_refused(self):
        """The real issue-748 failure: a published story called one
        public figure another's son. Both halves are verbatim and in the
        same article; they are not asserted of each other."""
        assert compose("Sam Carter", "is the son of the governor", self.TESTIMONY) is None

    def test_the_assertion_the_source_does_make_still_composes(self):
        assert compose(
            "Sam Carter", "confirmed he would sit next to Chris Dale Jr.", self.TESTIMONY
        ) is not None


class TestASpanMustRunToTheEndOfItsClause:
    """_DANGLING_TAIL catches a span cut before a preposition. It cannot
    catch one cut after a transitive verb — and that shipped. The Action
    Center published the fact "White House repeatedly violated." from a
    BBC headline reading "White House 'repeatedly violated' court order
    to restore press access". Verbatim, correctly attributed, and not a
    sentence: violated WHAT.
    """

    BBC = "White House 'repeatedly violated' court order to restore press access"

    def test_the_fragment_that_was_published_is_refused(self):
        assert compose("White House", "repeatedly violated", self.BBC) is None

    def test_the_full_assertion_from_the_same_headline_composes(self):
        assert compose(
            "White House", "repeatedly violated' court order to restore press access", self.BBC
        ) is not None

    @pytest.mark.parametrize("actor,predicate,source", [
        ("Prime Minister Ana Silva", "arrived in Washington",
         "Prime Minister Ana Silva arrived in Washington."),
        ("Judge", "orders White House to restore access to CNN, MS NOW and Politico",
         "Judge orders White House to restore access to CNN, MS NOW and Politico."),
        ("The Pentagon", "announced the plan",
         "The Pentagon announced the plan, which drew criticism from lawmakers."),
        ("Acme Corp", "liable for fraud and negligence",
         "A jury found Acme Corp liable for fraud and negligence in the case "
         "brought by Jordan Ellis."),
    ])
    def test_real_spans_still_compose(self, actor, predicate, source):
        """Including one ending at a comma — a clause boundary is a
        boundary, not only a full stop."""
        assert compose(actor, predicate, source) is not None


class TestHeadlineSource:
    """A headline is a sentence of its own. Joined to its summary with a
    bare newline it wasn't, and a claim ending at the headline's end read
    as cut off mid-clause (11 of 40 live articles, 2026-09-27)."""

    def test_claim_ending_at_the_headline_composes(self):
        src = headline_source(
            "Supreme Court rejects GOP-backed Missouri map",
            "Democrats hailed the ruling on Saturday.",
        )
        assert compose("Supreme Court", "rejects GOP-backed Missouri map", src)

    def test_actor_and_predicate_are_never_joined_across_the_headline(self):
        src = headline_source("Talks collapse in Geneva", "negotiators walked out of the room")
        assert compose("Geneva", "negotiators walked out of the room", src) is None

    def test_existing_terminal_punctuation_is_kept(self):
        assert headline_source("Is the outbreak slowing?", "Officials disagree.") == (
            "Is the outbreak slowing?\nOfficials disagree."
        )


class TestTheGapIsRenderedNotDropped:
    """Words between actor and predicate belong to the sentence. Dropping
    them published who-did-what wrong: "OpenAI agent made unauthorized
    attempts" composed as "OpenAI made unauthorized attempts" (live
    extraction, 2026-09-27)."""

    def test_a_gap_that_is_part_of_the_subject_is_kept(self):
        src = "OpenAI agent made unauthorized attempts to access federal agencies' websites"
        assert compose("OpenAI", "made unauthorized attempts to access federal agencies' websites", src) == (
            "OpenAI agent made unauthorized attempts to access federal agencies' websites."
        )

    def test_a_possessive_keeps_its_owner(self):
        src = "Morgan's lawyer argued the case was moot."
        assert compose("Morgan", "argued the case was moot", src) == "Morgan's lawyer argued the case was moot."

    def test_a_time_phrase_is_kept_verbatim(self):
        src = "Sen. Jamie Ortiz on Tuesday rejected the nominee."
        assert compose("Sen. Jamie Ortiz", "rejected the nominee", src) == (
            "Sen. Jamie Ortiz on Tuesday rejected the nominee."
        )


class TestATruncatedPredicateIsCompletedFromItsSource:
    """A live replay of 10 rejected claims found 7 refused because the model
    stopped its predicate span early ("sues", "grants review of"). The
    source still says the rest of the clause, so compose reads it on to the
    next clause punctuation — verbatim, never written — and refuses when it
    cannot tell where the clause ends.
    """

    def test_a_bare_verb_runs_to_the_end_of_its_headline(self):
        src = headline_source("Rivera sues the county clerk for withholding her voting records", None)
        assert compose("Rivera", "sues", src) == (
            "Rivera sues the county clerk for withholding her voting records."
        )

    def test_a_dangling_preposition_is_completed(self):
        title = ("Supreme Court grants review of the administration's mandatory "
                 "detention policy for immigrants")
        assert compose("Supreme Court", "grants review of", headline_source(title, None)) == f"{title}."

    def test_the_completion_stops_at_clause_punctuation(self):
        src = "Senate passes the funding bill, sending it to the House."
        assert compose("Senate", "passes", src) == "Senate passes the funding bill."

    def test_a_possible_abbreviation_refuses_rather_than_guesses(self):
        # "Sens." could end the sentence or not; the completion is refused
        # rather than truncated there.
        src = ("Alex Rivera takes a selfie with Maryland Sens. Sam Lee "
               "and Dana Cruz.")
        assert compose("Alex Rivera", "takes a selfie with", src) is None

    def test_a_predicate_in_a_later_sentence_is_still_refused(self):
        src = "Ellis spoke on Tuesday. The court sues nobody for anything at all."
        assert compose("Ellis", "sues", src) is None

    def test_a_clause_that_never_ends_within_reach_is_refused(self):
        src = "Governor signs " + " ".join(["word"] * 30) + "."
        assert compose("Governor", "signs", src) is None

    def test_no_boundary_at_all_is_refused_in_running_text(self):
        src = "The governor signs the order and then"
        assert compose("The governor", "signs", src) is None

    @pytest.mark.parametrize("source,expected", [
        ("Pentagon cuts $1.5 billion from the program.", "Pentagon cuts $1.5 billion from the program."),
        ("Pentagon cuts 1,000 jobs at the base.", "Pentagon cuts 1,000 jobs at the base."),
    ])
    def test_a_number_is_not_a_clause_end(self, source, expected):
        assert compose("Pentagon", "cuts", source) == expected

    def test_a_span_stopped_inside_a_number_is_refused(self):
        # Before: ".5 billion" read as the end of the clause, so the
        # model's span "cuts $1" composed as a fact with the wrong number.
        assert compose("Pentagon", "cuts funding by $1", "Pentagon cuts funding by $1.5 billion.") == (
            "Pentagon cuts funding by $1.5 billion."
        )
