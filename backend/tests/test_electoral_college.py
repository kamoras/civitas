"""Electoral votes per election (fetch/electoral_college.py) and the chart."""

from app.models import President
from app.pipeline.cache import api_cache_set
from app.pipeline.fetch.electoral_college import assign, parse_year_page
from app.president_charts import president_charts


def _page(body: str) -> str:
    return f"<html><body><a>Contact OFR</a><h1>1900 Electoral College Results</h1><div>{body}</div></body></html>"


def test_the_page_figures_are_read_past_footnote_marks():
    assert parse_year_page(_page(
        "President Jane Doe [R] Main Opponent John Roe [D] Electoral Vote* Winner: 304 Main Opponent: 227 "
        "Total/Majority: 538/270")) == {"president": "Jane Doe", "opponent": "John Roe", "winnerEv": 304,
                                       "opponentEv": 227, "electors": 538}
    got = parse_year_page(_page(
        "President Jane Doe [D] Main Opponent John Roe [W] Electoral Vote Winner: 214 Main Opponent: "
        "(71, excluding one state; 80, including it)* Total/Majority: 176 (175)*/89"))
    assert (got["opponentEv"], got["electors"]) == (71, 176)
    assert parse_year_page(_page("nothing here")) is None


def _p(n, name, start, end):
    return President(id=f"p-{n}", name=name, party="D", number=n, term_start=start, term_end=end, is_current=False)


def test_each_election_goes_to_the_presidency_it_put_in_office():
    presidents = [_p(1, "Ann Poe", "1821-03-04", "1825-03-04"), _p(2, "John Q. Roe", "1825-03-04", "1829-03-04"),
                  _p(3, "Max Moe", "1849-03-05", "1850-07-09")]
    results = {
        # The page names the electoral leader; the House elected the runner-up.
        1824: {"president": "Andrew Leader", "opponent": "John Q. Roe", "winnerEv": 99, "opponentEv": 84,
               "electors": 261},
        # Inauguration moved a day for a Sunday: still found.
        1848: {"president": "Max Moe", "opponent": "X", "winnerEv": 163, "opponentEv": 127, "electors": 290},
    }
    rows = assign(results, presidents, {1848: 4.8})
    assert [(r["year"], r["presidentId"], r["ev"], r["decidedByHouse"]) for r in rows] == [
        (1824, "p-2", 84, True), (1848, "p-3", 163, False)]
    assert rows[0]["popularMargin"] is None and rows[1]["evShare"] == 56.2


def test_the_chart_says_a_succession_rather_than_drawing_a_zero(db_session):
    db_session.add(_p(1, "Ann Poe", "1841-03-04", "1845-03-04"))
    db_session.commit()
    rows = [{"year": 1836, "presidentId": "p-0", "president": "John Roe", "ev": 170, "electors": 294,
             "evShare": 57.8, "popularMargin": 14.2, "decidedByHouse": False}]
    api_cache_set(db_session, "electoral-college", "elections", {"elections": rows})
    chart = next(c for c in president_charts(db_session, "p-1")["charts"] if c["key"] == "elections")
    assert chart["caption"].startswith("Never won a presidential election: came to office by succession")
    assert not any(r["mine"] for r in chart["rows"])
    assert chart["medianShareShown"] == "57.8%"
    assert chart["marks"] == [{"id": "1836", "label": "1836", "detail": "John Roe",
                               "shown": "170 of 294 electoral votes (57.8%)",
                               "context": "popular margin +14.2 points", "href": "/politicians/p-0"}]
