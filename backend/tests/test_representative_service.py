"""Tests for representative_service.build_rep_response's RepresentativeSchema
construction (see PR #122 — converted from a hand-typed dict, which had no
validation).

Production incident, 2026-07-16: the conversion briefly 500'd every
representative detail page because DonorSchema.type's Literal was missing
"SKIP" — a real, first-class donor-classification outcome (see
donor_classifier_ai.py / normalize_finance.py) that House data hits but no
senator happened to have at the time. These tests pin that gap shut and
guard against the same class of surprise for any other Literal-constrained
field build_rep_response feeds.
"""

import json

from app.models import RepDonor, RepKeyVote, RepLobbyingMatch, Representative, RollCall
from app.services.representative_service import build_rep_response


def _make_rep(db_session, rep_id: str, donor_types: list[str]) -> Representative:
    rep = Representative(
        id=rep_id, name="Test Rep", state="CA", district=12, party="D",
        total_raised=500_000, total_from_pacs=100_000, small_donor_percentage=25,
        years_in_office=2,
    )
    db_session.add(rep)
    for i, donor_type in enumerate(donor_types):
        # "PAC" in the name keeps _fixup_donor_type's read-time safety net
        # from rewriting a type="PAC" row to "Org/Employees" — irrelevant to
        # the other types, which it always passes through unchanged.
        db_session.add(RepDonor(
            representative_id=rep_id, name=f"Donor {i} PAC", total=1_000, type=donor_type, rank=i,
        ))
    db_session.commit()
    db_session.refresh(rep)
    return rep


class TestBuildRepResponseDonorTypes:
    def test_every_donor_type_seen_in_the_pipeline_is_accepted(self, db_session):
        # donor_classifier_ai.py / normalize_finance.py's full output vocabulary,
        # including "SKIP" — the one that 500'd every House detail page
        # (see the module docstring).
        donor_types = [
            "PAC", "Individual", "SuperPAC", "Org/Employees",
            "Party/Ideological", "CandidateAffiliated", "Self-Funded", "SKIP",
        ]
        rep = _make_rep(db_session, "test-rep-2", donor_types)
        result = build_rep_response(rep, db_session)
        assert [d.type for d in result.funding.top_donors] == donor_types


class TestDonorVoteBillsAreReadable:
    def test_a_synthetic_roll_call_id_becomes_its_bill_or_title(self, db_session):
        """The donor-vote panel printed "HouseRC-2026-314" chips. Each is the
        roll call's own bill where the record has one, else the vote's title;
        a Senate-style bill number passes through."""
        rep = _make_rep(db_session, "test-rep-3", [])
        db_session.add_all([
            RollCall(chamber="house", congress=119, session=2, number=314, date="2026-09-16",
                     question="On Passage", bill_id="HR.5823"),
            RepKeyVote(representative_id=rep.id, bill_name="Example Savings Act", bill_id="HouseRC-2026-314",
                       date="2026-09-16", vote="Yea", roll_call="house-119-2-314"),
            RepKeyVote(representative_id=rep.id, bill_name="Example Rule Resolution", bill_id="HouseRC-2026-300",
                       date="2026-09-01", vote="Nay", roll_call="house-119-2-300"),
        ])
        db_session.add(RepLobbyingMatch(
            representative_id=rep.id, lobbyist_org="Org", industry="LABOR", lobbying_spend=0,
            donation_to_representative=1000, description="",
            bills_influenced=json.dumps(["HouseRC-2026-314", "HouseRC-2026-300", "S. 12", "HouseRC-2026-999"]),
        ))
        db_session.commit()
        db_session.refresh(rep)
        bills = build_rep_response(rep, db_session).lobbying_matches[0].bills_influenced
        # 999 is no vote of this member's: nothing readable to show, so dropped.
        assert bills == ["H.R. 5823", "Example Rule Resolution", "S. 12"]

    def test_each_related_bill_carries_the_members_vote_and_roll_call(self, db_session):
        """The panel had no vote for any of its 129 matches (2026-10-15):
        each related bill now carries the member's Yea or Nay and the roll
        call it was cast in, once per roll call, in the match's order."""
        rep = _make_rep(db_session, "test-rep-4", [])
        db_session.add_all([
            RollCall(chamber="house", congress=119, session=2, number=314, date="2026-09-16",
                     question="On Passage", title="Example Savings Act", bill_id="HR.5823"),
            RepKeyVote(representative_id=rep.id, bill_name="Example Savings Act", bill_id="HouseRC-2026-314",
                       date="2026-09-16", vote="Yea", roll_call="house-119-2-314", vote_category="key"),
            # The same vote listed again under "recent": counted once.
            RepKeyVote(representative_id=rep.id, bill_name="Example Savings Act", bill_id="HouseRC-2026-314",
                       date="2026-09-16", vote="Yea", roll_call="house-119-2-314", vote_category="recent"),
            # Stored before its roll call was recorded: the vote, no roll call.
            RepKeyVote(representative_id=rep.id, bill_name="Example Clean Water Act", bill_id="S.12",
                       date="2026-03-01", vote="Nay"),
            RepKeyVote(representative_id=rep.id, bill_name="Unrelated", bill_id="HR.1", date="2026-03-01",
                       vote="Yea"),
        ])
        db_session.add(RepLobbyingMatch(
            representative_id=rep.id, lobbyist_org="Org", industry="LABOR", lobbying_spend=0,
            donation_to_representative=1000, description="",
            bills_influenced=json.dumps(["S.12", "HouseRC-2026-314"]),
        ))
        db_session.commit()
        db_session.refresh(rep)
        votes = build_rep_response(rep, db_session).lobbying_matches[0].related_votes
        assert [(v.bill_id, v.label, v.vote, v.title) for v in votes] == [
            ("S.12", "S. 12", "Nay", "Example Clean Water Act"),
            ("HR.5823", "H.R. 5823", "Yea", "Example Savings Act"),
        ]
        assert votes[0].roll_call is None
        assert (votes[1].roll_call["chamber"], votes[1].roll_call["number"]) == ("house", 314)
