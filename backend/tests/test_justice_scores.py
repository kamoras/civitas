"""Justice v3: no justice is scored. Each justice's own estimate of the
appointing president's effect (Epstein & Posner 2016) is stored and shown
with its confidence interval, unranked, because no method yet separates
loyalty from career timing for an individual justice. See justice_loyalty's
module docstring and docs/research/justice-scores.md.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.models import Justice
from app.pipeline.analyze.justice_loyalty import Vote, estimates_by_justice, fit, label, president_on
from app.pipeline.fetch.justice_records import fetch_scdb, fjc_appointments, scdb_president_votes
from app.pipeline.justice_pipeline import (
    _appointers,
    _bundled_rows,
    _database_name,
    _loyalty_fields,
    _measure_loyalty,
    run_justice_pipeline,
)
from app.services.justice_service import get_justice, get_justice_leaderboard

TERMS = [("P1", "2000-01-20", "2008-01-20"), ("P2", "2008-01-20", None)]


def simulate(rng, effect, n=400, base=0.5):
    """(for_government, appointer_in_office, government_petitioner) votes
    with a true loyalty `effect` and petitioner-side reversal bias."""
    x_in = rng.random(n) < 0.5
    pet = rng.random(n) < 0.5
    p = base + effect * x_in + 0.1 * pet
    return [(int(rng.random() < q), int(i), int(t)) for q, i, t in zip(p, x_in, pet)]


def test_fit_recovers_the_effect_holding_the_side_fixed():
    est = fit(simulate(np.random.default_rng(0), 0.2, n=4000))
    assert est.raw == pytest.approx(0.2, abs=0.04)
    assert 0 < est.se < 0.03
    assert est.votes_in + est.votes_out == 4000


def test_a_side_of_one_vote_is_not_estimated():
    """One vote fits itself (leverage 1): nothing is measured."""
    assert fit([(1, 1, 0)] * 100 + [(0, 0, 1)]) is None
    assert fit([(1, 1, 0)] * 100) is None


def test_a_thin_side_is_measured_with_an_honest_interval():
    """No count of votes is typed: nine votes all one way are measured, but
    their interval is never narrower than the shares' own uncertainty (HC1
    alone read them as exact)."""
    e = fit([(1, 1, 0)] * 100 + [(0, 0, 1)] * 9)
    assert e is not None and e.se > 0.05


def test_each_justice_keeps_their_own_unshrunk_estimate():
    # Nothing is pulled toward other justices or scored: one justice alone
    # is estimated, and a record of one vote a side has no estimate.
    rng = np.random.default_rng(3)
    est = estimates_by_justice({"alone": simulate(rng, 0.3, n=2000), "thin": [(1, 1, 0)] + [(0, 0, 0)] * 100})
    assert set(est) == {"alone"}
    assert est["alone"].raw == pytest.approx(0.3, abs=0.06)


def test_the_stored_fields_never_carry_a_score():
    e = fit(simulate(np.random.default_rng(5), 0.12, n=1000))
    fields = _loyalty_fields(e, 2025, None)
    assert fields["score_loyalty"] is None and fields["loyalty"] is None and fields["loyalty_se"] is None
    assert fields["appointer_effect"] == round(e.raw, 4) and fields["appointer_effect_se"] == round(e.se, 4)
    uncovered = _loyalty_fields(None, 2025, None)
    assert uncovered["score_loyalty"] is None and uncovered["appointer_effect"] is None


def test_no_justice_is_scored_or_ranked_even_with_a_v2_score_stored(db_session):
    """A v2 score left in the database is never served; null is "not
    scored", never 0; the list is by seniority, not by any number."""
    db_session.add_all([
        Justice(id="junior", name="Junior Justice", last_name="Junior", is_active=True, date_start="2020-10-27",
                score_loyalty=99.0, appointer_effect=-0.02, appointer_effect_se=0.05,
                loyalty_votes_in=40, loyalty_votes_out=90, loyalty_rate_in=0.5, loyalty_rate_out=0.52),
        Justice(id="senior", name="Senior Justice", last_name="Senior", is_active=True, date_start="1991-10-23",
                score_loyalty=10.0, appointer_effect=0.2, appointer_effect_se=0.05),
        Justice(id="chief", name="Chief Justice", last_name="Chief", role_title="Chief Justice", is_active=True,
                date_start="2005-09-29", score_loyalty=50.0),
    ])
    db_session.commit()
    board = get_justice_leaderboard(db_session)
    assert [e.id for e in board] == ["chief", "senior", "junior"]
    assert all(e.score.loyalty is None and e.score.overall is None for e in board)
    junior = get_justice(db_session, "junior").model_dump(by_alias=True)
    assert junior["score"] == {"loyalty": None, "overall": None}
    assert junior["loyalty"]["estimate"] == -0.02
    assert (junior["loyalty"]["ciLow"], junior["loyalty"]["ciHigh"]) == (-0.118, 0.078)
    assert get_justice(db_session, "chief").loyalty is None  # never measured: no estimate, no zero


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
    with patch("app.pipeline.justice_pipeline.fetch_justice_list", AsyncMock(return_value=None)), \
         patch("app.pipeline.justice_pipeline.fetch_current_justices", AsyncMock(return_value=[justice])), \
         patch("app.pipeline.justice_pipeline.fetch_case_votes", AsyncMock(return_value=[])), \
         patch("app.pipeline.justice_pipeline._measure_loyalty", AsyncMock(return_value=(None, "x could not be read"))):
        result = asyncio.run(run_justice_pipeline(db_session))
    assert result == {"justices": 1, "votes": 0, "loyalty_unmeasured": "x could not be read", "positions_unread": False}


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


