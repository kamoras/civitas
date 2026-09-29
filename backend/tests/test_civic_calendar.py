from datetime import date

import pytest

from app.api.action import _upcoming_civic_events


@pytest.mark.parametrize("year,first_monday", [
    (2026, date(2026, 10, 5)),  # the 7th is a Wednesday
    (2024, date(2024, 10, 7)),
    (2025, date(2025, 10, 6)),
    (2027, date(2027, 10, 4)),
])
def test_the_court_term_begins_on_the_first_monday_in_october(year, first_monday):
    events = _upcoming_civic_events(year, date(year, 1, 1))
    term = next(e for e in events if e["category"] == "scotus")
    assert term["date"] == first_monday.isoformat()
    assert first_monday.weekday() == 0


def test_every_link_is_a_page_the_site_has():
    events = _upcoming_civic_events(2029, date(2029, 1, 1))  # inauguration, Congress, Court, no election
    links = {e["link"].split("?")[0] for e in events}
    assert "/scorecard" not in links
    assert links <= {"/politicians", "/leaderboard", "/elections"}


def test_election_day_links_to_the_elections_page():
    """The Action Center's elections tab is gone (2026-09); Election Day
    points at /elections, which carries the countdown and every state's
    ballot."""
    events = _upcoming_civic_events(2026, date(2026, 1, 1))
    election = next(e for e in events if e["category"] == "election")
    assert election["link"] == "/elections"
