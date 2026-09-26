"""A candidate list means something different before and after a primary.

`_candidate_source` already says WHAT a race's list is (confirmed /
nominees / primary / filers). Nothing said whether "filers" was still a
defensible answer, and the calendar is what decides that: before a
primary it is the honest best answer, because nobody knows the ballot
yet. After one, it means the ballot HAS been decided and this platform
does not have it — while the page goes on showing every FEC filer as
though the contest were open.

Measured across all 50 states on 2026-09-26: 39 had certified
candidates and ELEVEN were still on `filers` after their primary — Ohio
by 144 days, Louisiana 133, New York 95, one NY race listing 25 filers
for a ballot that holds about two.
"""

from datetime import datetime
from unittest.mock import patch

from app.api.elections import _ballot_basis


def _at(y, m, d):
    return patch("app.api.elections.utcnow", lambda: datetime(y, m, d))


def _races(*sources):
    return [{"candidateSource": s} for s in sources]


class TestFilersAfterAPrimaryAreSuperseded:
    def test_the_new_york_case(self):
        """Primary 95 days gone, still every FEC filer."""
        with _at(2026, 9, 26):
            b = _ballot_basis(_races("filers", "filers"), "2026-06-23")
        assert b["supersededByPrimary"] is True
        assert b["primaryPassed"] is True
        assert b["daysSincePrimary"] == 95

    def test_filers_before_the_primary_are_not_superseded(self):
        """The honest best answer — nobody knows the ballot yet."""
        with _at(2026, 9, 26):
            b = _ballot_basis(_races("filers"), "2026-12-01")
        assert b["supersededByPrimary"] is False
        assert b["primaryPassed"] is False
        assert b["daysSincePrimary"] is None

    def test_a_certified_list_is_never_superseded(self):
        """Georgia's primary is long past and that is fine — its ballot
        is certified, which is the whole point of the distinction."""
        with _at(2026, 9, 26):
            b = _ballot_basis(_races("confirmed", "confirmed"), "2026-05-19")
        assert b["supersededByPrimary"] is False
        assert b["basis"] == "confirmed"

    def test_on_the_primary_day_itself_nothing_is_superseded(self):
        with _at(2026, 6, 23):
            b = _ballot_basis(_races("filers"), "2026-06-23")
        assert b["primaryPassed"] is False
        assert b["supersededByPrimary"] is False

    def test_no_primary_date_claims_nothing(self):
        """A state whose source doesn't date itself gets no verdict
        rather than a guessed one."""
        b = _ballot_basis(_races("filers"), None)
        assert b["primaryPassed"] is None
        assert b["supersededByPrimary"] is False

    def test_an_unparseable_primary_date_claims_nothing(self):
        b = _ballot_basis(_races("filers"), "not-a-date")
        assert b["primaryPassed"] is None
        assert b["supersededByPrimary"] is False


class TestAStateIsOnlyAsCertainAsItsWeakestRace:
    def test_one_filers_race_drags_the_state_basis_down(self):
        """Saying "confirmed" while one contest is still guesswork is
        exactly what this prevents."""
        with _at(2026, 9, 26):
            b = _ballot_basis(_races("confirmed", "nominees", "filers"), "2026-06-23")
        assert b["basis"] == "filers"
        assert b["supersededByPrimary"] is True

    def test_nominees_beats_primary_beats_filers(self):
        with _at(2026, 9, 26):
            assert _ballot_basis(_races("nominees", "primary"), "2026-06-23")["basis"] == "primary"
            assert _ballot_basis(_races("confirmed", "nominees"), "2026-06-23")["basis"] == "nominees"

    def test_no_races_yields_no_basis(self):
        b = _ballot_basis([], "2026-06-23")
        assert b["basis"] is None
        assert b["supersededByPrimary"] is False
