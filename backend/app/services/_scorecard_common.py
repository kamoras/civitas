"""Shared assembly for the on-demand score-breakdown endpoints.

``get_senator_score_breakdown`` and ``get_representative_score_breakdown``
built the identical ``score_calculator.explain_scores`` input dict from a
loaded ``Senator``/``Representative``, differing only in which lobbying
donation attribute to read and whether the entity carries a district. This is
the one canonical copy (mirrors the ``pagination``/``score_trends`` shared
helpers).
"""

import string
from datetime import date, timedelta, timezone
from typing import Any

from sqlalchemy import ColumnElement, String, cast, func, or_
from sqlalchemy.orm import Query, Session

from app.config import settings
from app.district_lean import lean_for as computed_lean_for
from app.models import RollCall
from app.pipeline.analyze.party_line_record import load_record
from app.pipeline.analyze.score_calculator import (
    bills_inputs_from_status,
    explain_scores,
    funding_share_base,
    _seat_pvi,
    party_line_tally,
    stored_bills_status,
)
from app.pipeline.fetch.district_pvi import lines_congress, lines_of
from app.pipeline.transform.normalize_votes import stored_vote
from app.services.bill_record import roll_call_summaries, typed_bill_number
from app.services.score_findings import add_findings
from app.services.word_search import matching_keys
from app.time_utils import CONGRESS_TZ, utcnow

# The votes list's date ranges (`period` on the votes endpoints).
VOTE_PERIOD_PATTERN = "^(all|congress|month)$"
# "month" is the last 30 days, not the calendar month: a calendar month is
# empty for its first days, and for every day of a recess month, when a
# reader asking what a member voted on lately still wants the answer.
VOTE_PERIOD_MONTH_DAYS = 30



def pac_share_pct(entity: Any) -> float:
    """PAC money as a percentage of contributions (0-100, unrounded), over
    the same base every funding share uses (score_calculator.
    funding_share_base); 0 when nothing was raised. The one place this is
    worked out for display: the site shows it and sorts by it, rather than
    each page dividing for itself."""
    base = funding_share_base({"totalContributions": entity.total_contributions, "totalRaised": entity.total_raised})
    return (entity.total_from_pacs or 0) / base * 100 if base > 0 else 0.0

def vote_period_start(period: str) -> str | None:
    """The first date (YYYY-MM-DD, the stored votes' format) a votes list
    over `period` includes, or None for every vote: "month" the last
    VOTE_PERIOD_MONTH_DAYS days, counted on the Capitol's calendar;
    "congress" from January 3 of the Congress in office (the 20th
    Amendment; settings.CURRENT_CONGRESS, the scored window's Congress)."""
    if period == "month":
        today = utcnow().replace(tzinfo=timezone.utc).astimezone(CONGRESS_TZ).date()
        return (today - timedelta(days=VOTE_PERIOD_MONTH_DAYS)).isoformat()
    if period == "congress":
        return date(1789 + (settings.CURRENT_CONGRESS - 1) * 2, 1, 3).isoformat()
    return None


def vote_search(base_q: Query, model: Any, q: str) -> ColumnElement[bool]:
    """The condition for a member's stored votes (KeyVote / RepKeyVote)
    matching a reader's search: the bill number as typed in any spelling
    ("HR 123", "h.r.123", "hr12" while typing — matched as a prefix with
    the dots and spaces left out, so "S 1" doesn't find "S. Res. 15", and
    only when it has a digit), or every word of it in the bill's title, the
    vote's description or its roll call's question and title, matched as
    the bill list matches titles (word_search: stemmed, as word starts, in
    any order), so "flag burning" finds a vote on the "Flag Burners Act".

    `base_q` is the member's own (indexed) query: the words are matched
    over its few hundred rows, and their roll calls once, in Python."""
    text = q.strip()
    votes = base_q.with_entities(model.id, model.bill_name, model.description, model.roll_call).all()
    ref = (RollCall.chamber + "-" + cast(RollCall.congress, String) + "-" + cast(RollCall.session, String)
           + "-" + cast(RollCall.number, String))
    refs = {v.roll_call for v in votes if v.roll_call}
    asked = {
        r: f"{question or ''} {title or ''}"
        for r, question, title in (
            base_q.session.query(ref, RollCall.question, RollCall.title).filter(ref.in_(refs)).all()
            if refs else []
        )
    }
    found = matching_keys(
        ((str(v.id), v.bill_name or "", v.description or "", asked.get(v.roll_call, "")) for v in votes),
        n_texts=3,
        q=text,
    )
    conditions = [model.id.in_([int(k) for k in found])]
    number = typed_bill_number(text)
    if number:
        bare = func.replace(func.replace(func.upper(model.bill_id), ".", ""), " ", "")
        if number.isdigit():  # "123" alone: the number, of any bill type
            bare = func.ltrim(bare, string.ascii_uppercase)
        conditions.append(bare.startswith(number, autoescape=True))
    return or_(*conditions)


