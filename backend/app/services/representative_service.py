"""Service layer for House representative data — mirrors senator_service.py."""

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.config_definitions import SCORE_WEIGHTS
from app.models import (
    RepCampaignPromise,
    RepDonor,
    RepIndustryDonation,
    RepKeyVote,
    RepLobbyingMatch,
    RepSponsoredBill,
    Representative,
    ScoreSnapshot,
)
from app.schemas import (
    CampaignPromiseSchema,
    DonorSchema,
    FundingSchema,
    IndustryDonationSchema,
    KeyVoteSchema,
    LeaderboardEntrySchema,
    LobbyingMatchSchema,
    PaginatedVotesSchema,
    PartisanDepthSchema,
    PolicyAlignmentSchema,
    PolicyAreaDetail,
    RepresentationScoreSchema,
    ScoreTrendSchema,
    SponsoredBillSchema,
    VoteCountsSchema,
    VotingRecordSchema,
)
from app.services.senator_service import (
    STATE_NAMES,
    _build_sponsored_bills,
    _clean_pac_sponsor,
    _clean_platform_summary,
    _compute_initials,
    _filter_promises,
    _fixup_donor_type,
    _parse_partisan_depth,
)


def _rep_eager_options():
    return [
        selectinload(Representative.donors),
        selectinload(Representative.industry_donations),
        selectinload(Representative.key_votes),
        selectinload(Representative.lobbying_matches),
        selectinload(Representative.campaign_promises),
        selectinload(Representative.sponsored_bills),
    ]


class _RepSponsoredBillAdapter:
    """Adapter to make RepSponsoredBill look like SponsoredBill for reuse."""
    def __init__(self, sb):
        self._sb = sb
    def __getattr__(self, name):
        return getattr(self._sb, name)


