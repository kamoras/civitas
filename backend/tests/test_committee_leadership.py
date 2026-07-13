"""Tests for committee membership / chamber leadership data.

Covers the loader in app/pipeline/transform/committee_data.py (graceful
fallback when the JSON caches are absent) and the two API call sites in
app/api/politicians.py that surface leadershipTitle/committees for both
the directory listing and the individual profile endpoint.
"""

import json

from app.api.politicians import _build_identity, get_politician, list_politicians
from app.models import Representative, Senator
from app.pipeline.transform import committee_data


def test_missing_cache_files_return_empty_not_error(tmp_path, monkeypatch):
    committee_data.clear_committee_data_cache()
    monkeypatch.setattr(committee_data, "_DATA_DIR", tmp_path)

    assert committee_data.load_committee_membership() == {}
    assert committee_data.load_leadership_roles() == {}

    committee_data.clear_committee_data_cache()


def test_loaders_parse_present_cache_files(tmp_path, monkeypatch):
    committee_data.clear_committee_data_cache()
    monkeypatch.setattr(committee_data, "_DATA_DIR", tmp_path)

    (tmp_path / "committee_membership.json").write_text(json.dumps({
        "membership": {"T000250": [{"committeeName": "Senate Committee on Finance", "chamber": "senate", "title": None}]},
    }))
    (tmp_path / "leadership_roles.json").write_text(json.dumps({
        "roles": {"T000250": "Senate Majority Leader"},
    }))

    assert committee_data.load_committee_membership() == {
        "T000250": [{"committeeName": "Senate Committee on Finance", "chamber": "senate", "title": None}],
    }
    assert committee_data.load_leadership_roles() == {"T000250": "Senate Majority Leader"}

    committee_data.clear_committee_data_cache()


def _make_senator(db_session, **overrides) -> Senator:
    defaults = dict(
        id="test-senator", bioguide_id="T000250", name="Test Senator", state="OH",
        party="D", years_in_office=4, initials="TS",
        leadership_title="Senate Majority Leader",
        committees=json.dumps([{"committeeName": "Senate Committee on Finance", "chamber": "senate", "title": "Chairman"}]),
    )
    defaults.update(overrides)
    senator = Senator(**defaults)
    db_session.add(senator)
    db_session.commit()
    return senator


def _make_representative(db_session, **overrides) -> Representative:
    defaults = dict(
        id="test-rep", bioguide_id="J000294", name="Test Rep", state="NY", district=1,
        party="R", years_in_office=2, initials="TR",
        leadership_title=None,
        committees="[]",
    )
    defaults.update(overrides)
    rep = Representative(**defaults)
    db_session.add(rep)
    db_session.commit()
    return rep


def test_directory_exposes_leadership_title_for_senate(db_session):
    _make_senator(db_session)
    response = list_politicians(branch="senate", state=None, party=None, q=None, db=db_session)
    results = json.loads(response.body)
    assert results[0]["leadershipTitle"] == "Senate Majority Leader"


def test_directory_leadership_title_none_when_no_role(db_session):
    _make_representative(db_session)
    response = list_politicians(branch="house", state=None, party=None, q=None, db=db_session)
    results = json.loads(response.body)
    assert results[0]["leadershipTitle"] is None


def test_build_identity_includes_committees_for_senate(db_session):
    senator = _make_senator(db_session)
    identity = _build_identity("senate", senator)
    assert identity["leadershipTitle"] == "Senate Majority Leader"
    assert identity["committees"] == [
        {"committeeName": "Senate Committee on Finance", "chamber": "senate", "title": "Chairman"},
    ]


def test_build_identity_includes_committees_for_house(db_session):
    rep = _make_representative(db_session, committees=json.dumps(
        [{"committeeName": "House Committee on Ways and Means", "chamber": "house", "title": None}],
    ))
    identity = _build_identity("house", rep)
    assert identity["committees"] == [
        {"committeeName": "House Committee on Ways and Means", "chamber": "house", "title": None},
    ]


def test_profile_endpoint_surfaces_committee_data(db_session):
    _make_senator(db_session)
    response = get_politician("test-senator", db=db_session)
    body = json.loads(response.body)
    assert body["identity"]["leadershipTitle"] == "Senate Majority Leader"
    assert body["identity"]["committees"][0]["title"] == "Chairman"
