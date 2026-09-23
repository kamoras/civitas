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