def build_rep_response(rep: Representative, db: Session):
    """Assemble a full response dict from Representative ORM model."""
    donors = sorted(rep.donors, key=lambda d: d.rank)
    industry_donations = sorted(rep.industry_donations, key=lambda d: d.total, reverse=True)
    key_votes = rep.key_votes
    lobbying_matches = rep.lobbying_matches
    campaign_promises = rep.campaign_promises

    recent_votes_db = [v for v in key_votes if v.vote_category == "recent"]
    key_votes_db = [v for v in key_votes if v.vote_category == "key"]

    all_votes = key_votes
    total_votes = len(all_votes)
    voted_with = sum(1 for v in all_votes if v.voted_with_party is True)
    voted_against = sum(1 for v in all_votes if v.voted_with_party is False)
    party_total = voted_with + voted_against
    party_loyalty_pct = round(voted_with / party_total * 100, 1) if party_total > 0 else 0.0

    initials = _compute_initials(rep.name) or rep.initials

    def _build_areas(raw_str):
        try:
            raw = json.loads(raw_str) if raw_str else []
        except (json.JSONDecodeError, TypeError):
            raw = []
        return [
            {"area": a.get("area", ""), "confidence": a.get("confidence", 0.0), "party": a.get("party", "bipartisan")}
            for a in raw if isinstance(a, dict) and a.get("area")
        ]

    return {
        "id": rep.id,
        "name": rep.name,
        "state": rep.state,
        "district": rep.district,
        "party": rep.party,
        "yearsInOffice": rep.years_in_office,
        "initials": initials,
        "representationScore": {
            "fundingIndependence": rep.score_funding_independence,
            "promisePersistence": rep.score_promise_persistence,
            "independentVoting": rep.score_independent_voting,
            "fundingDiversity": rep.score_funding_diversity,
            "legislativeEffectiveness": rep.score_legislative_effectiveness,
        },
        "funding": {
            "totalRaised": rep.total_raised,
            "totalFromPACs": rep.total_from_pacs,
            "smallDonorPercentage": rep.small_donor_percentage,
            "topDonors": [
                {
                    "name": d.name,
                    "total": d.total,
                    "type": _fixup_donor_type(d.name, d.type, rep.name),
                    "industry": d.industry or "OTHER",
                    "pacSponsor": _clean_pac_sponsor(d.pac_sponsor, d.name),
                    "pacIndustry": d.pac_industry if d.pac_industry and d.pac_industry.lower().strip() not in {"", "n/a", "none", "unclear", "unknown"} else None,
                    "pacAnalysis": d.pac_analysis,
                }
                for d in donors
            ],
            "industryBreakdown": [
                {
                    "industry": ind.industry,
                    "name": ind.name,
                    "total": ind.total,
                    "percentage": ind.percentage,
                }
                for ind in industry_donations
            ],
        },
        "votingRecord": {
            "totalVotes": total_votes,
            "votedWithPartyCount": voted_with,
            "votedAgainstPartyCount": voted_against,
            "partyLoyaltyPct": party_loyalty_pct,
            "votingSummary": rep.voting_summary or "",
            "recentVoteCount": len(recent_votes_db),
            "keyVoteCount": len(key_votes_db),
        },
        "lobbyingMatches": [
            {
                "lobbyistOrg": lm.lobbyist_org,
                "industry": lm.industry,
                "lobbyingSpend": lm.lobbying_spend,
                "donationToSenator": lm.donation_to_representative,
                "billsInfluenced": json.loads(lm.bills_influenced) if lm.bills_influenced else [],
                "senatorVoteAligned": lm.representative_vote_aligned,
                "description": lm.description,
            }
            for lm in lobbying_matches
        ],
        "campaignPromises": [
            {
                "promiseText": cp.promise_text,
                "category": cp.category,
                "alignment": cp.alignment,
                "relatedVotes": json.loads(cp.related_votes) if cp.related_votes else [],
                "analysis": cp.analysis,
                "partyAlignment": cp.party_alignment,
            }
            for cp in campaign_promises
        ],
        "platformSummary": _clean_platform_summary(rep.platform_summary),
        "partisanDepth": json.loads(rep.partisan_depth) if rep.partisan_depth else None,
        "sponsoredBills": [
            {
                "billId": sb.bill_id,
                "title": sb.title,
                "introducedDate": sb.introduced_date or "",
                "latestAction": sb.latest_action or "",
                "latestActionDate": sb.latest_action_date or "",
                "policyArea": sb.policy_area or "",
                "policyAreas": _build_areas(sb.policy_areas),
                "partyLeaning": sb.party_leaning,
                "congress": sb.congress or 0,
                "billType": sb.bill_type or "",
                "isLaw": sb.is_law or False,
            }
            for sb in sorted(rep.sponsored_bills, key=lambda x: x.introduced_date or "", reverse=True)
        ],
        "leadershipScore": rep.leadership_score,
        "ideologyScore": rep.ideology_score,
        "sponsorshipDescription": rep.sponsorship_description or "",
    }


