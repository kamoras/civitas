"""Member ids (app/member_ids.py): the slug, its uniqueness across both
chambers, and renaming a member in place without orphaning anything."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.database import Base, get_db
from app.member_ids import (
    CHAMBER_HOUSE,
    CHAMBER_SENATE,
    assign_member_ids,
    member_slug,
    rename_member,
    resolve_member_id,
)
from app.models import (
    ActionIssue,
    BroadcastPost,
    BskySenatorSpotlight,
    Donor,
    ExploreDocument,
    FinancialDisclosure,
    KeyVote,
    MemberIdAlias,
    Representative,
    RepDonor,
    ScoreSnapshot,
    Senator,
)
from app.pipeline.member_lifecycle import purge_departed_members
from app.pipeline.transform.normalize_members import normalize_house_members, normalize_members
from app.time_utils import utcnow


@pytest.mark.parametrize("raw,slug", [
    ("Doe, Jane", "jane-doe"),
    ("Doe, Jane Q.", "jane-doe"),  # not the middle initial
    ("Doe, J. Quincy", "quincy-doe"),  # an initial is passed over for a name
    ("Doe, J.", "j-doe"),  # unless it is all there is
    ('Doe, Jonathan Q. "Jack"', "jonathan-doe"),  # nor the quoted nickname
    ("Doe, John, Jr.", "john-doe"),  # nor the suffix
    ("Doe, John Q. Sr.", "john-doe"),
    ("Doe, John III", "john-doe"),
    ("Doe Roe, Jane", "jane-doe-roe"),  # the whole surname
    ("Doe-Roe, Jane Q.", "jane-doe-roe"),
    ("Núñez, Ana María", "ana-nunez"),  # accents folded, not deleted
    ('Pérez, Jesús G. "Chucho"', "jesus-perez"),
    ("De La Peña, Andrés", "andres-de-la-pena"),
    ("O'Doe, Seán", "sean-o-doe"),
    ("Jane Doe", "jane-doe"),  # no comma: "First ... Last"
])
def test_member_slug(raw, slug):
    assert member_slug(raw) == slug


def test_normalize_gives_first_last_in_both_chambers():
    senate = normalize_members([{
        "bioguideId": "D000001", "name": "Doe Roe, Jane Q.", "state": "Ohio", "chamber": "Senate",
    }])
    house = normalize_house_members([{
        "bioguideId": "N000001", "name": "Núñez, Ana María", "state": "New Mexico",
        "terms": {"item": [{"chamber": "House of Representatives", "startYear": 2023}]},
    }])
    assert [m["id"] for m in senate] == ["jane-doe-roe"]
    assert [m["id"] for m in house] == ["ana-nunez"]


def _member(slug_of, bio, state="OH"):
    return {"id": member_slug(slug_of), "bioguideId": bio, "state": state}


def _senator(db, sid, bio, state="OH", **kw):
    db.add(Senator(id=sid, bioguide_id=bio, name=sid, state=state, party="D", **kw))


def _rep(db, rid, bio, state="OH", **kw):
    db.add(Representative(id=rid, bioguide_id=bio, name=rid, state=state, party="D", district=1, **kw))


def test_a_changed_id_is_renamed_in_place_everywhere_it_is_stored(db_session):
    _senator(db_session, "q-doe", "D000001")
    db_session.add_all([
        Donor(senator_id="q-doe", name="Acme PAC", total=1.0, type="PAC"),
        KeyVote(senator_id="q-doe", bill_name="A bill", bill_id="S.1", date="2026-01-01", vote="Yea"),
        FinancialDisclosure(senator_id="q-doe", filing_id="f1", filed_date="2026-01-01"),
        ScoreSnapshot(entity_type="senator", entity_id="q-doe", date="2026-01-01", overall_score=50.0),
        BskySenatorSpotlight(senator_id="q-doe", chamber="senate"),
        ExploreDocument(doc_type="Senate Floor Speech", source="GovInfo", title="A speech",
                        date="2026-01-01", politician_id="q-doe", chamber="Senate"),
        BroadcastPost(kind="spotlight", subject="member:senate:q-doe", title="t", text="t",
                      url="https://civitas-research.org/politicians/q-doe", published_at=utcnow()),
        ActionIssue(date="2026-01-01", rank=1, title="An issue",
                    related_senators=json.dumps([{"id": "q-doe", "chamber": "senate"}, {"id": "x-y"}]),
                    related_officials=json.dumps([{"id": "q-doe", "chamber": "senate", "branch": "senate"}])),
    ])
    # Someone else's references under the same string, in the other chamber:
    # never moved.
    db_session.add_all([
        ScoreSnapshot(entity_type="representative", entity_id="q-doe", date="2026-01-01", overall_score=50.0),
        ExploreDocument(doc_type="House Floor Speech", source="GovInfo", title="Another speech",
                        date="2026-01-01", politician_id="q-doe", chamber="House"),
    ])
    db_session.commit()

    members = [_member("Doe, Jane Q.", "D000001")]
    assert assign_member_ids(db_session, CHAMBER_SENATE, members) == {"q-doe": "jane-doe"}
    db_session.commit()

    assert members[0]["id"] == "jane-doe"
    assert [s.id for s in db_session.query(Senator).all()] == ["jane-doe"]  # renamed, not re-added
    for model in (Donor, KeyVote, FinancialDisclosure):
        assert db_session.query(model).one().senator_id == "jane-doe"
    snaps = {(s.entity_type, s.entity_id) for s in db_session.query(ScoreSnapshot)}
    assert snaps == {("senator", "jane-doe"), ("representative", "q-doe")}
    assert db_session.query(BskySenatorSpotlight).one().senator_id == "jane-doe"
    docs = {(d.chamber, d.politician_id) for d in db_session.query(ExploreDocument)}
    assert docs == {("Senate", "jane-doe"), ("House", "q-doe")}
    post = db_session.query(BroadcastPost).one()
    assert post.subject == "member:senate:jane-doe"
    assert post.url.endswith("/q-doe")  # what was published stays as published
    issue = db_session.query(ActionIssue).one()
    assert [e["id"] for e in json.loads(issue.related_senators)] == ["jane-doe", "x-y"]
    assert [e["id"] for e in json.loads(issue.related_officials)] == ["jane-doe"]

    alias = db_session.get(MemberIdAlias, "q-doe")
    assert (alias.new_id, alias.bioguide_id) == ("jane-doe", "D000001")
    assert resolve_member_id(db_session, "q-doe") == "jane-doe"
    assert resolve_member_id(db_session, "jane-doe") == "jane-doe"


def test_every_foreign_key_child_is_covered():
    """The rename finds child tables from the models' metadata; this pins
    that it sees them all, so one added later is moved too."""
    children = {
        (t.name, fk.parent.name)
        for t in Base.metadata.sorted_tables for fk in t.foreign_keys
        if fk.column.table.name in ("senators", "representatives")
    }
    assert ("donors", "senator_id") in children
    assert ("rep_stock_trades", "representative_id") in children
    assert ("financial_disclosures", "senator_id") in children
    assert ("financial_disclosures", "representative_id") in children


def test_rerun_with_the_same_ids_renames_nothing(db_session):
    _senator(db_session, "jane-doe", "D000001")
    db_session.commit()
    assert assign_member_ids(db_session, CHAMBER_SENATE, [_member("Doe, Jane", "D000001")]) == {}
    assert db_session.query(MemberIdAlias).count() == 0


def test_the_same_person_keeps_one_id_across_chambers(db_session):
    """A former representative now in the Senate: renaming the senator
    renames the departed House row too, so /politicians/<id> still reaches
    both records."""
    _senator(db_session, "q-doe", "D000001")
    _rep(db_session, "q-doe", "D000001", is_current=False, left_office_date="2025-01-03")
    db_session.add(RepDonor(representative_id="q-doe", name="Acme PAC", total=1.0, type="PAC"))
    db_session.commit()

    assign_member_ids(db_session, CHAMBER_SENATE, [_member("Doe, Jane Q.", "D000001")])
    db_session.commit()

    assert db_session.query(Senator).one().id == "jane-doe"
    assert db_session.query(Representative).one().id == "jane-doe"
    assert db_session.query(RepDonor).one().representative_id == "jane-doe"


def test_two_people_with_one_slug_are_told_apart_by_state_then_bioguide(db_session):
    # Already holds the plain slug (a serving representative): keeps it.
    _rep(db_session, "jane-doe", "D000009", state="TX")
    db_session.commit()

    members = [
        _member("Doe, Jane", "D000001", state="OH"),
        _member("Doe, Jane Q.", "D000002", state="TX"),
    ]
    assign_member_ids(db_session, CHAMBER_SENATE, members)
    assert [m["id"] for m in members] == ["jane-doe-oh", "jane-doe-tx"]

    members.append(_member("Doe, Jane R.", "D000003", state="OH"))
    assign_member_ids(db_session, CHAMBER_SENATE, members)
    assert [m["id"] for m in members] == ["jane-doe-oh", "jane-doe-tx", "jane-doe-oh-d000003"]


def test_a_holder_keeps_a_suffixed_id_when_the_plain_one_frees_up(db_session):
    """Nobody's URL moves because of someone else: once disambiguated, a
    member stays put after the other person leaves."""
    _senator(db_session, "jane-doe-oh", "D000001")
    db_session.commit()
    members = [_member("Doe, Jane", "D000001")]
    assert assign_member_ids(db_session, CHAMBER_SENATE, members) == {}
    assert members[0]["id"] == "jane-doe-oh"


def test_ids_can_change_hands_within_one_run(db_session):
    """One member takes the id another gives up, whichever order they're
    processed in (the rename goes through a placeholder)."""
    _senator(db_session, "jane-doe", "D000002", state="TX")  # an old id that is now someone else's slug
    _senator(db_session, "q-doe", "D000001")
    db_session.add(Donor(senator_id="jane-doe", name="Tx PAC", total=1.0, type="PAC"))
    db_session.add(Donor(senator_id="q-doe", name="Oh PAC", total=1.0, type="PAC"))
    db_session.commit()

    members = [_member("Doe, Jane", "D000001"), _member("Doe, John", "D000002", state="TX")]
    renames = assign_member_ids(db_session, CHAMBER_SENATE, members)
    db_session.commit()

    assert renames == {"q-doe": "jane-doe", "jane-doe": "john-doe"}
    owner = {d.name: d.senator_id for d in db_session.query(Donor)}
    assert owner == {"Oh PAC": "jane-doe", "Tx PAC": "john-doe"}
    # "jane-doe" is a live id again: it answers as its holder, not as an alias.
    assert resolve_member_id(db_session, "jane-doe") == "jane-doe"
    assert resolve_member_id(db_session, "q-doe") == "jane-doe"


def test_aliases_follow_later_renames(db_session):
    _senator(db_session, "a-doe", "D000001")
    db_session.commit()
    rename_member(db_session, CHAMBER_SENATE, "a-doe", "ann-doe", "D000001")
    rename_member(db_session, CHAMBER_SENATE, "ann-doe", "ann-doe-roe", "D000001")  # a name change
    db_session.commit()
    assert {a.old_id: a.new_id for a in db_session.query(MemberIdAlias)} == {
        "a-doe": "ann-doe-roe", "ann-doe": "ann-doe-roe",
    }
    # And back: the id held again is no alias of itself.
    rename_member(db_session, CHAMBER_SENATE, "ann-doe-roe", "ann-doe", "D000001")
    db_session.commit()
    assert {a.old_id: a.new_id for a in db_session.query(MemberIdAlias)} == {
        "a-doe": "ann-doe", "ann-doe-roe": "ann-doe",
    }


def test_purge_strips_a_member_from_both_issue_lists(db_session):
    _rep(db_session, "long-gone", "G000001", is_current=False, left_office_date="2025-01-01")
    db_session.add(ActionIssue(
        date="2026-07-27", rank=1, title="An issue",
        related_senators=json.dumps([{"id": "long-gone", "chamber": "house"}]),
        related_officials=json.dumps([{"id": "long-gone", "chamber": "house", "branch": "house"},
                                      {"id": "pres-1", "branch": "president"}]),
    ))
    db_session.flush()
    purge_departed_members(db_session, CHAMBER_HOUSE, today="2026-07-27")
    db_session.flush()
    issue = db_session.query(ActionIssue).one()
    assert json.loads(issue.related_senators) == []
    assert [e["id"] for e in json.loads(issue.related_officials)] == ["pres-1"]


@pytest.fixture
def client(db_session):
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_api_answers_an_old_id_under_the_current_one(db_session, client):
    _senator(db_session, "jane-doe", "D000001")
    db_session.add(MemberIdAlias(old_id="q-doe", new_id="jane-doe", bioguide_id="D000001"))
    db_session.add(ScoreSnapshot(entity_type="senator", entity_id="jane-doe", date="2026-01-01",
                                 overall_score=50.0, score_1=50, score_2=50, score_3=50, score_4=50, score_5=50))
    db_session.commit()

    profile = client.get("/api/politicians/q-doe")
    assert profile.status_code == 200 and profile.json()["id"] == "jane-doe"
    assert client.get("/api/senators/q-doe").json()["id"] == "jane-doe"
    assert client.get("/api/senators/q-doe/history").status_code == 200
    public = client.get("/api/public/v1/senators/q-doe").json()
    assert (public["id"], public["siteUrl"].rsplit("/", 1)[-1]) == ("jane-doe", "jane-doe")
    assert client.get("/api/public/v1/senators/q-doe/history").json()["id"] == "jane-doe"
    assert client.get("/api/politicians/nobody-here").status_code == 404
    # The sitemap lists the current id only.
    ids = [p["id"] for p in client.get("/api/sitemap").json()["politicians"]]
    assert "jane-doe" in ids and "q-doe" not in ids
