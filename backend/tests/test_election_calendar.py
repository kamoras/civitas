"""Tests for election_calendar — the Senate three-class rotation and its
mapping to election years. These sets are the authoritative cross-check
_sync_roster uses to label special elections, so getting a class roster
wrong silently mislabels real races; the structural invariants below
(every state covered by the rotation) catch a typo'd roster.
"""

from datetime import date

from app.election_calendar import (
    CLASS_I_STATES,
    CLASS_II_STATES,
    CLASS_III_STATES,
    next_election_day,
    seats_up_for_year,
)

ALL_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})


class TestSeatsUpForYear:
    def test_2026_is_class_ii(self):
        assert seats_up_for_year(2026) == CLASS_II_STATES

    def test_2028_is_class_iii(self):
        assert seats_up_for_year(2028) == CLASS_III_STATES

    def test_2030_is_class_i(self):
        assert seats_up_for_year(2030) == CLASS_I_STATES

    def test_odd_year_has_no_regular_seats(self):
        assert seats_up_for_year(2027) == frozenset()


class TestClassRosters:
    def test_union_of_classes_is_exactly_the_fifty_states(self):
        # Every state elects senators, and every senator belongs to exactly
        # one class — so the three sets must cover all 50 states, no more.
        assert CLASS_I_STATES | CLASS_II_STATES | CLASS_III_STATES == ALL_STATES

    def test_class_sizes_are_33_33_34(self):
        """The constitutional split of 100 seats. This exact test would
        have caught the AR omission from Class III that shipped in
        api/action.py's original hand-typed rosters (2026-07): the union
        check alone can't, because a state missing from one of its TWO
        classes still appears in the union via the other."""
        assert len(CLASS_I_STATES) == 33
        assert len(CLASS_II_STATES) == 33
        assert len(CLASS_III_STATES) == 34

    def test_every_state_appears_in_exactly_two_classes(self):
        # Two senators per state, one class each, never the same class —
        # so each state must appear in exactly two of the three sets.
        for st in ALL_STATES:
            count = sum(st in c for c in (CLASS_I_STATES, CLASS_II_STATES, CLASS_III_STATES))
            assert count == 2, f"{st} appears in {count} classes"

    def test_fl_and_oh_have_no_regular_2026_seat(self):
        """Load-bearing for the 2026 cycle: FL and OH are not Class II, so
        their 2026 Senate races can only be specials — the exact fact
        _sync_roster's special-election derivation rests on."""
        assert "FL" not in CLASS_II_STATES
        assert "OH" not in CLASS_II_STATES


class TestNextElectionDay:
    def test_2026_election_day_is_november_3rd(self):
        assert next_election_day(date(2026, 1, 1)) == date(2026, 11, 3)

    def test_day_before_election_day_still_returns_same_year(self):
        # Regression: a `year = after.year + 1` short-circuit for any
        # November date used to skip past the current year's own election
        # day whenever `after` landed a day or two before it.
        assert next_election_day(date(2026, 11, 2)) == date(2026, 11, 3)

    def test_election_day_itself_rolls_to_next_cycle(self):
        assert next_election_day(date(2026, 11, 3)) == date(2028, 11, 7)

    def test_day_after_election_day_rolls_to_next_cycle(self):
        assert next_election_day(date(2026, 11, 4)) == date(2028, 11, 7)
