"""The president page's charts (app/president_charts.py), from stored series."""

from app.models import President
from app.pipeline.cache import api_cache_set
from app.president_charts import MIN_BAND, president_charts


def _president(n: int, start: int, end: int | None) -> President:
    return President(id=f"p-{n}", name=f"President {n}", party="D", number=n,
                     term_start=f"{start}-01-20", term_end=f"{end}-01-20" if end else None,
                     is_current=end is None)


def test_growth_and_jobs_come_from_the_stored_series_against_the_same_era(db_session):
    for i in range(7):
        db_session.add(_president(i + 1, 1953 + 4 * i, 1957 + 4 * i))
    db_session.commit()
    gdp = {str(y): 1000 * 1.03 ** (y - 1950) for y in range(1950, 1990)}
    api_cache_set(db_session, "historical-gdp", "real-gdp-1790-present", {"data": gdp})
    payrolls = {str(y): 50000 + 1000 * (y - 1950) for y in range(1950, 1990)}
    api_cache_set(db_session, "fred-annual-series", "PAYEMS", {"years": payrolls})

    out = president_charts(db_session, "p-7")
    growth = next(c for c in out["charts"] if c["key"] == "growth")
    # The term's years: inauguration year to the year before it ended.
    assert [b["label"] for b in growth["bars"]] == ["1977", "1978", "1979", "1980"]
    assert growth["bars"][0]["first"] is True and growth["bars"][0]["shown"] == "3.0%"
    assert growth["eraBand"]["presidencies"] == 6 and growth["eraBand"]["years"] == 24
    assert "because" not in growth["caption"]

    jobs = next(c for c in out["charts"] if c["key"] == "jobs")
    assert jobs["series"]["points"] == [[0, 0.0], [1, 1.0], [2, 2.0], [3, 3.0]]
    assert jobs["band"][0]["n"] >= MIN_BAND and jobs["table"][3]["value"] == "+3.0M"


def test_nothing_is_drawn_without_a_stored_series(db_session):
    db_session.add(_president(1, 1953, 1957))
    db_session.commit()
    assert president_charts(db_session, "p-1") == {"charts": [], "minBand": MIN_BAND}
    assert president_charts(db_session, "nobody") is None
