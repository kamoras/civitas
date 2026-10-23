"""Where a president stands among the other presidencies (app/president_peers.py)."""

from datetime import datetime, timezone

from app.models import President
from app.president_peers import MIN_PEERS, president_peers


def _president(n: int, **kw) -> President:
    defaults = dict(
        id=f"p-{n}", name=f"President {n}", party="D", number=n,
        term_start=f"{1940 + 4 * n}-01-20", term_end=f"{1944 + 4 * n}-01-20", is_current=False,
    )
    defaults.update(kw)
    return President(**defaults)


def _seed(db, approvals):
    for i, a in enumerate(approvals, start=1):
        db.add(_president(i, avg_approval=a, historical_legacy_score=400 + 10 * i))
    db.commit()


def test_each_figure_is_set_among_the_other_presidencies_with_a_word(db_session):
    _seed(db_session, [40 + i for i in range(12)])
    out = president_peers(db_session, "p-12")
    row = next(m for m in out["measures"] if m["key"] == "approval")
    assert row["value"] == 51 and row["shown"] == "51.0%"
    assert row["peerGroup"]["n"] == 11 and len(row["distribution"]) == 11
    assert row["distributionShown"][0] == "40.0%"
    assert row["word"] == "well above other presidencies"  # top of 12, over 1 sd from the median
    assert row["sentence"].startswith("Approval averaged 51.0% during the term. 11 of the other 11 presidencies averaged lower.")
    # No score anywhere in what the page draws.
    assert "score" not in row and "overall" not in out
    assert out["historians"]["rank"] == 1 and out["historians"]["rated"] == 12
    # The strip's hover labels: each presidency's years and its place among
    # every presidency on the row, this one included.
    assert row["rank"] == "Highest of 12"
    assert row["distributionRanks"][0] == "Lowest of 12" and row["distributionRanks"][-1] == "2nd highest of 12"
    assert row["distributionYears"][0] == "1944–1948"
    assert out["years"] == "1988–1992"


def test_no_word_below_the_peer_floor(db_session):
    _seed(db_session, [40 + i for i in range(MIN_PEERS - 1)])
    row = next(m for m in president_peers(db_session, "p-1")["measures"] if m["key"] == "approval")
    assert row["thin"] is True and row["word"] is None


def test_a_missing_record_is_said_plainly_never_drawn(db_session):
    db_session.add(_president(1))
    db_session.commit()
    out = president_peers(db_session, "p-1")
    assert out["measures"] == []
    assert any("No approval polls" in m for m in out["missing"])
    assert any("Not rated by historians" in m for m in out["missing"])
    assert president_peers(db_session, "nobody") is None


def test_a_sitting_presidents_peer_growth_says_it_is_the_first_year(db_session, freeze_utcnow):
    """A term in its second calendar year has no later complete year, so the
    pipeline credits the first year alone (peer_gdp.peer_relative_growth):
    the method says so and the row draws no word."""
    for i in range(2, 9):
        db_session.add(_president(i, gdp_growth_relative=0.5 * i))
    db_session.add(_president(9, term_start="2025-01-20", term_end=None, is_current=True,
                              gdp_growth_relative=3.6))
    db_session.commit()
    freeze_utcnow(datetime(2026, 10, 22, tzinfo=timezone.utc))
    row = next(m for m in president_peers(db_session, "p-9")["measures"] if m["key"] == "growthVsPeers")
    assert row["measured"].startswith("First year only; no later year published yet.")
    assert "leaving out the first year" not in row["measured"]
    assert row["thin"] is True and row["word"] is None
    finished = next(m for m in president_peers(db_session, "p-8")["measures"] if m["key"] == "growthVsPeers")
    assert "leaving out the first year" in finished["measured"]


def _sitting_seed(db):
    """Twelve finished full terms whose whole-term averages (40-51) sit
    below their first-stretch averages (60-71), a sitting president at 55,
    and a cut-short presidency polled for fewer days than the window."""
    for i in range(1, 13):
        db.add(_president(i, avg_approval=39.0 + i, approval_trend=-10.0))
    db.add(_president(13, term_start="1990-01-20", term_end="1990-06-01", avg_approval=90.0, approval_trend=0.0))
    db.add(_president(14, term_start="2025-01-20", term_end=None, is_current=True,
                      avg_approval=55.0, approval_trend=-4.0, approval_start=58.0))
    db.commit()


_WINDOW = {
    "days": 600,
    "avg_approval": {"mean": 65.5, "stdev": 3.6, "n": 12},
    "values": {f"p-{i}": {"avg": 59.0 + i, "trend": -2.0 - i} for i in range(1, 13)},
}


