"""Service layer for House representative data — mirrors senator_service.py."""

import json

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import (
    PromiseAlignment,
    RepCampaignPromise,
    RepDonor,
    RepIndustryDonation,
    RepKeyVote,
    RepLobbyingMatch,
    RepSponsoredBill,
    RepStockTrade,
    Representative,
)
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.pipeline.analyze.sponsorship_analysis import (
    describe_senator_position,
    party_ideology_bounds,
)
from app.schemas import PaginatedRepresentativesSchema, RepresentativeSchema
from app.services.pagination import paginate_bounds
from app.services.score_trends import compute_score_trend_map
from app.services.senator_service import (
    STATE_NAMES,
    _USELESS_SPONSOR,
    _clean_pac_sponsor,
    _clean_platform_summary,
    _compute_initials,
    _fixup_donor_type,
)


def _rep_eager_options() -> list:
    return [
        selectinload(Representative.donors),
        selectinload(Representative.industry_donations),
        selectinload(Representative.key_votes),
        selectinload(Representative.lobbying_matches),
        selectinload(Representative.campaign_promises),
        selectinload(Representative.sponsored_bills),
    ]


def _build_areas(raw_str: str | None) -> list[dict]:
    try:
        raw = json.loads(raw_str) if raw_str else []
    except (json.JSONDecodeError, TypeError):
        raw = []
    return [
        {"area": a.get("area", ""), "confidence": a.get("confidence", 0.0), "party": a.get("party", "bipartisan")}
        for a in raw if isinstance(a, dict) and a.get("area")
    ]