def get_representatives_by_state(
    db: Session,
    state: str,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    base_q = (
        db.query(Representative)
        .options(*_rep_eager_options())
        .filter(Representative.state == state.upper())
        .order_by(Representative.district)
    )
    total = base_q.count()
    total_pages = max(1, -(-total // per_page))
    page = max(1, min(page, total_pages))

    reps = base_q.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "entries": [build_rep_response(r, db) for r in reps],
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
    }


def get_representative_by_id(db: Session, rep_id: str) -> dict | None:
    rep = (
        db.query(Representative)
        .options(*_rep_eager_options())
        .filter(Representative.id == rep_id)
        .first()
    )
    if rep is None:
        return None
    return build_rep_response(rep, db)


def get_rep_states_with_counts(db: Session) -> list[dict]:
    rows = (
        db.query(Representative.state, func.count(Representative.id).label("cnt"))
        .group_by(Representative.state)
        .order_by(Representative.state)
        .all()
    )
    return [
        {"code": row.state, "name": STATE_NAMES.get(row.state, row.state), "repCount": row.cnt}
        for row in rows
    ]


_FIELD_TO_WEIGHT_KEY = {
    "score_funding_independence": "fundingIndependence",
    "score_promise_persistence":  "promisePersistence",
    "score_independent_voting":   "independentVoting",
    "score_funding_diversity":    "fundingDiversity",
    "score_legislative_effectiveness": "legislativeEffectiveness",
}

_TREND_LOOKBACK_DAYS = 7
_TREND_THRESHOLD = 0.5


def _compute_rep_trend_map(db: Session) -> dict[str, dict]:
    today = datetime.utcnow().date()
    target_date = today - timedelta(days=_TREND_LOOKBACK_DAYS)

    latest_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == "representative",
            ScoreSnapshot.date == today.isoformat(),
        )
        .all()
    )
    if not latest_snapshots:
        return {}

    yesterday = (today - timedelta(days=1)).isoformat()
    older_snapshots = (
        db.query(ScoreSnapshot)
        .filter(
            ScoreSnapshot.entity_type == "representative",
            ScoreSnapshot.date <= min(target_date.isoformat(), yesterday),
        )
        .order_by(ScoreSnapshot.date.desc())
        .all()
    )
    older_map: dict[str, float] = {}
    for snap in older_snapshots:
        if snap.entity_id not in older_map:
            older_map[snap.entity_id] = snap.overall_score

    result: dict[str, dict] = {}
    for snap in latest_snapshots:
        prev = older_map.get(snap.entity_id)
        if prev is None:
            result[snap.entity_id] = {"direction": "new", "change": 0.0, "previousScore": None}
        else:
            change = round(snap.overall_score - prev, 2)
            if change > _TREND_THRESHOLD:
                direction = "up"
            elif change < -_TREND_THRESHOLD:
                direction = "down"
            else:
                direction = "stable"
            result[snap.entity_id] = {"direction": direction, "change": change, "previousScore": prev}
    return result