def test_a_sitting_president_is_ranked_among_the_same_days_of_every_other_term(db_session, monkeypatch):
    _sitting_seed(db_session)
    monkeypatch.setattr("app.president_peers._approval_reference",
                        lambda ref, is_current, pid=None: _WINDOW if pid == "p-14" else {})
    rows = {m["key"]: m for m in president_peers(db_session, "p-14")["measures"]}
    row = rows["approval"]
    # Against full terms 55% would be the highest; over the same 600 days
    # every other presidency averaged higher. The presidency polled for
    # fewer days (p-13) is left out.
    assert row["distribution"] == [59.0 + i for i in range(1, 13)]
    assert "p-13" not in row["distributionIds"]
    assert row["rank"] == "Lowest of 13" and row["distributionRanks"][0] == "12th highest of 13"
    assert row["sentence"] == ("Approval averaged 55.0% over the term's first 600 days. "
                               "12 of the other 12 presidencies averaged higher over their first 600 days.")
    assert row["peerGroup"] == {"label": "presidencies polled for at least 600 days, each over its first 600 days",
                                "n": 12}
    assert row["expected"] == 65.5 and row["expectedBasis"] == "same stretch"
    change = rows["approvalChange"]
    assert change["distribution"] == sorted(-2.0 - i for i in range(1, 13))
    assert change["sentence"].startswith("Approval fell 4.0 points over the term's first 600 days.")


def test_a_full_term_president_is_still_set_among_full_terms(db_session, monkeypatch):
    _sitting_seed(db_session)
    monkeypatch.setattr("app.president_peers._approval_reference",
                        lambda ref, is_current, pid=None: _WINDOW if pid == "p-14" else {})
    row = next(m for m in president_peers(db_session, "p-12")["measures"] if m["key"] == "approval")
    # The other full terms only: not the sitting or the cut-short presidency.
    assert row["distribution"] == [39.0 + i for i in range(1, 12)]
    assert row["sentence"] == "Approval averaged 51.0% during the term. 11 of the other 11 presidencies averaged lower."


def test_a_reference_without_per_term_values_falls_back(db_session, monkeypatch):
    """A reference persisted before the values were kept: the row still
    draws (against full terms) until the next president run writes them."""
    _sitting_seed(db_session)
    old = {k: v for k, v in _WINDOW.items() if k != "values"}
    monkeypatch.setattr("app.president_peers._approval_reference", lambda ref, is_current, pid=None: old)
    row = next(m for m in president_peers(db_session, "p-14")["measures"] if m["key"] == "approval")
    assert row["peerGroup"]["n"] == 12 and row["sentence"].startswith("Approval averaged 55.0% during the term.")


def _store_polls(db, pid, rows):
    """rows: (MM/DD/YYYY, approve %, source) as UCSB's table gives them."""
    from app.pipeline.cache import api_cache_set
    api_cache_set(db, "presidential-approval", f"approval-v2-{pid}", {"polls": [
        {"start_date": d, "end_date": d, "approving": a, "disapproving": None, "unsure": None, "source": src}
        for d, a, src in rows
    ]})


def test_the_approval_row_names_each_presidencys_lowest_and_highest_single_poll(db_session):
    _seed(db_session, [40 + i for i in range(12)])
    # An interpolated row is no reading anyone took: never an extreme.
    _store_polls(db_session, "p-12", [("01/25/1989", 63, None), ("03/01/1990", 41, None),
                                      ("06/01/1990", 20, "interpolated"), ("09/01/1991", 41, None)])
    _store_polls(db_session, "p-1", [("02/01/1945", 87, None), ("02/01/1947", 22, None)])
    row = next(m for m in president_peers(db_session, "p-12")["measures"] if m["key"] == "approval")
    assert row["aside"] == ("Lowest single poll 41% (Mar 1990) · highest 63% (Jan 1989). "
                            "The strip ranks term averages, not single polls.")
    assert row["detail"] == "1988–1992 · single polls from 41% (Mar 1990) to 63% (Jan 1989)"
    assert row["distributionDetails"][0] == "1944–1948 · single polls from 22% (Feb 1947) to 87% (Feb 1945)"
    # A presidency with no stored polls keeps its years alone.
    assert row["distributionDetails"][1] == "1948–1952"
    assert "—" not in row["aside"] + row["detail"]
    others = [m for m in president_peers(db_session, "p-12")["measures"] if m["key"] != "approval"]
    assert all("aside" not in m for m in others)


def test_a_sitting_presidents_extremes_are_so_far_and_over_the_window(db_session, monkeypatch):
    _sitting_seed(db_session)
    _store_polls(db_session, "p-14", [("01/25/2025", 47, None), ("03/01/2026", 34, None)])
    # p-1's polls after its first 600 days are outside the window.
    _store_polls(db_session, "p-1", [("01/25/1945", 70, None), ("06/01/1946", 55, None),
                                     ("06/01/1948", 20, None)])
    monkeypatch.setattr("app.president_peers._approval_reference",
                        lambda ref, is_current, pid=None: _WINDOW if pid == "p-14" else {})
    row = next(m for m in president_peers(db_session, "p-14")["measures"] if m["key"] == "approval")
    assert row["aside"] == ("Lowest single poll so far 34% (Mar 2026) · highest so far 47% (Jan 2025). "
                            "The strip ranks averages over each presidency's first 600 days, not single polls.")
    assert row["detail"] == "since 2025 · single polls so far from 34% (Mar 2026) to 47% (Jan 2025)"
    i = row["distributionIds"].index("p-1")
    assert row["distributionDetails"][i] == ("1944–1948 · single polls in the first 600 days from 55% (Jun 1946) "
                                             "to 70% (Jan 1945)")