class TestResolveAppointment:
    """The appointing president and party come from the presidents table,
    never a hand-typed list: a new president is known the night the roster
    names them."""

    @staticmethod
    def _presidents():
        from types import SimpleNamespace as P

        return [
            # The roster's (UCSB's) own spelling of the 41st president.
            P(id="bush-41", name="George Bush", party="R", term_start="1989-01-20", term_end="1993-01-20"),
            P(id="bush-43", name="George W. Bush", party="R", term_start="2001-01-20", term_end="2009-01-20"),
            P(id="obama-44", name="Barack Obama", party="D", term_start="2009-01-20", term_end="2017-01-20"),
            P(id="trump-45", name="Donald J. Trump", party="R", term_start="2017-01-20", term_end="2021-01-20"),
            P(id="biden-46", name="Joseph R. Biden", party="D", term_start="2021-01-20", term_end="2025-01-20"),
            P(id="new-48", name="A. New President", party="X", term_start="2029-01-20", term_end=None),
        ]

    @pytest.mark.parametrize("name, confirmed, expected", [
        pytest.param("Barack Obama", "2010-08-07", ("Barack Obama", "D"), id="oyez_names_the_president"),
        # Oyez's "George H. W. Bush" is nearer "George W. Bush" than the
        # roster's "George Bush"; the date decides (2026-10-08: every Bush
        # appointee had read as the 43rd president's).
        pytest.param("George H. W. Bush", "1991-10-23", ("George Bush", "R"), id="oyez_names_the_elder_bush"),
        # Oyez leaves Ketanji Brown Jackson's appointing president empty;
        # the old table then gave no party, and the Action Center filled "R".
        pytest.param("", "2022-06-30", ("Joseph R. Biden", "D"), id="no_name_resolves_by_who_was_in_office"),
        pytest.param("", "2030-03-01", ("A. New President", "X"),
                     id="a_president_the_code_never_heard_of_is_known_from_the_table"),
        # "George Bush" is as near one Bush as the other: the date decides.
        pytest.param("George Bush", "2006-01-31", ("George W. Bush", "R"),
                     id="a_close_but_ambiguous_name_falls_back_to_the_dates"),
        pytest.param("", "1700-01-01", ("", ""), id="unresolvable_gives_no_party"),
    ])
    def test_resolve_appointment(self, name, confirmed, expected):
        from app.pipeline.justice_pipeline import resolve_appointment

        assert resolve_appointment(name, confirmed, self._presidents()) == expected


