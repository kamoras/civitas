"""Tests for promise_quality.clean_promises (platform-review O7).

_KEPT_RE/_BROKEN_RE count raw keyword occurrences with no notion of which
one the sentence is actually asserting. "Voted against the bill,
consistent with his promise to oppose it" hits both ("consistent" is a
KEPT word, "voted against" is a BROKEN word) and used to force a
correctly-KEPT promise about *opposing* something down to UNCLEAR. See
_STRONG_CONSISTENT_RE/_STRONG_INCONSISTENT_RE's definition for the fix
and why a full embedding rewrite wasn't pursued for this specific,
now-legacy code path.
"""

from app.models import PromiseAlignment
from app.pipeline.analyze.promise_quality import clean_promises


def _promise(**overrides):
    p = {
        "promiseText": "Oppose the widget tariff bill",
        "category": "TRADE",
        "alignment": PromiseAlignment.UNCLEAR,
        "relatedVotes": ["S.100"],
        "relatedBills": [],
        "analysis": "",
    }
    p.update(overrides)
    return p


class TestStrongConsistentFramingOverridesAmbiguity:
    def test_voted_against_consistent_with_promise_is_kept(self):
        """The exact O7 failure case: a correctly-KEPT promise to OPPOSE
        something used to fall to UNCLEAR because "voted against" (a
        BROKEN word) and "consistent" (a KEPT word) both matched."""
        result = clean_promises([_promise(
            alignment=PromiseAlignment.UNCLEAR,
            analysis="Voted against the bill (S.100), consistent with his promise to oppose the legislation.",
        )])
        assert result[0]["alignment"] == PromiseAlignment.KEPT

    def test_voted_for_inconsistent_with_promise_is_broken(self):
        result = clean_promises([_promise(
            alignment=PromiseAlignment.UNCLEAR,
            analysis="Voted in support of the bill (S.100), inconsistent with his promise to oppose the legislation.",
        )])
        assert result[0]["alignment"] == PromiseAlignment.BROKEN

    def test_ambiguous_text_without_a_strong_framing_still_falls_to_unclear(self):
        """No explicit "consistent with"/"inconsistent with" phrase — the
        pre-existing both-matched-keywords ambiguity resolution still
        applies unchanged."""
        result = clean_promises([_promise(
            alignment=PromiseAlignment.KEPT,
            analysis="Supported the measure (S.100) but also opposed a related amendment.",
        )])
        assert result[0]["alignment"] == PromiseAlignment.UNCLEAR


class TestKeptBrokenFlipsUnchanged:
    """Pre-existing behavior (unambiguous single-signal cases) must still work."""

    def test_broken_flips_to_kept_on_unambiguous_kept_language(self):
        result = clean_promises([_promise(
            alignment=PromiseAlignment.BROKEN,
            analysis="Voted yea on S.100, which aligns with and supports this promise.",
        )])
        assert result[0]["alignment"] == PromiseAlignment.KEPT

    def test_kept_flips_to_broken_on_unambiguous_broken_language(self):
        result = clean_promises([_promise(
            alignment=PromiseAlignment.KEPT,
            analysis="This vote on S.100 directly contradicts and undermines the promise.",
        )])
        assert result[0]["alignment"] == PromiseAlignment.BROKEN
