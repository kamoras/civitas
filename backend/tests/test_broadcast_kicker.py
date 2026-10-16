"""Livestream announcements are told by their headline's kicker
(action_center.announces_broadcast), classified by embedding margin against
a cut measured from app/data/broadcast_kicker_labels.json."""

import numpy as np
import pytest

from app.pipeline.analyze import action_center as ac


def test_kicker_is_the_label_before_the_first_colon():
    assert ac._headline_kicker("WATCH LIVE: Trump speaks at a rally") == "WATCH LIVE"
    assert ac._headline_kicker("Trump speaks at a rally") is None
    assert ac._headline_kicker("Results after 8:00 tonight") is None
    assert ac._headline_kicker(": no label") is None


def test_only_headlines_with_a_kicker_past_the_cut_are_broadcasts(monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_threshold", lambda: 0.2)
    monkeypatch.setattr(ac, "_broadcast_margins", lambda kickers: np.array([
        0.3 if k.lower() == "watch live" else 0.1 for k in kickers
    ]))
    assert ac.announces_broadcast([
        "Senate passes the bill", "Watch live: Senate votes", "Analysis: what the vote means",
    ]) == [False, True, False]


def test_no_kicker_means_no_model_call(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("embedded a headline with no kicker")

    monkeypatch.setattr(ac, "_broadcast_margins", refuse)
    assert ac.announces_broadcast(["Senate passes the bill"]) == [False]


def test_labels_that_no_longer_separate_hold_nothing_back(monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_cache", {})
    monkeypatch.setattr(ac, "_broadcast_margins", lambda kickers: np.full(len(kickers), 0.1))
    assert ac._broadcast_threshold() is None
    assert ac.announces_broadcast(["WATCH LIVE: Senate votes"]) == [False]


@pytest.mark.slow
def test_the_measured_cases_on_the_real_model(monkeypatch):
    monkeypatch.setattr(ac, "_broadcast_cache", {})
    assert ac._broadcast_threshold() is not None
    assert ac.announces_broadcast([
        "WATCH LIVE: Trump speaks at midterm rally in Youngstown, Ohio",
        "Watch live: Trump heads to Alaska as Senate race heats up",
        "LISTEN LIVE: Supreme Court hears case seeking damages from fossil fuel industry for climate change",
        "Trump heads to Alaska in a sign of how close its Senate race may be",
        "WATCH: Senate rejects war powers resolution to halt military action in Iran",
        "Live updates: Trump takes the stage for Alaska rally; Key races hold midterm debates",
        "Capitol agenda: Waters rallies support as rivals circle",
    ]) == [True, True, True, False, False, False, False]
