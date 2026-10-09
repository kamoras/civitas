"""Senators' approval among the state's other-party voters and independents
(Constituent Alignment's approval part, v6.29)."""

from datetime import datetime

import pytest

from app.pipeline.analyze import constituent_approval, score_calculator
from app.services import constituent_survey
from app.pipeline.analyze.score_calculator import _constituent_alignment_core

PVI = {"AA": 10, "BB": -10, "CC": 0, "DD": 5, "EE": -5, "FF": 15}
PRIORS = {
    "senate/D/R": {"mu": 0.15, "k": 50}, "senate/D/I": {"mu": 0.45, "k": 50},
    "senate/R/D": {"mu": 0.12, "k": 50}, "senate/R/I": {"mu": 0.50, "k": 50},
}


def member(state, name, party, opp, ind, chamber="senate"):
    other = "R" if party == "D" else "D"
    return {"chamber": chamber, "state": state, "name": name, "member_party": party, "district": None,
            "by_party": {other: {"shrunk": opp, "n": 200, "own_weight": 0.7},
                         "I": {"shrunk": ind, "n": 150, "own_weight": 0.5}}}


@pytest.fixture
def survey(monkeypatch):
    members = [
        member("AA", "Avery Able", "D", 0.30, 0.60), member("BB", "Blair Best", "D", 0.15, 0.45),
        member("CC", "Casey Cole", "D", 0.10, 0.40), member("DD", "Drew Dunn", "D", 0.16, 0.44),
        member("AA", "Ellis Erb", "R", 0.12, 0.50), member("BB", "Finley Ford", "R", 0.05, 0.40),
        member("CC", "Gray Gould", "R", 0.20, 0.55), member("EE", "Harper Hale", "R", 0.12, 0.52),
        member("FF", "Indigo Ives", "R", 0.11, 0.49),
    ]
    data = {"survey": "Test survey", "fielded": "2024-10/2024-11", "fielded_year": 2024,
            "priors": PRIORS, "members": members}
    monkeypatch.setattr(constituent_survey, "_survey_cache", data)
    monkeypatch.setattr(constituent_approval, "_cache", {})
    return data


def test_a_senator_rated_better_by_the_other_side_scores_higher(survey):
    able = constituent_approval.senate_approval("AA", "Avery Able", "D", 6, PVI)
    cole = constituent_approval.senate_approval("CC", "Casey Cole", "D", 6, PVI)
    assert able["z"] > 0 > cole["z"]
    assert [g for g, _, _ in able["groups"]] == ["R", "I"]  # never the senator's own party


def test_no_reading_for_a_senator_seated_after_the_survey_or_unrated(survey, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 7, 12))
    assert constituent_approval.senate_approval("AA", "Avery Able", "D", 1, PVI) is None
    assert constituent_approval.senate_approval("AA", "Someone Else", "D", 6, PVI) is None
    assert constituent_approval.senate_approval("AA", "Avery Able", "I", 6, PVI) is None


def _core(monkeypatch, approval, district=None):
    monkeypatch.setattr(score_calculator, "senate_approval", lambda *a, **k: approval)
    return _constituent_alignment_core({}, [], {}, state="AA", party="D", district=district,
                                       name="Avery Able", years_in_office=6)


def test_approval_moves_the_score_around_the_typical_senator(monkeypatch):
    base = _core(monkeypatch, None)["score"]
    typical = {"index": 0.0, "z": 0.0, "groups": [("R", 0.15, 0.15), ("I", 0.45, 0.45)], "survey": "Test survey"}
    assert _core(monkeypatch, typical)["score"] == base  # typical approval adds nothing, as unrated
    high = {**typical, "z": 1.0}
    core = _core(monkeypatch, high)
    approval = next(c for c in core["components"] if c["label"] == "Constituent approval")
    # As much as position congruence can move the score: its weight times
    # the part's distance from 50.
    expected = base + score_calculator.POSITION_CONGRUENCE_WEIGHT * (approval["score"] - 50)
    assert abs(core["score"] - min(expected, 100)) <= 1
    assert core["facts"]["approval"]["z"] == 1.0
    low = _core(monkeypatch, {**typical, "z": -1.0})
    assert low["score"] < base


def test_an_unrated_senator_lists_the_part_as_not_measured(monkeypatch):
    core = _core(monkeypatch, None)
    approval = [c for c in core["components"] if c["label"] == "Constituent approval"]
    assert approval and approval[0]["score"] is None and approval[0]["weight"] == 0.0
    assert core["facts"]["approval"] is None


def test_house_members_are_never_scored_on_approval(monkeypatch):
    calls = []
    monkeypatch.setattr(score_calculator, "senate_approval", lambda *a, **k: calls.append(a))
    core = _constituent_alignment_core({}, [], {}, state="AA", party="D", district=3, name="Avery Able", years_in_office=6)
    assert calls == [] and all(c["label"] != "Constituent approval" for c in core["components"])
