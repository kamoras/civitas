"""A candidate's party on the ballot page: the state list's printing for a
confirmed nominee, and the FEC's own label for an FEC code."""

from app.pipeline.fetch.state_candidates_common import ballot_party, fec_party_label


def test_the_fec_table_labels_a_code_and_not_a_typo():
    assert fec_party_label("TX") == "Taxpayers"
    assert fec_party_label("PG") == "Pacific Green"
    assert fec_party_label("08") is None  # typed by a filer, shown as filed
    assert fec_party_label(None) is None


def test_the_state_list_prints_the_party():
    assert ballot_party({"party": "R"}) == "REP"
    assert ballot_party({"party": "O", "party_label": "Freedom  and Unity"}) == "FREEDOM AND UNITY"
    assert ballot_party({"party": ""}) is None


def test_a_confirmed_nominee_shows_the_ballot_s_party(db_session):
    from app.api.elections import _candidate_summary
    from app.models import Candidate
    from app.pipeline.fetch.state_candidates import _note_ballot_name

    cand = Candidate(id="H2XX08090", race_id="2026-HOUSE-XX-8", name="DOE, ROBERT", party="08")
    db_session.add(cand)
    db_session.commit()
    assert _candidate_summary(cand)["party"] == "08"

    _note_ballot_name(db_session, cand, {"display_name": "Robert Doe", "party": "R"})
    summary = _candidate_summary(cand)
    assert (summary["party"], summary["partyGroup"], summary["ballotName"]) == ("REP", "REP", "Robert Doe")


def test_link_members_follows_the_crosswalk_and_ignores_a_failed_read(db_session):
    from app.models import Candidate, Race
    from app.pipeline.election_pipeline import link_members

    db_session.add(Race(id="2026-HOUSE-GA-6", cycle_year=2026, office="H", state="GA", district=6))
    db_session.add(Candidate(id="H6GA06001", race_id="2026-HOUSE-GA-6", name="MCBRIDE, LUCY", party="DEM"))
    db_session.add(Candidate(id="H6GA06002", race_id="2026-HOUSE-GA-6", name="ROE, JAN", party="REP"))
    db_session.commit()

    assert link_members(db_session, 2026, {"M000001": ["S2GA00001", "H6GA06001"]}) == 1
    assert db_session.get(Candidate, "H6GA06001").member_bioguide == "M000001"
    assert link_members(db_session, 2026, {}) == 0  # the crosswalk couldn't be read
    assert db_session.get(Candidate, "H6GA06001").member_bioguide == "M000001"