def build_rep_response(rep: Representative, _db: Session = None) -> RepresentativeSchema:
    """Assemble a full RepresentativeSchema from Representative ORM model.

    Mirrors senator_service.build_senator_response — same sub-schemas,
    same dict->model construction pattern.
    """
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

    return RepresentativeSchema(
        id=rep.id,
        name=rep.name,
        state=rep.state,
        district=rep.district,
        party=rep.party,
        years_in_office=rep.years_in_office,
        initials=initials,
        leadership_title=rep.leadership_title,
        committees=json.loads(rep.committees or "[]"),
        representation_score={
            "fundingIndependence": rep.score_funding_independence,
            "promisePersistence": rep.score_promise_persistence,
            "independentVoting": rep.score_independent_voting,
            "fundingDiversity": rep.score_funding_diversity,
            "legislativeEffectiveness": rep.score_legislative_effectiveness,
            "overall": compute_overall_score(rep),
            "confidence": json.loads(rep.score_confidence or "{}"),
        },
        funding={
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
                    "pacIndustry": d.pac_industry if d.pac_industry and d.pac_industry.lower().strip() not in _USELESS_SPONSOR else None,
                    "pacAnalysis": d.pac_analysis,
                    "committeeType": d.committee_type,
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
        voting_record={
            "totalVotes": total_votes,
            "votedWithPartyCount": voted_with,
            "votedAgainstPartyCount": voted_against,
            "partyLoyaltyPct": party_loyalty_pct,
            "votingSummary": rep.voting_summary or "",
            "recentVoteCount": len(recent_votes_db),
            "keyVoteCount": len(key_votes_db),
        },
        lobbying_matches=[
            {
                "lobbyistOrg": lm.lobbyist_org,
                "industry": lm.industry,
                "lobbyingSpend": lm.lobbying_spend,
                "donationToSenator": lm.donation_to_representative,  # key shared with senator schema
                "billsInfluenced": json.loads(lm.bills_influenced) if lm.bills_influenced else [],
                "senatorVoteAligned": lm.representative_vote_aligned,  # key shared with senator schema
                "description": lm.description,
            }
            for lm in lobbying_matches
        ],
        campaign_promises=[
            {
                "promiseText": cp.promise_text,
                "category": cp.category,
                "alignment": cp.alignment,
                "relatedVotes": json.loads(cp.related_votes) if cp.related_votes else [],
                "relatedBills": json.loads(cp.related_bills) if cp.related_bills else [],
                "analysis": cp.analysis,
                "partyAlignment": cp.party_alignment,
            }
            for cp in campaign_promises
        ],
        platform_summary=_clean_platform_summary(rep.platform_summary),
        partisan_depth=json.loads(rep.partisan_depth) if rep.partisan_depth else None,
        sponsored_bills=[
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
        leadership_score=rep.leadership_score,
        bipartisanship_score=rep.bipartisanship_score,
        ideology_score=rep.ideology_score,
        sponsorship_description=rep.sponsorship_description or "",
        website_url=getattr(rep, "website_url", "") or "",
        contact_form_url=getattr(rep, "contact_form_url", "") or "",
        office_phone=getattr(rep, "office_phone", "") or "",
        office_address=getattr(rep, "office_address", "") or "",
    )


def get_representatives_by_state(
    db: Session,
    state: str,
    page: int = 1,
    per_page: int = 10,
) -> PaginatedRepresentativesSchema:
    base_q = (
        db.query(Representative)
        .options(*_rep_eager_options())
        .filter(Representative.state == state.upper())
        .order_by(Representative.district)
    )
    total = base_q.count()
    total_pages, page = paginate_bounds(total, page, per_page)

    reps = base_q.offset((page - 1) * per_page).limit(per_page).all()

    return PaginatedRepresentativesSchema(
        entries=[build_rep_response(r, db) for r in reps],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


def get_representative_by_id(db: Session, rep_id: str) -> RepresentativeSchema | None:
    rep = (
        db.query(Representative)
        .options(*_rep_eager_options())
        .filter(Representative.id == rep_id)
        .first()
    )
    if rep is None:
        return None
    return build_rep_response(rep, db)


def get_representative_score_breakdown(db: Session, rep_id: str) -> dict | None:
    """Recompute a representative's full score-derivation breakdown on-demand.

    Mirrors get_senator_score_breakdown in senator_service.py — see that
    function's docstring for why this reads directly from ORM relationships
    rather than build_rep_response()'s display-oriented dict (which only
    has vote counts, not per-vote votedWithParty/partyAlignmentWeight).
    """
    from app.pipeline.analyze.score_calculator import explain_scores

    rep = (
        db.query(Representative)
        .options(*_rep_eager_options())
        .filter(Representative.id == rep_id)
        .first()
    )
    if rep is None:
        return None

    def _vote_dict(v: RepKeyVote) -> dict:
        return {
            "votedWithParty": v.voted_with_party,
            "partyAlignmentWeight": v.party_alignment_weight,
            "partyLeaning": v.party_leaning,
            "opposingPartyUnityPct": v.opposing_party_unity_pct,
        }

    voting_record = {
        "effectiveParty": None,
        "keyVotes": [_vote_dict(v) for v in rep.key_votes if v.vote_category == "key"],
        "recentVotes": [_vote_dict(v) for v in rep.key_votes if v.vote_category == "recent"],
    }

    funding = {
        "totalRaised": rep.total_raised,
        "totalFromPACs": rep.total_from_pacs,
        "smallDonorPercentage": rep.small_donor_percentage,
        "outsideSpendingFor": rep.outside_spending_for,
        "topDonors": [
            {"name": d.name, "total": d.total, "type": d.type, "committeeType": d.committee_type}
            for d in rep.donors
        ],
        "industryBreakdown": [
            {"industry": ind.industry, "total": ind.total}
            for ind in rep.industry_donations
        ],
    }

    lobbying_matches = [
        {
            "donationToSenator": lm.donation_to_representative,
            "isConsensusVote": lm.is_consensus_vote,
        }
        for lm in rep.lobbying_matches
    ]

    sponsored_bills = [
        {
            "billType": sb.bill_type,
            "congress": sb.congress,
            "isLaw": sb.is_law,
            "latestAction": sb.latest_action,
        }
        for sb in rep.sponsored_bills
    ]

    entity = {
        "funding": funding,
        "votingRecord": voting_record,
        "lobbyingMatches": lobbying_matches,
        "sponsoredBills": sponsored_bills,
        "state": rep.state,
        "party": rep.party,
        "district": rep.district,
        "bipartisanshipScore": rep.bipartisanship_score,
        "leadershipScore": rep.leadership_score,
        "yearsInOffice": rep.years_in_office,
    }
    return explain_scores(entity)


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


def _compute_rep_trend_map(db: Session) -> dict[str, dict]:
    return compute_score_trend_map(db, "representative")


def get_rep_leaderboard(
    db: Session,
    page: int = 1,
    per_page: int = 50,
    party: str | None = None,
) -> dict:
    reps = db.query(Representative).all()

    # Party-relative ideology label thresholds over the FULL cohort — before
    # the party filter/pagination below — so the terciles are stable
    # regardless of which page or party is requested. See party_ideology_bounds.
    ideology_bounds_by_party = party_ideology_bounds(
        [(r.ideology_score, r.party) for r in reps]
    )

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

    reps.sort(key=compute_overall_score, reverse=True)

    if party:
        reps = [r for r in reps if r.party == party.upper()]

    total = len(reps)
    total_pages, page = paginate_bounds(total, page, per_page)
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
                "overall": compute_overall_score(r),
            },
            "totalRaised": r.total_raised,
            "totalFromPacs": r.total_from_pacs,
            "smallDonorPercentage": r.small_donor_percentage,
            "topIndustry": top_industry_map.get(r.id),
            "trend": trend_map.get(r.id, {"direction": "new", "change": 0.0, "previousScore": None}),
            "ideologyScore": r.ideology_score,
            "ideologyLabel": (
                describe_senator_position(
                    r.ideology_score, r.leadership_score, r.party,
                    years_in_office=r.years_in_office,
                    ideology_bounds=ideology_bounds_by_party.get(r.party),
                )
                if r.ideology_score is not None and r.leadership_score is not None
                else None
            ),
            "leadershipScore": r.leadership_score,
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
    existing.leadership_title = rep_data.get("leadershipTitle", existing.leadership_title)
    if "committees" in rep_data:
        existing.committees = json.dumps(rep_data["committees"])

    # Absent score => unknown, not "fully captured": default to neutral 50,
    # matching the scoring standard (score_calculator: "Missing data yields a
    # neutral 50, never a perfect 100 or 0").
    existing.score_funding_independence = cs.get("fundingIndependence", 50)
    existing.score_promise_persistence = cs.get("promisePersistence", 50)
    existing.score_independent_voting = cs.get("independentVoting", 50)
    existing.score_funding_diversity = cs.get("fundingDiversity", 50)
    existing.score_legislative_effectiveness = cs.get("legislativeEffectiveness", 50)
    existing.score_confidence = json.dumps(cs.get("confidence") or {})

    existing.total_raised = funding.get("totalRaised", 0)
    existing.total_from_pacs = funding.get("totalFromPACs", 0)
    existing.small_donor_percentage = funding.get("smallDonorPercentage", 0)
    existing.outside_spending_for = funding.get("outsideSpendingFor")
    voting_record = rep_data.get("votingRecord", {})
    existing.voting_summary = voting_record.get("votingSummary", "")
    existing.platform_summary = rep_data.get("platformSummary", "")
    existing.website_url = rep_data.get("officialWebsiteUrl") or ""
    existing.contact_form_url = rep_data.get("contactFormUrl") or ""
    existing.office_phone = rep_data.get("officePhone") or ""
    existing.office_address = rep_data.get("officeAddress") or ""

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
            committee_type=d.get("committeeType"),
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
            opposing_party_unity_pct=v.get("opposingPartyUnityPct"),
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
            is_consensus_vote=lm.get("isConsensusVote"),
            description=lm.get("description") or "",
        ))

    db.query(RepCampaignPromise).filter(RepCampaignPromise.representative_id == rid).delete()
    for cp in rep_data.get("campaignPromises", []):
        db.add(RepCampaignPromise(
            representative_id=rid,
            promise_text=cp.get("promiseText") or "",
            category=cp.get("category") or "other",
            alignment=cp.get("alignment") or PromiseAlignment.UNCLEAR,
            related_votes=json.dumps(cp.get("relatedVotes") or []),
            related_bills=json.dumps(cp.get("relatedBills") or []),
            analysis=cp.get("analysis") or "",
            party_alignment=cp.get("partyAlignment"),
        ))

    db.query(RepSponsoredBill).filter(RepSponsoredBill.representative_id == rid).delete()
    for sp_data in rep_data.get("sponsoredBills", []):
        db.add(RepSponsoredBill(
            representative_id=rid,
            bill_id=sp_data.get("billId") or "",
            title=sp_data.get("title") or "",
            introduced_date=sp_data.get("introducedDate") or "",
            latest_action=sp_data.get("latestAction") or "",
            latest_action_date=sp_data.get("latestActionDate") or "",
            policy_area=sp_data.get("policyArea") or "",
            policy_areas=json.dumps(sp_data.get("policyAreas") or []),
            party_leaning=sp_data.get("partyLeaning"),
            congress=sp_data.get("congress") or 0,
            bill_type=sp_data.get("billType") or "",
            is_law=sp_data.get("isLaw") or False,
            stage=sp_data.get("stage") or "",
        ))

    partisan_depth_data = rep_data.get("partisanDepth")
    if partisan_depth_data:
        existing.partisan_depth = json.dumps(partisan_depth_data)

    ls = rep_data.get("leadershipScore")
    existing.leadership_score = ls if ls is not None else existing.leadership_score
    bs = rep_data.get("bipartisanshipScore")
    existing.bipartisanship_score = bs if bs is not None else existing.bipartisanship_score
    ids = rep_data.get("ideologyScore")
    existing.ideology_score = ids if ids is not None else existing.ideology_score
    existing.sponsorship_description = rep_data.get("sponsorshipDescription") or existing.sponsorship_description

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
    total_pages, page = paginate_bounds(total, page, per_page)

    votes_db = query.order_by(RepKeyVote.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

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


def get_rep_stock_trades(
    db: Session,
    rep_id: str,
    page: int = 1,
    per_page: int = 15,
) -> dict | None:
    """Return paginated STOCK Act trade disclosures for a representative.

    Informational only — see senator_service.get_senator_stock_trades for
    the scoring rationale (issue #45).
    """
    rep = db.query(Representative).filter(Representative.id == rep_id).first()
    if rep is None:
        return None

    query = db.query(RepStockTrade).filter(RepStockTrade.representative_id == rep_id)
    total = query.count()
    late_count = query.filter(RepStockTrade.days_to_disclose > 45).count()
    total_pages, page = paginate_bounds(total, page, per_page)

    trades_db = (
        query.order_by(RepStockTrade.transaction_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "trades": [
            {
                "ticker": t.ticker,
                "assetName": t.asset_name,
                "owner": t.owner,
                "transactionType": t.transaction_type,
                "transactionDate": t.transaction_date,
                "disclosureDate": t.disclosure_date,
                "daysToDisclose": t.days_to_disclose,
                "late": t.days_to_disclose > 45,
                "amountLow": t.amount_low,
                "amountHigh": t.amount_high,
                "industry": t.industry,
                "sourceUrl": t.source_url,
                "parseConfidence": t.parse_confidence,
            }
            for t in trades_db
        ],
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
        "lateCount": late_count,
    }
