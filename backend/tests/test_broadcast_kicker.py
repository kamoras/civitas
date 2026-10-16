"""Livestream and video pages are told by their headline's kicker
(action_center.announces_broadcast), classified by embedding margin against
a cut fitted daily on labels the publishers supply: items they file under a
video section against the same feeds' articles."""

import numpy as np
import pytest

from app.models import FeedArticle
from app.pipeline.analyze import action_center as ac


def _margins_by_kicker(kickers):
    return np.array([{"watch live": 0.3, "watch": 0.13}.get(k.lower(), 0.0) for k in kickers])


def test_kicker_is_the_label_before_the_first_colon():
    assert ac._headline_kicker("WATCH LIVE: Trump speaks at a rally") == "WATCH LIVE"
    assert ac._headline_kicker("Trump speaks at a rally") is None
    assert ac._headline_kicker("Results after 8:00 tonight") is None
    assert ac._headline_kicker(": no label") is None


def test_filed_as_video_reads_the_section_not_the_slug():
    assert ac._filed_as_video("https://thehill.com/video-clips/6154181-watch-live-donald-trump/")
    assert ac._filed_as_video("https://www.bbc.com/news/videos/c4g0e1")
    assert not ac._filed_as_video("https://www.pbs.org/newshour/politics/watch-live-video-of-the-debate")
    assert not ac._filed_as_video("https://www.politico.com/live-updates/2026/10/16/congress/x")


def test_cut_maximises_f1_on_noisy_publisher_labels(monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_margins", _margins_by_kicker)
    labelled = ([("Watch live", True)] * 12 + [("Watch", True)]
                + [("Watch live", False)] * 2 + [("Live updates", False)] * 40)
    cut = ac.fit_broadcast_cut(labelled)
    assert 0.0 < cut < 0.13


def test_too_few_filed_items_fit_no_cut(monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_margins", _margins_by_kicker)
    assert ac.fit_broadcast_cut([("Watch live", True)] * 3 + [("Live updates", False)] * 40) is None
    assert ac.fit_broadcast_cut([("Watch live", True)] * 12) is None


def test_the_cut_is_fitted_on_stored_feed_articles(db_session, monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_cache", {})
    monkeypatch.setattr(ac, "_broadcast_margins", _margins_by_kicker)
    rows = [("The Hill", f"https://thehill.com/video-clips/{i}-watch-live/", f"Watch live: Event {i}") for i in range(12)]
    rows += [("The Hill", f"https://thehill.com/homenews/{i}-live/", f"Live updates: Day {i}") for i in range(30)]
    # A feed with no video section supplies no labels.
    rows += [("Roll Call", f"https://rollcall.com/2026/10/{i}/", f"Watch live: Other {i}") for i in range(30)]
    db_session.add_all([FeedArticle(source_name=s, url=u, title=t, text=t, embedding=b"", model="m") for s, u, t in rows])
    db_session.commit()
    assert ac.announces_broadcast(
        ["Watch live: Senate votes", "Senate passes the bill", "Live updates: the vote"], db_session,
    ) == [True, False, False]


def test_no_kicker_means_no_model_call(db_session, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("embedded a headline with no kicker")

    monkeypatch.setattr(ac, "_broadcast_margins", refuse)
    assert ac.announces_broadcast(["Senate passes the bill"], db_session) == [False]


def test_an_empty_store_holds_nothing_back(db_session, monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_cache", {})
    monkeypatch.setattr(ac, "_broadcast_margins", _margins_by_kicker)
    assert ac.announces_broadcast(["WATCH LIVE: Senate votes"], db_session) == [False]


@pytest.mark.slow
def test_publisher_labelled_kickers_separate_on_the_real_model():
    """The shape stored on 2026-10-16: The Hill's /video-clips/ livestreams
    and a BBC clip against the same feeds' article kickers."""
    labelled = ([("Watch live", True)] * 17 + [("Watch", True)] + [("Watch live", False)] * 2
                + [(k, False) for k in ["Live updates", "Live results", "The Memo", "Massie", "Tillis",
                                         "This week on the Hill", "Mamdani weighs in on 'Woke 1 was crazy'",
                                         "Democrat leads Marshall in tight Kansas Senate race", "Capitol agenda"]])
    cut = ac.fit_broadcast_cut(labelled)
    flags = ac._broadcast_margins([
        "WATCH LIVE", "LISTEN LIVE", "WATCH", "Live updates", "Capitol agenda", "At the Races",
    ]) > cut
    assert flags.tolist() == [True, True, True, False, False, False]