def _vote_dict(v: Any) -> dict:
    return stored_vote(v.id, v.bill_id, v.voted_with_party)


def _stored_voting_record(entity: Any) -> dict:
    """The member's votes as score_calculator reads them: the stored key and
    recent votes and the whole-Congress party-line record."""
    return {
        "keyVotes": [_vote_dict(v) for v in entity.key_votes if v.vote_category == "key"],
        "recentVotes": [_vote_dict(v) for v in entity.key_votes if v.vote_category == "recent"],
        "partyLineRecord": load_record(entity.party_line_record),
    }


def party_line_counts(entity: Any) -> tuple[int, int, float]:
    """(votes with the party, breaks, with-party share 0-100) on party-line
    votes, as Constituent Alignment counts them (score_calculator.
    party_line_tally): the profile's voting record states the same figures
    as the column scored from them."""
    breaks, n = party_line_tally(_stored_voting_record(entity))
    return n - breaks, breaks, (round((n - breaks) / n * 100, 1) if n else 0.0)


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
        **_stored_voting_record(entity),
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
        "name": entity.name,
        "yearsInOffice": entity.years_in_office,
        # Representatives only (Senator has no column): see Representative.sworn_date.
        "swornDate": getattr(entity, "sworn_date", None),
        "ideologyScore": entity.ideology_score,
        "bioguideId": entity.bioguide_id,
        # How the stored score treated these bills (scored as read that day,
        # carried forward from an earlier run's, or not measured): the
        # breakdown recomputes from the same stored bills the same way.
        **bills_inputs_from_status(stored_bills_status(entity)),
    }



def score_breakdown(db: Session, entity: Any, *, lobbying_donation_attr: str) -> dict:
    """explain_scores for a loaded Senator or Representative, with the
    breaks its Constituent Alignment counts (and the flank breaks it
    doesn't) as the chamber recorded each roll call: the member's vote and
    how each party split. Served with the score so the list and the number
    can't disagree. Each dimension also carries its one-sentence finding
    (score_findings)."""
    entity_dict = build_score_breakdown_entity(entity, lobbying_donation_attr=lobbying_donation_attr)
    district = entity_dict["district"]
    # A House score is recomputed on the district lines it was stored on,
    # which differ from the sitting ones between a change of Congress and
    # the House run that rescores the member (or for good, for a member
    # who left then) — see district_pvi.lines_of. A senator's (None) reads
    # the sitting lines, which no state lean depends on.
    with lines_of(getattr(entity, "district_lines_congress", None) if district is not None else None):
        breakdown = explain_scores(entity_dict)
        # The seat's lean as the score read it (Cook's), and beside it the
        # one Civitas computes from precinct returns — shown, never scored.
        seat_lean = {
            "cookPvi": _seat_pvi(entity_dict["state"], district),
            "computedLean": (
                computed_lean_for(entity_dict["state"], district, lines_congress()) if district is not None else None
            ),
            "linesCongress": lines_congress() if district is not None else None,
        }
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
    if facts is not None:
        facts["seatLean"] = seat_lean
    return add_findings(breakdown, "senate" if district is None else "house")
