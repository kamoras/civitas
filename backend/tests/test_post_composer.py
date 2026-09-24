"""Tests for post_composer — the extract-and-render redesign.

Each test names the published failure it makes unreachable. The point of
this module is that these are STRUCTURAL impossibilities, not patterns
being matched: see post_composer's own docstring for why the previous
filter-the-output approach could not hold.
"""

import pytest

from app.pipeline.analyze.post_composer import compose

IRAN = (
    "Recent statements emphasize the need for an immediate conclusion to the Iran "
    "conflict to alleviate rising energy costs. Officials from Michigan and Iowa "
    "have called for measures such as temporary export restrictions."
)
ENDORSEMENT = (
    "VOTE VERONICA FERNANDEZ!  I didn't know a thing about her until I saw her on "
    "the ballot. she is better for Jersey then booker!"
)
REAL_NEWS = (
    "Democrat John Larson loses to younger primary challenger in Connecticut. "
    "Larson conceded the race on Tuesday night after trailing all evening."
)


class TestParaphraseIsImpossible:
    """"Iran War Ends Quickly to Lower Prices" over a source that said
    officials had CALLED FOR an end. Asserting the event required
    inventing the word "Ends", and an invented word is not a span."""

    def test_an_invented_verb_cannot_be_published(self):
        assert compose("Iran War", "Ends Quickly to Lower Prices", IRAN) is None

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
        assert compose("John Larson", "won the election decisively", REAL_NEWS) is None


class TestEndorsementIsImpossible:
    """"VOTE VERONICA FERNANDEZ!" — an imperative has no grammatical
    subject, so there is no actor to extract and nothing to compose."""

    def test_an_imperative_yields_no_actor(self):
        assert compose("", "VOTE VERONICA FERNANDEZ", ENDORSEMENT) is None

    def test_an_imperative_smuggled_in_as_a_predicate_is_refused(self):
        """Both spans here are genuinely verbatim, so the verbatim rule
        alone does not catch it — a predicate that restates its own actor
        is malformed, and that is what disqualifies it."""
        assert compose("Veronica Fernandez", "VOTE VERONICA FERNANDEZ", ENDORSEMENT) is None


class TestEmptinessIsImpossible:
    """60 of 160 live posts said only that they were coverage. With no
    fact extracted there is simply no post."""

    @pytest.mark.parametrize("actor", ["This coverage", "The race", "It", "They"])
    def test_a_non_actor_opener_is_not_an_actor(self, actor):
        assert compose(actor, "tracks the TX-4 House race", "This coverage tracks the TX-4 House race") is None

    def test_no_extraction_means_no_post(self):
        assert compose("", "", REAL_NEWS) is None

    def test_a_listing_with_nobody_doing_anything_yields_nothing(self):
        src = "The (movie) Reservoir Dogs 4K (iTunes)(CA) C$4.99 (Crime) (iMDB) 8.3"
        assert compose("", "", src) is None


class TestWellFormedness:
    def test_a_real_fact_composes(self):
        assert compose(
            "John Larson", "loses to younger primary challenger in Connecticut", REAL_NEWS
        ) == "John Larson loses to younger primary challenger in Connecticut."

    def test_a_predicate_cut_before_its_object_is_refused(self):
        """Measured live: the model located the right span but stopped
        early, yielding "Cory Booker takes a selfie with." — verbatim,
        grounded, and not a sentence."""
        src = ("From left, New Jersey Sen. Cory Booker takes a selfie with Maryland "
               "Sens. Chris Van Hollen and Angela Alsobrooks.")
        assert compose("New Jersey Sen. Cory Booker", "takes a selfie with", src) is None
        assert compose(
            "New Jersey Sen. Cory Booker",
            "takes a selfie with Maryland Sens. Chris Van Hollen and Angela Alsobrooks",
            src,
        ) is not None

    def test_whitespace_and_smart_quotes_do_not_break_a_real_span(self):
        src = "Decision Desk HQ shifted the Florida governor’s race\n   to a toss-up."
        assert compose("Decision Desk HQ", "shifted the Florida governor's race to a toss-up", src)


class TestBothSpansMustBeAssertedTogether:
    """Two true fragments can make one false sentence.

    Verbatim-ness alone does NOT preserve who-did-what, which the first
    version of this module got wrong: both "E. Jean Carroll" and "liable
    for sexual abuse and defamation" are genuine spans of the source
    below, so checking them separately composed the plaintiff as the
    party found liable — reproducing issue #376 exactly, in the module
    written to make that class impossible.
    """

    CARROLL = (
        "A jury found Donald Trump liable for sexual abuse and defamation in the "
        "case brought by E. Jean Carroll. Carroll sued Trump in 2022."
    )
    BIDEN = (
        "Hunter Biden confirmed he would sit next to Donald Trump Jr. for testimony "
        "before Congress. Trump Jr. is the son of the president."
    )

    def test_the_party_the_source_actually_names_composes(self):
        assert compose(
            "Donald Trump", "liable for sexual abuse and defamation", self.CARROLL
        ) == "Donald Trump liable for sexual abuse and defamation."

    def test_the_reversed_party_is_refused(self):
        assert compose(
            "E. Jean Carroll", "liable for sexual abuse and defamation", self.CARROLL
        ) is None

    def test_a_relationship_spliced_from_two_sentences_is_refused(self):
        """The real issue-748 failure: a published story called Donald
        Trump Jr. Hunter Biden's son. Both halves are verbatim and in the
        same article; they are not asserted of each other."""
        assert compose("Hunter Biden", "is the son of the president", self.BIDEN) is None

    def test_the_assertion_the_source_does_make_still_composes(self):
        assert compose(
            "Hunter Biden", "confirmed he would sit next to Donald Trump Jr.", self.BIDEN
        ) is not None

    def test_an_abbreviation_period_inside_the_predicate_is_not_a_boundary(self):
        """"Sens." must not read as the end of the sentence — an earlier
        version truncated there and rejected a valid composition."""
        src = ("From left, New Jersey Sen. Cory Booker takes a selfie with Maryland "
               "Sens. Chris Van Hollen and Angela Alsobrooks.")
        assert compose(
            "New Jersey Sen. Cory Booker",
            "takes a selfie with Maryland Sens. Chris Van Hollen and Angela Alsobrooks",
            src,
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
        ("Chinese President Xi Jinping", "arrived in Washington",
         "Chinese President Xi Jinping arrived in Washington."),
        ("Judge", "orders White House to restore access to CNN, MS NOW and Politico",
         "Judge orders White House to restore access to CNN, MS NOW and Politico."),
        ("The Pentagon", "announced the plan",
         "The Pentagon announced the plan, which drew criticism from lawmakers."),
        ("Donald Trump", "liable for sexual abuse and defamation",
         "A jury found Donald Trump liable for sexual abuse and defamation in the case "
         "brought by E. Jean Carroll."),
    ])
    def test_real_spans_still_compose(self, actor, predicate, source):
        """Including one ending at a comma — a clause boundary is a
        boundary, not only a full stop."""
        assert compose(actor, predicate, source) is not None
