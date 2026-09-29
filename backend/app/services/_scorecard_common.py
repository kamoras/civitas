"""Shared assembly for the on-demand score-breakdown endpoints.

``get_senator_score_breakdown`` and ``get_representative_score_breakdown``
built the identical ``score_calculator.explain_scores`` input dict from a
loaded ``Senator``/``Representative``, differing only in which lobbying
donation attribute to read and whether the entity carries a district. This is
the one canonical copy (mirrors the ``pagination``/``score_trends`` shared
helpers).
"""

from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.analyze.party_line_record import load_record
from app.pipeline.analyze.score_calculator import explain_scores
from app.pipeline.transform.normalize_votes import stored_vote
from app.services.bill_record import roll_call_summaries


def _vote_dict(v: Any) -> dict:
    return stored_vote(v.id, v.bill_id, v.voted_with_party)


def build_score_breakdown_entity(entity: Any, *, lobbying_donation_attr: str) -> dict:
    """Assemble the ``score_calculator.explain_scores`` input dict from a loaded
    ``Senator`` or ``Representative`` ORM object.

    ``lobbying_donation_attr`` is ``"donation_to_senator"`` or
    ``"donation_to_representative"``; both are emitted under the same output
    key (``"donationToSenator"``) the scoring formula reads. ``district`` is
    read via ``getattr`` so a ``Senator`` (which has no district column) yields
    ``None``.
    """
    voting_record = {
        # Only differs from party for an Independent (the party they caucus with).
        "effectiveParty": getattr(entity, "caucus_party", None),
        "keyVotes": [_vote_dict(v) for v in entity.key_votes if v.vote_category == "key"],
        "recentVotes": [_vote_dict(v) for v in entity.key_votes if v.vote_category == "recent"],
        "partyLineRecord": load_record(entity.party_line_record),
    }

    funding = {
        "totalRaised": entity.total_raised,
        "totalContributions": entity.total_contributions,
        "totalFromPACs": entity.total_from_pacs,
        "smallDonorPercentage": entity.small_donor_percentage,
        "topDonors": [
            {"name": d.name, "total": d.total, "type": d.type, "committeeType": d.committee_type}
            for d in entity.donors
        ],
        "industryBreakdown": [
            {"industry": ind.industry, "total": ind.total}
            for ind in entity.industry_donations
        ],
    }

    lobbying_matches = [
        {
            "donationToSenator": getattr(lm, lobbying_donation_attr),
            "isConsensusVote": lm.is_consensus_vote,
        }
        for lm in entity.lobbying_matches
    ]

    sponsored_bills = [
        {
            "billType": sb.bill_type,
            "congress": sb.congress,
            "isLaw": sb.is_law,
            "latestAction": sb.latest_action,
            # The pipeline scores on this (bill_stage.py, from Congress.gov's
            # action codes). Without it the breakdown fell back to inferring
            # stages from latestAction prose and could credit a different
            # stage than the stored score did.
            "stage": sb.stage,
            "commemorative": sb.commemorative,
        }
        for sb in entity.sponsored_bills
    ]

    return {
        "funding": funding,
        "votingRecord": voting_record,
        "lobbyingMatches": lobbying_matches,
        "sponsoredBills": sponsored_bills,
        "state": entity.state,
        "party": entity.party,
        "district": getattr(entity, "district", None),
        "bipartisanshipScore": entity.bipartisanship_score,
        "attractedBipartisanshipScore": getattr(entity, "attracted_bipartisanship_score", None),
        "leadershipScore": entity.leadership_score,
        "yearsInOffice": entity.years_in_office,
        # Representatives only (Senator has no column): see Representative.sworn_date.
        "swornDate": getattr(entity, "sworn_date", None),
        "ideologyScore": entity.ideology_score,
        "bioguideId": entity.bioguide_id,
    }


def score_breakdown(db: Session, entity: Any, *, lobbying_donation_attr: str) -> dict:
    """explain_scores for a loaded Senator or Representative, with the
    breaks its Constituent Alignment counts (and the flank breaks it
    doesn't) as the chamber recorded each roll call: the member's vote and
    how each party split. Served with the score so the list and the number
    can't disagree."""
    entity_dict = build_score_breakdown_entity(entity, lobbying_donation_attr=lobbying_donation_attr)
    breakdown = explain_scores(entity_dict)
    record = entity_dict["votingRecord"]["partyLineRecord"]
    facts = (breakdown.get("constituentAlignment") or {}).get("facts")
    if record and facts is not None:
        listed = (record.get("breaks") or []) + (record.get("flankBreaks") or [])
        summaries = roll_call_summaries(db, [b.get("rollCall") for b in listed])
        for key, served_as in (("breaks", "breakVotes"), ("flankBreaks", "flankBreakVotes")):
            facts[served_as] = [
                {"vote": b.get("vote"), "rollCall": summaries.get(b.get("rollCall"))}
                for b in record.get(key) or []
            ]
    return breakdown