def get_rep_leaderboard(
    db: Session,
    page: int = 1,
    per_page: int = 50,
    party: str | None = None,
) -> dict:
    reps = db.query(Representative).all()

    top_industry_map: dict[str, str] = {}
    ind_rows = (
        db.query(RepIndustryDonation.representative_id, RepIndustryDonation.name)
        .order_by(RepIndustryDonation.representative_id, RepIndustryDonation.total.desc())
        .all()
    )
    for rep_id, name in ind_rows:
        if rep_id not in top_industry_map:
            top_industry_map[rep_id] = name

    trend_map = _compute_rep_trend_map(db)

    def _weighted_score(r: Representative) -> float:
        return sum(
            getattr(r, db_field, 0) * SCORE_WEIGHTS[weight_key]
            for db_field, weight_key in _FIELD_TO_WEIGHT_KEY.items()
        )

    reps.sort(key=_weighted_score, reverse=True)

    if party:
        reps = [r for r in reps if r.party == party.upper()]

    total = len(reps)
    total_pages = max(1, -(-total // per_page))
    page = max(1, min(page, total_pages))
    page_reps = reps[(page - 1) * per_page : page * per_page]

    entries = [
        {
            "id": r.id,
            "name": r.name,
            "state": r.state,
            "district": r.district,
            "party": r.party,
            "yearsInOffice": r.years_in_office,
            "initials": _compute_initials(r.name) or r.initials,
            "representationScore": {
                "fundingIndependence": r.score_funding_independence,
                "promisePersistence": r.score_promise_persistence,
                "independentVoting": r.score_independent_voting,
                "fundingDiversity": r.score_funding_diversity,
                "legislativeEffectiveness": r.score_legislative_effectiveness,
            },
            "totalRaised": r.total_raised,
            "totalFromPACs": r.total_from_pacs,
            "smallDonorPercentage": r.small_donor_percentage,
            "topIndustry": top_industry_map.get(r.id),
            "trend": trend_map.get(r.id, {"direction": "new", "change": 0.0, "previousScore": None}),
        }
        for r in page_reps
    ]

    return {
        "entries": entries,
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
    }


def upsert_representative(db: Session, rep_data: dict) -> Representative:
    """Insert or update a representative and all related records."""
    rid = rep_data["id"]

    existing = db.query(Representative).filter(Representative.id == rid).first()
    if existing is None:
        existing = Representative(id=rid)
        db.add(existing)

    cs = rep_data.get("representationScore", {})
    funding = rep_data.get("funding", {})

    existing.bioguide_id = rep_data.get("bioguideId", existing.bioguide_id)
    existing.name = rep_data.get("name", existing.name)
    existing.state = rep_data.get("state", existing.state)
    existing.district = rep_data.get("district", existing.district or 0)
    existing.party = rep_data.get("party", existing.party)
    existing.years_in_office = rep_data.get("yearsInOffice", existing.years_in_office)
    existing.initials = rep_data.get("initials", existing.initials)

    existing.score_funding_independence = cs.get("fundingIndependence", 0)
    existing.score_promise_persistence = cs.get("promisePersistence", 0)
    existing.score_independent_voting = cs.get("independentVoting", 0)
    existing.score_funding_diversity = cs.get("fundingDiversity", 0)
    existing.score_legislative_effectiveness = cs.get("legislativeEffectiveness", 0)

    existing.total_raised = funding.get("totalRaised", 0)
    existing.total_from_pacs = funding.get("totalFromPACs", 0)
    existing.small_donor_percentage = funding.get("smallDonorPercentage", 0)
    voting_record = rep_data.get("votingRecord", {})
    existing.voting_summary = voting_record.get("votingSummary", "")
    existing.platform_summary = rep_data.get("platformSummary", "")

    db.flush()

    db.query(RepDonor).filter(RepDonor.representative_id == rid).delete()
    for rank, d in enumerate(funding.get("topDonors", []), start=1):
        db.add(RepDonor(
            representative_id=rid,
            name=d["name"],
            total=d["total"],
            type=d["type"],
            industry=d.get("industry", "OTHER"),
            rank=rank,
            pac_sponsor=d.get("pacSponsor"),
            pac_industry=d.get("pacIndustry"),
            pac_analysis=d.get("pacAnalysis"),
        ))

    db.query(RepIndustryDonation).filter(RepIndustryDonation.representative_id == rid).delete()
    for ind in funding.get("industryBreakdown", []):
        db.add(RepIndustryDonation(
            representative_id=rid,
            industry=ind["industry"],
            name=ind["name"],
            total=ind["total"],
            percentage=ind["percentage"],
        ))

    db.query(RepKeyVote).filter(RepKeyVote.representative_id == rid).delete()
    all_votes = (
        [(v, "recent") for v in voting_record.get("recentVotes", [])]
        + [(v, "key") for v in voting_record.get("keyVotes", [])]
    )
    if not all_votes and voting_record.get("keyVotes"):
        all_votes = [(v, "key") for v in voting_record["keyVotes"]]
    for v, category in all_votes:
        db.add(RepKeyVote(
            representative_id=rid,
            bill_name=v.get("billName", "Unknown Bill"),
            bill_id=v.get("billId", ""),
            date=v.get("date", ""),
            vote=v.get("vote", "Not Voting"),
            policy_area=v.get("policyArea", "PROCEDURAL"),
            stance=v.get("stance", "neutral"),
            description=v.get("description", ""),
            party_leaning=v.get("partyLeaning"),
            voted_with_party=v.get("votedWithParty"),
            vote_category=category,
            key_vote_reasoning=v.get("keyVoteReasoning"),
        ))

    db.query(RepLobbyingMatch).filter(RepLobbyingMatch.representative_id == rid).delete()
    for lm in rep_data.get("lobbyingMatches", []):
        db.add(RepLobbyingMatch(
            representative_id=rid,
            lobbyist_org=lm.get("lobbyistOrg") or "Unknown",
            industry=lm.get("industry") or "OTHER",
            lobbying_spend=lm.get("lobbyingSpend") or 0,
            donation_to_representative=lm.get("donationToSenator") or lm.get("donationToRepresentative") or 0,
            bills_influenced=json.dumps(lm.get("billsInfluenced") or []),
            representative_vote_aligned=lm.get("senatorVoteAligned") or lm.get("representativeVoteAligned"),
            description=lm.get("description") or "",
        ))

    db.query(RepCampaignPromise).filter(RepCampaignPromise.representative_id == rid).delete()
    for cp in rep_data.get("campaignPromises", []):
        db.add(RepCampaignPromise(
            representative_id=rid,
            promise_text=cp.get("promiseText") or "",
            category=cp.get("category") or "other",
            alignment=cp.get("alignment") or "unclear",
            related_votes=json.dumps(cp.get("relatedVotes") or []),
            analysis=cp.get("analysis") or "",
            party_alignment=cp.get("partyAlignment"),
        ))

    partisan_depth_data = rep_data.get("partisanDepth")
    if partisan_depth_data:
        existing.partisan_depth = json.dumps(partisan_depth_data)

    db.commit()
    db.refresh(existing)
    return existing


def get_rep_votes(
    db: Session,
    rep_id: str,
    category: str = "recent",
    page: int = 1,
    per_page: int = 15,
    vote_filter: str = "all",
) -> dict | None:
    rep = db.query(Representative).filter(Representative.id == rep_id).first()
    if rep is None:
        return None

    base_q = db.query(RepKeyVote).filter(
        RepKeyVote.representative_id == rep_id,
        RepKeyVote.vote_category == category,
    )

    count_all = base_q.count()
    count_yea = base_q.filter(RepKeyVote.vote == "Yea").count()
    count_nay = base_q.filter(RepKeyVote.vote == "Nay").count()
    count_against = base_q.filter(RepKeyVote.voted_with_party == False).count()  # noqa: E712

    query = base_q
    if vote_filter == "yea":
        query = query.filter(RepKeyVote.vote == "Yea")
    elif vote_filter == "nay":
        query = query.filter(RepKeyVote.vote == "Nay")
    elif vote_filter == "against-party":
        query = query.filter(RepKeyVote.voted_with_party == False)  # noqa: E712

    total = query.count()
    total_pages = max(1, -(-total // per_page))
    page = max(1, min(page, total_pages))

    votes_db = query.order_by(RepKeyVote.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

    def _build_areas(raw_str):
        try:
            raw = json.loads(raw_str) if raw_str else []
        except (json.JSONDecodeError, TypeError):
            raw = []
        return [
            {"area": a.get("area", ""), "confidence": a.get("confidence", 0.0), "party": a.get("party", "bipartisan")}
            for a in raw if isinstance(a, dict)
        ]

    return {
        "votes": [
            {
                "billName": v.bill_name,
                "billId": v.bill_id,
                "date": v.date,
                "vote": v.vote,
                "policyArea": v.policy_area or "PROCEDURAL",
                "policyAreas": _build_areas(v.policy_areas),
                "partyAlignmentWeight": getattr(v, "party_alignment_weight", 0.0) or 0.0,
                "stance": v.stance or "neutral",
                "description": v.description or "",
                "partyLeaning": v.party_leaning,
                "votedWithParty": v.voted_with_party,
                "voteCategory": v.vote_category or "key",
                "keyVoteReasoning": v.key_vote_reasoning,
            }
            for v in votes_db
        ],
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
        "category": category,
        "filter": vote_filter,
        "counts": {"all": count_all, "yea": count_yea, "nay": count_nay, "againstParty": count_against},
    }
