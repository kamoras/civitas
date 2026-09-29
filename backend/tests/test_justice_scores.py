"""Justice v2: the score is loyalty to the appointing president (Epstein &
Posner 2016), fit per justice, shrunk across justices, scored against the
between-justice spread. See justice_loyalty's module docstring and
docs/research/justice-scores.md.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.config_definitions import JUSTICE_SCORE_WEIGHTS
from app.models import Justice
from app.pipeline.analyze.justice_loyalty import Vote, fit, label, loyalty_by_justice, president_on, score
from app.pipeline.fetch.justice_records import fetch_scdb, fjc_appointments, scdb_president_votes
from app.pipeline.justice_pipeline import (
    _appointers,
    _bundled_rows,
    _database_name,
    _measure_loyalty,
    run_justice_pipeline,
)
from app.services.justice_service import get_justice

TERMS = [("P1", "2000-01-20", "2008-01-20"), ("P2", "2008-01-20", None)]


def simulate(rng, effect, n=400, base=0.5):
    """(for_government, appointer_in_office, government_petitioner) votes
    with a true loyalty `effect` and petitioner-side reversal bias."""
    x_in = rng.random(n) < 0.5
    pet = rng.random(n) < 0.5
    p = base + effect * x_in + 0.1 * pet
    return [(int(rng.random() < q), int(i), int(t)) for q, i, t in zip(p, x_in, pet)]


def test_loyalty_is_the_only_scored_measure():
    assert JUSTICE_SCORE_WEIGHTS == {"loyalty": 1.0}


def test_fit_recovers_the_effect_holding_the_side_fixed():
    est = fit(simulate(np.random.default_rng(0), 0.2, n=4000))
    assert est.raw == pytest.approx(0.2, abs=0.04)
    assert 0 < est.se < 0.03
    assert est.votes_in + est.votes_out == 4000


def test_too_few_votes_on_either_side_is_not_estimated():
    votes = [(1, 1, 0)] * 100 + [(0, 0, 1)] * 9
    assert fit(votes) is None


def test_shrinkage_pulls_noisy_estimates_toward_the_mean_and_scores_them():
    rng = np.random.default_rng(3)
    rows = {f"j{i}": simulate(rng, e) for i, e in enumerate([0.0, 0.05, 0.1, 0.15, 0.3])}
    rows["tiny"] = simulate(rng, 0.6, n=40)
    result, mean, tau = loyalty_by_justice(rows)
    assert tau > 0
    raw, shrunk = result["tiny"].estimate.raw, result["tiny"].loyalty
    assert abs(shrunk - mean) < abs(raw - mean)
    assert all(0.0 <= r.score <= 100.0 for r in result.values())


def test_fewer_than_three_estimable_justices_scores_no_one():
    rng = np.random.default_rng(4)
    assert loyalty_by_justice({"a": simulate(rng, 0.1), "b": simulate(rng, 0.1)}) == ({}, 0.0, 0.0)


def test_score_is_symmetric_and_bounded():
    assert score(0.0, 0.1) == 100.0
    assert score(0.1, 0.1) == score(-0.1, 0.1) == 50.0
    assert score(0.5, 0.1) == 0.0
    assert score(0.3, 0.0) == 100.0  # no spread between justices: none stands out


def test_president_on_uses_half_open_terms():
    assert president_on("2008-01-19", TERMS) == "P1"
    assert president_on("2008-01-20", TERMS) == "P2"
    assert president_on("1999-01-01", TERMS) is None


def test_label_counts_a_vote_only_inside_an_appointment():
    appointer = {"J": [("P1", "2001-01-01", None)]}
    votes = [
        Vote("J", "2003-05-01", True, True),   # P1 in office, the appointer
        Vote("J", "2010-05-01", False, False),  # P2 in office
        Vote("J", "2000-06-01", True, True),   # before the appointment
        Vote("X", "2003-05-01", True, True),   # no appointment known
    ]
    assert label(votes, appointer, TERMS) == {"J": [(1, 1, 1), (0, 0, 0)]}


def _scdb_row(**kw):
    row = {"justiceName": "JDoe", "term": "2016", "dateDecision": "6/1/2017", "decisionType": "1",
           "petitioner": "27", "respondent": "100", "partyWinning": "1", "majority": "2"}
    return {**row, **kw}


def test_scdb_votes_code_the_government_side():
    rows = [
        _scdb_row(),                                            # gov petitioner won, justice in majority
        _scdb_row(petitioner="100", respondent="301"),          # gov respondent lost, justice in majority
        _scdb_row(respondent="27"),                             # government on both sides: skipped
        _scdb_row(decisionType="2"),                            # not a signed decision: skipped
        _scdb_row(justiceName="RRoe", term="2017", petitioner="100", respondent="100"),
    ]
    votes, current, term = scdb_president_votes(rows)
    assert [(v.government_petitioner, v.for_government, t) for v, t in votes] == [(True, True, 2016), (False, False, 2016)]
    assert votes[0][0].date == "2017-06-01"
    assert (current, term) == ({"RRoe"}, 2017)


def test_fjc_appointments_and_appointer_match():
    rows = [{"Last Name": "Rehnquist", "First Name": "William",
             "Court Type (1)": "Supreme Court", "Nomination Date (1)": "10/22/1971",
             "Commission Date (1)": "12/15/1971", "Termination Date (1)": "",
             "Court Type (2)": "Supreme Court", "Nomination Date (2)": "06/20/1986",
             "Commission Date (2)": "09/26/1986", "Termination Date (2)": "09/03/2005"},
            {"Last Name": "Smith", "First Name": "Ann", "Court Type (1)": "U.S. District Court",
             "Nomination Date (1)": "1/1/1990", "Commission Date (1)": "2/1/1990"}]
    appts = fjc_appointments(rows)
    assert [a["nominated"] for a in appts] == ["1971-10-22", "1986-06-20"]
    terms = [("Nixon", "1969-01-20", "1974-08-09"), ("Reagan", "1981-01-20", "1989-01-20")]
    assert [p for p, _, _ in _appointers({"WHRehnquist"}, appts, terms)["WHRehnquist"]] == ["Nixon", "Reagan"]


def test_database_name_needs_one_unambiguous_match():
    current = ["JGRoberts", "SAAlito", "CThomas"]
    assert _database_name({"name": "Samuel A. Alito, Jr.", "last_name": "Alito"}, current) == "SAAlito"
    assert _database_name({"name": "New Justice", "last_name": "Justice"}, current) is None


def test_the_bundle_reads_and_every_row_is_binary():
    rows = _bundled_rows()
    assert sum(len(r) for r in rows.values()) == 29585
    assert all(set(v) <= {0, 1} for r in rows.values() for v in r)


def test_an_unreadable_scdb_archive_is_none_not_an_empty_release_list(caplog):
    # A 403 from the archive (2026-09-29) read as "no release found".
    with patch("app.pipeline.fetch.justice_records._get", AsyncMock(return_value=None)):
        assert asyncio.run(fetch_scdb(None, None)) is None
    assert "could not be read" in caplog.text


def test_the_run_reports_when_loyalty_was_not_measured(db_session):
    justice = {"id": "clarence_thomas", "name": "Clarence Thomas", "last_name": "Thomas"}
    with patch("app.pipeline.justice_pipeline.fetch_current_justices", AsyncMock(return_value=[justice])), \
         patch("app.pipeline.justice_pipeline.fetch_case_votes", AsyncMock(return_value=[])), \
         patch("app.pipeline.justice_pipeline._measure_loyalty", AsyncMock(return_value=(None, "x could not be read"))):
        result = asyncio.run(run_justice_pipeline(db_session))
    assert result == {"justices": 1, "votes": 0, "loyalty_unmeasured": "x could not be read"}


def test_unmeasured_loyalty_names_every_source_that_was_down(db_session):
    # No presidents stored, the Database down, the FJC file fine.
    with patch("app.pipeline.justice_pipeline.fetch_scdb", AsyncMock(return_value=None)), \
         patch("app.pipeline.justice_pipeline.fetch_fjc", AsyncMock(return_value=[{"last": "thomas"}])):
        measured, why = asyncio.run(_measure_loyalty(None, db_session))
    assert measured is None
    assert why == "the Supreme Court Database and the presidents table could not be read"


def test_agreement_is_served_with_each_justices_name(db_session):
    """The scorecard showed "Brett M Kavanaugh" by splitting the id
    "brett_m_kavanaugh" on underscores; the API names each justice itself."""
    db_session.add_all([
        Justice(id="samuel_a_alito_jr", name="Samuel A. Alito, Jr.", last_name="Alito", is_active=True,
                agreement_matrix='{"brett_m_kavanaugh": 88.0, "clarence_thomas": 91.2, "gone": 50.0}'),
        Justice(id="brett_m_kavanaugh", name="Brett M. Kavanaugh", last_name="Kavanaugh", is_active=True),
        Justice(id="clarence_thomas", name="Clarence Thomas", last_name="Thomas", is_active=True),
    ])
    db_session.commit()
    agreement = get_justice(db_session, "samuel_a_alito_jr").agreement
    assert [(a.name, a.share) for a in agreement] == [("Clarence Thomas", 91.2), ("Brett M. Kavanaugh", 88.0)]