def test_an_unreadable_martin_quinn_file_keeps_the_stored_positions():
    from app.pipeline.justice_pipeline import _loyalty_fields

    assert "ideal_points" not in _loyalty_fields(None, 2024, None, ideal_read=False)
    assert _loyalty_fields(None, 2024, [[2024, 1.0]])["ideal_points"] == "[[2024, 1.0]]"
    assert _loyalty_fields(None, 2024, None)["ideal_points"] is None  # read, and not in it


def test_scdb_case_votes_read_side_and_opinion():
    from app.pipeline.fetch.justice_records import scdb_case_votes
    case = dict(caseId="2024-001", docket="23-621", caseName="A v. B", term="2024", dateDecision="6/20/2025",
                decisionType="1", majOpinWriter="111", majVotes="6", minVotes="3", vote="1", opinion="1")
    rows = [
        {**case, "justice": "111", "justiceName": "JGRoberts", "majority": "2", "opinion": "2"},
        {**case, "justice": "108", "justiceName": "CThomas", "majority": "2", "vote": "3", "opinion": "2"},
        {**case, "justice": "114", "justiceName": "SSotomayor", "majority": "1", "vote": "2", "opinion": "2"},
        {**case, "justice": "115", "justiceName": "EKagan", "majority": "1", "vote": "2"},
        {**case, "justice": "116", "justiceName": "NMGorsuch", "majority": ""},  # did not take part
        {**case, "justiceName": "OldTerm", "term": "2019", "majority": "2"},
        {**case, "justiceName": "PerCuriamUnargued", "decisionType": "2", "majority": "2"},
    ]
    got = {r[5]: (r[6], r[7]) for r in scdb_case_votes(rows, 2022)}
    assert got == {
        "JGRoberts": ("majority", "majority"), "CThomas": ("majority", "concurrence"),
        "SSotomayor": ("minority", "dissent"), "EKagan": ("minority", "none"),
    }


def test_the_record_reads_the_database_for_the_terms_it_covers():
    from app.pipeline.justice_pipeline import scdb_vote_records
    cases = [["2024-001", "23-621", "A v. B", 2024, "2025-06-20", "CThomas", "majority", "none", 6, 3],
             ["2024-001", "23-621", "A v. B", 2024, "2025-06-20", "RRetired", "minority", "none", 6, 3],
             ["2021-001", "20-1", "C v. D", 2021, "2022-06-20", "CThomas", "majority", "none", 9, 0]]
    justices = [{"id": "clarence_thomas", "name": "Clarence Thomas", "last_name": "Thomas"}]
    [vote] = scdb_vote_records(cases, justices, {2024})
    assert vote["case_id"] == "scotus-2024-23-621" and vote["justice_id"] == "clarence_thomas"
    assert vote["is_close"] is False and vote["is_unanimous"] is False


def _oyez_justice(jid: str, start: int, end: int | None = None, chief: bool = False) -> dict:
    """One entry of Oyez's justice list: `end` None is a current role."""
    return {"identifier": jid, "name": jid.replace("_", " ").title(), "last_name": jid.split("_")[-1],
            "roles": [{"date_start": start, "date_end": 0 if end is None else end,
                       "role_title": "Chief Justice of the United States" if chief else "Associate Justice"}]}


