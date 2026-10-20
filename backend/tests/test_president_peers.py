"""Where a president stands among the other presidencies (app/president_peers.py)."""

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
