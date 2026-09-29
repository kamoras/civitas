"""Tests for election_calendar — the Senate three-class rotation and its
mapping to election years. The class sets are data (app/data/
senate_classes.json, from the Senate's own member list; pipeline/fetch/
senate_classes.py); these are the authoritative cross-check _sync_roster
uses to label special elections, so the structural invariants below
(every state covered by the rotation) check the bundled file.
"""

from datetime import date

from app.election_calendar import (
    federal_states,
    next_election_day,
    next_senate_election_year,
    seats_up_for_year,
    senate_classes,
)

CLASS_I_STATES = senate_classes()[1]
CLASS_II_STATES = senate_classes()[2]
CLASS_III_STATES = senate_classes()[3]

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
        assert federal_states() == ALL_STATES

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


class TestNextSenateElectionYear:
    def test_state_not_up_in_2026_gets_its_real_next_year(self):
        # AZ is Class I and III, not Class II — not up in 2026. Its
        # soonest regular seat after 2026 is the Class III rotation
        # (2022, 2028, ...), not the later Class I one (2018, ..., 2030).
        assert next_senate_election_year("AZ", 2026) == 2028

    def test_state_up_this_cycle_still_returns_its_OTHER_seat(self):
        # GA is Class II (up in 2026) and Class III. Called with the
        # current cycle year, this answers "when's the other one" — the
        # caller only invokes it when THIS cycle's race is absent, but
        # the function itself doesn't know that and shouldn't guess.
        assert next_senate_election_year("GA", 2026) == 2028

    def test_no_senate_seats_returns_none(self):
        assert next_senate_election_year("DC", 2026) is None

    def test_every_real_state_gets_an_answer(self):
        for st in ALL_STATES:
            assert next_senate_election_year(st, 2026) is not None, st


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


class TestSenateClassesFromTheSenatesList:
    """The class sets are read from senate.gov's member list, never typed."""

    _XML = b"""<contact_information>
    <member><state>MD</state><class>Class I</class></member>
    <member><state>MD</state><class>Class III</class></member>
    <member><state>AK</state><class>Class II</class></member>
    <member><state>AK</state><class>Class III</class></member>
    <member><state>ZZ</state><class>Class I</class></member>
    </contact_information>"""

    def test_parses_each_senators_class(self):
        from app.pipeline.fetch.senate_classes import parse_senate_classes

        assert parse_senate_classes(self._XML) == {1: {"MD", "ZZ"}, 2: {"AK"}, 3: {"MD", "AK"}}

    def test_a_new_state_needs_no_code_change(self):
        # "ZZ" parsed above like any other state: nothing lists the states.
        from app.pipeline.fetch.senate_classes import gate, parse_senate_classes

        assert gate(parse_senate_classes(self._XML)) == []

    def test_gates_refuse_a_list_that_lost_a_class_or_put_a_state_in_all_three(self):
        from app.pipeline.fetch.senate_classes import gate

        assert gate({1: {"MD"}, 2: {"AK"}})
        assert gate({1: {"MD"}, 2: {"MD"}, 3: {"MD"}})

    async def test_a_vacant_seat_does_not_drop_its_state(self, tmp_path, monkeypatch):
        import httpx

        from app import election_calendar
        from app.pipeline.fetch import senate_classes as sc

        path = tmp_path / "senate_classes.json"
        sc.write_classes({1: {"MD", "DE"}, 2: {"AK", "DE"}, 3: {"MD", "AK"}}, path)
        # DE's Class I senator has left; the list shows only its Class II one.
        vacancy = b"""<contact_information>
        <member><state>MD</state><class>Class I</class></member>
        <member><state>AK</state><class>Class II</class></member><member><state>DE</state><class>Class II</class></member>
        <member><state>MD</state><class>Class III</class></member><member><state>AK</state><class>Class III</class></member>
        </contact_information>"""
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=vacancy))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await sc.refresh_senate_classes(client, str(path)) is True
        assert "DE" in sc._stored(path)[1]
        election_calendar.reset_senate_classes()

    async def test_an_unreadable_list_keeps_what_is_stored(self, tmp_path):
        import httpx

        from app.pipeline.fetch import senate_classes as sc

        path = tmp_path / "senate_classes.json"
        sc.write_classes({1: {"MD"}, 2: {"AK"}, 3: {"MD", "AK"}}, path)
        before = path.read_text()
        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await sc.refresh_senate_classes(client, str(path)) is False
        assert path.read_text() == before