class TestCourtRosterChanges:
    """Who sits on the Court comes from Oyez's list every run: a departure
    is marked, a new justice is added, and a list too short to be the
    Court retires nobody."""

    @staticmethod
    def _seat(db_session, ids):
        db_session.add_all([Justice(id=j, name=j, last_name=j, is_active=True) for j in ids])
        db_session.commit()

    @staticmethod
    def _run(db_session, listed):
        with patch("app.pipeline.justice_pipeline.fetch_justice_list", AsyncMock(return_value=listed)), \
             patch("app.pipeline.justice_pipeline.fetch_case_votes", AsyncMock(return_value=[])), \
             patch("app.pipeline.justice_pipeline._measure_loyalty", AsyncMock(return_value=(None, "not read"))):
            return asyncio.run(run_justice_pipeline(db_session))

    def test_a_vacancy_marks_the_departed_justice_and_keeps_the_record(self, db_session):
        nine = [f"justice_{n}" for n in range(9)]
        self._seat(db_session, nine)
        listed = [_oyez_justice(j, 1_000_000_000) for j in nine[1:]]
        listed.append(_oyez_justice(nine[0], 900_000_000, end=1_900_000_000))  # 2030-03-17 UTC
        result = self._run(db_session, listed)
        assert result["retired"] == ["justice_0"]
        gone = db_session.get(Justice, "justice_0")
        assert gone.is_active is False and gone.date_end == "2030-03-17"
        assert db_session.query(Justice).filter(Justice.is_active.is_(True)).count() == 8
        assert len(get_justice_leaderboard(db_session)) == 8

    def test_a_confirmed_justice_is_added_the_run_oyez_lists_them(self, db_session):
        eight = [f"justice_{n}" for n in range(8)]
        self._seat(db_session, eight)
        result = self._run(db_session, [_oyez_justice(j, 1_000_000_000) for j in eight + ["justice_new"]])
        assert "retired" not in result
        assert db_session.get(Justice, "justice_new").is_active is True
        assert db_session.query(Justice).filter(Justice.is_active.is_(True)).count() == 9

    def test_an_associate_made_chief_is_not_retired(self, db_session):
        nine = [f"justice_{n}" for n in range(9)]
        self._seat(db_session, nine)
        listed = [_oyez_justice(j, 1_000_000_000) for j in nine[1:]]
        promoted = _oyez_justice(nine[0], 1_000_000_000, chief=True)
        promoted["roles"].insert(0, {"date_start": 800_000_000, "date_end": 1_000_000_000})
        listed.append(promoted)
        assert "retired" not in self._run(db_session, listed)
        assert db_session.get(Justice, "justice_0").is_active is True

    def test_a_list_shorter_than_a_quorum_retires_nobody_and_alerts(self, db_session):
        nine = [f"justice_{n}" for n in range(9)]
        self._seat(db_session, nine)
        with patch("app.services.justice_service.send_ops_alert") as alert:
            result = self._run(db_session, [_oyez_justice(j, 1_000_000_000) for j in nine[:3]])
        assert "retired" not in result
        assert db_session.query(Justice).filter(Justice.is_active.is_(True)).count() == 9
        assert alert.call_args.kwargs["condition"] == "justice-list-short"

    def test_an_unreadable_list_changes_nothing(self, db_session):
        self._seat(db_session, ["justice_0"])
        assert self._run(db_session, None)["justices"] == 0
        assert db_session.get(Justice, "justice_0").is_active is True

    def test_the_nightly_check_sees_a_change_and_not_an_outage(self, db_session):
        from app.pipeline.justice_pipeline import court_roster_changed

        self._seat(db_session, ["justice_0", "justice_1"])

        def check(listed):
            with patch("app.pipeline.justice_pipeline.fetch_justice_list", AsyncMock(return_value=listed)):
                return asyncio.run(court_roster_changed(db_session))

        same = [_oyez_justice("justice_0", 1), _oyez_justice("justice_1", 1)]
        assert check(same) is False
        assert check(same + [_oyez_justice("justice_2", 2)]) is True
        assert check([same[0], _oyez_justice("justice_1", 1, end=2)]) is True
        assert check(None) is False
