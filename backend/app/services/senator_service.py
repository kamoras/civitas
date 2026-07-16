import json
import re

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import CampaignPromise, Donor, IndustryDonation, KeyVote, LobbyingMatch, PromiseAlignment, Senator, StockTrade

# Promise quality rules are shared with the pipeline (which now cleans
# promises before scoring/persisting); the read path keeps applying them
# only as a safety net for rows persisted before 2026-07.
from app.pipeline.analyze.promise_quality import (
    _ERROR_PAGE_RE,
    _FILLER_RE,
    clean_promises,
)
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.services.pagination import paginate_bounds
from app.services.score_trends import compute_score_trend_map
from app.schemas import (
    CampaignPromiseSchema,
    PaginatedVotesSchema,
    PartisanDepthSchema,
    PolicyAlignmentSchema,
    PolicyAreaDetail,
    RepresentationScoreSchema,
    DonorSchema,
    FundingSchema,
    IndustryDonationSchema,
    KeyVoteSchema,
    LeaderboardEntrySchema,
    ScoreTrendSchema,
    LobbyingMatchSchema,
    PaginatedStockTradesSchema,
    SenatorSchema,
    SponsoredBillSchema,
    StateCountSchema,
    StockTradeSchema,
    VoteCountsSchema,
    VotingRecordSchema,
)

# US state code -> name mapping
STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}




_USELESS_SPONSOR = {"unclear", "unknown", "n/a", "none", ""}


def _clean_pac_sponsor(sponsor: str | None, donor_name: str) -> str | None:
    """Return None for useless or self-referential PAC sponsors."""
    if not sponsor or sponsor.lower().strip() in _USELESS_SPONSOR:
        return None
    if len(sponsor.strip()) <= 2:
        return None
    if sponsor.lower().strip() == donor_name.lower().strip():
        return None
    return sponsor


def _compute_initials(name: str) -> str:
    """Compute initials from 'First [Middle] Last' display name."""
    parts = name.split()
    if len(parts) >= 2:
        return parts[0][0].upper() + parts[-1][0].upper()
    if parts:
        return parts[0][0].upper()
    return ""


_NAV_ARTIFACT_RE = re.compile(
    r"skip to (?:main )?content|menu\s+menu\s+menu|"
    r"toggle navigation|hamburger|breadcrumb",
    re.IGNORECASE,
)


def _clean_platform_summary(text: str | None) -> str:
    """Strip error pages and navigation artifacts from platform text."""
    if not text:
        return ""
    if _ERROR_PAGE_RE.search(text):
        return ""
    text = _NAV_ARTIFACT_RE.sub("", text).strip()
    if text.startswith(". ") or text.startswith(", "):
        text = text[2:].strip()
    return text


def _fixup_donor_type(donor_name: str, donor_type: str, senator_name: str) -> str:
    """Read-time safety net for donor types.

    Lightweight string-only checks that avoid loading the embedding model.
    Full semantic classification happens at pipeline write time; this only
    catches obvious cases where a company name was persisted as "PAC".
    """
    if donor_type != "PAC":
        return donor_type

    name_upper = donor_name.upper()
    if any(sig in name_upper for sig in ("PAC", "COMMITTEE", "FUND", "LEAGUE", "CAUCUS")):
        return donor_type

    if senator_name:
        last = senator_name.split()[-1].upper()
        if last in name_upper:
            return "CandidateAffiliated"

    return "Org/Employees"


def _filter_promises(campaign_promises: list) -> list[CampaignPromiseSchema]:
    """Filter and correct campaign promise quality issues in persisted data.

    Delegates to the shared pipeline rules (promise_quality.clean_promises)
    so displayed alignments always match what the scoring path would
    produce. For rows written by pipelines after 2026-07 this is a no-op;
    it corrects legacy rows persisted before cleaning moved upstream.
    """
    as_dicts = [
        {
            "promiseText": cp.promise_text or "",
            "category": cp.category,
            "alignment": cp.alignment or PromiseAlignment.UNCLEAR,
            "relatedVotes": json.loads(cp.related_votes) if cp.related_votes else [],
            "relatedBills": json.loads(cp.related_bills) if cp.related_bills else [],
            "analysis": cp.analysis or "",
            "partyAlignment": cp.party_alignment,
        }
        for cp in campaign_promises
    ]

    return [
        CampaignPromiseSchema(
            promise_text=p["promiseText"],
            category=p["category"],
            alignment=p["alignment"],
            related_votes=p["relatedVotes"],
            related_bills=p["relatedBills"],
            analysis=p["analysis"],
            party_alignment=p["partyAlignment"],
        )
        for p in clean_promises(as_dicts)
    ]


def _build_sponsored_bills(sponsored_bills: list) -> list[SponsoredBillSchema]:
    """Convert ORM SponsoredBill records to schema objects, sorted by date."""
    result: list[SponsoredBillSchema] = []
    for sb in sponsored_bills:
        raw_areas = []
        if getattr(sb, "policy_areas", None):
            try:
                raw_areas = json.loads(sb.policy_areas)
            except (json.JSONDecodeError, TypeError):
                raw_areas = []
        areas = [
            PolicyAreaDetail(
                area=a.get("area", ""),
                confidence=a.get("confidence", 0.0),
                party=a.get("party", "bipartisan"),
            )
            for a in raw_areas
            if a.get("area")
        ]
        result.append(SponsoredBillSchema(
            bill_id=sb.bill_id,
            title=sb.title,
            introduced_date=sb.introduced_date or "",
            latest_action=sb.latest_action or "",
            latest_action_date=sb.latest_action_date or "",
            policy_area=sb.policy_area or "",
            policy_areas=areas,
            party_leaning=sb.party_leaning,
            congress=sb.congress or 0,
            bill_type=sb.bill_type or "",
            is_law=sb.is_law or False,
        ))
    result.sort(key=lambda x: x.introduced_date, reverse=True)
    return result


def _parse_partisan_depth(raw: str | None) -> PartisanDepthSchema | None:
    """Deserialize JSON partisan depth profile stored on the senator row."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return PartisanDepthSchema(
            overall_lean=data.get("overallLean", 0.0),
            overall_party=data.get("overallParty", "centrist"),
            depth=data.get("depth", "centrist"),
            cross_party_count=data.get("crossPartyCount", 0),
            total_positions=data.get("totalPositions", 0),
            policy_breakdown=[
                PolicyAlignmentSchema(
                    area=p["area"],
                    alignment=p["alignment"],
                    strength=p["strength"],
                )
                for p in data.get("policyBreakdown", [])
            ],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _senator_eager_options() -> list:
    """SQLAlchemy options to eager-load all senator relationships in one round-trip."""
    return [
        selectinload(Senator.donors),
        selectinload(Senator.industry_donations),
        selectinload(Senator.key_votes),
        selectinload(Senator.lobbying_matches),
        selectinload(Senator.campaign_promises),
        selectinload(Senator.sponsored_bills),
    ]


def build_senator_response(senator: Senator, db: Session) -> SenatorSchema:
    """Assemble a full SenatorSchema from the ORM model and related records.

    Expects senator to be loaded with eager-loaded relationships via
    ``_senator_eager_options()``.
    """
    donors = sorted(senator.donors, key=lambda d: d.rank)
    industry_donations = sorted(senator.industry_donations, key=lambda d: d.total, reverse=True)
    key_votes = senator.key_votes
    lobbying_matches = senator.lobbying_matches
    campaign_promises = senator.campaign_promises

    # 5. Split votes by category
    recent_votes_db = [v for v in key_votes if v.vote_category == "recent"]
    key_votes_db = [v for v in key_votes if v.vote_category == "key"]

    all_votes = key_votes
    total_votes = len(all_votes)
    voted_with = sum(1 for v in all_votes if v.voted_with_party is True)
    voted_against = sum(1 for v in all_votes if v.voted_with_party is False)

    party_total = voted_with + voted_against
    party_loyalty_pct = round(voted_with / party_total * 100, 1) if party_total > 0 else 0.0

    # 6. Assemble the nested schema
    initials = _compute_initials(senator.name) or senator.initials
    return SenatorSchema(
        id=senator.id,
        name=senator.name,
        state=senator.state,
        party=senator.party,
        years_in_office=senator.years_in_office,
        initials=initials,
        leadership_title=senator.leadership_title,
        committees=json.loads(senator.committees or "[]"),
        representation_score=RepresentationScoreSchema(
            funding_independence=senator.score_funding_independence,
            promise_persistence=senator.score_promise_persistence,
            independent_voting=senator.score_independent_voting,
            funding_diversity=senator.score_funding_diversity,
            legislative_effectiveness=senator.score_legislative_effectiveness,
            confidence=json.loads(senator.score_confidence or "{}") or None,
        ),
        funding=FundingSchema(
            total_raised=senator.total_raised,
            total_from_pacs=senator.total_from_pacs,
            small_donor_percentage=senator.small_donor_percentage,
            top_donors=[
                DonorSchema(
                    name=d.name,
                    total=d.total,
                    type=_fixup_donor_type(d.name, d.type, senator.name),
                    industry=d.industry or "OTHER",
                    pac_sponsor=_clean_pac_sponsor(d.pac_sponsor, d.name),
                    pac_industry=(
                        None if d.pac_industry and d.pac_industry.lower().strip() in _USELESS_SPONSOR
                        else d.pac_industry
                    ),
                    pac_analysis="" if (d.pac_analysis and _FILLER_RE.search(d.pac_analysis)) else d.pac_analysis,
                )
                for d in donors
            ],
            industry_breakdown=[
                IndustryDonationSchema(
                    industry=ind.industry,
                    name=ind.name,
                    total=ind.total,
                    percentage=ind.percentage,
                )
                for ind in industry_donations
            ],
        ),
        voting_record=VotingRecordSchema(
            total_votes=total_votes,
            voted_with_party_count=voted_with,
            voted_against_party_count=voted_against,
            party_loyalty_pct=party_loyalty_pct,
            voting_summary=senator.voting_summary or "",
            recent_vote_count=len(recent_votes_db),
            key_vote_count=len(key_votes_db),
        ),
        lobbying_matches=[
            LobbyingMatchSchema(
                lobbyist_org=lm.lobbyist_org,
                industry=lm.industry,
                lobbying_spend=lm.lobbying_spend,
                donation_to_senator=lm.donation_to_senator,
                bills_influenced=json.loads(lm.bills_influenced) if lm.bills_influenced else [],
                senator_vote_aligned=lm.senator_vote_aligned,
                description=lm.description,
            )
            for lm in lobbying_matches
        ],
        campaign_promises=_filter_promises(campaign_promises),
        platform_summary=_clean_platform_summary(senator.platform_summary),
        partisan_depth=_parse_partisan_depth(senator.partisan_depth),
        sponsored_bills=_build_sponsored_bills(senator.sponsored_bills),
        leadership_score=senator.leadership_score,
        bipartisanship_score=senator.bipartisanship_score,
        ideology_score=senator.ideology_score,
        sponsorship_description=getattr(senator, "sponsorship_description", "") or "",
        website_url=getattr(senator, "website_url", "") or "",
        contact_form_url=getattr(senator, "contact_form_url", "") or "",
        office_phone=getattr(senator, "office_phone", "") or "",
        office_address=getattr(senator, "office_address", "") or "",
    )


def get_senators_by_state(db: Session, state: str) -> list[SenatorSchema]:
    """Return all senators for a given state code."""
    senators = (
        db.query(Senator)
        .options(*_senator_eager_options())
        .filter(Senator.state == state.upper())
        .all()
    )
    return [build_senator_response(s, db) for s in senators]


def get_senator_by_id(db: Session, senator_id: str) -> SenatorSchema | None:
    """Return a single senator by ID, or None if not found."""
    senator = (
        db.query(Senator)
        .options(*_senator_eager_options())
        .filter(Senator.id == senator_id)
        .first()
    )
    if senator is None:
        return None
    return build_senator_response(senator, db)


def get_senator_score_breakdown(db: Session, senator_id: str) -> dict | None:
    """Recompute a senator's full score-derivation breakdown on-demand.

    Builds the same dict shape score_calculator.calculate_scores() consumes
    during the pipeline (funding, votingRecord with full per-vote keyVotes/
    recentVotes, lobbyingMatches, sponsoredBills) directly from the ORM
    relationships — NOT from build_senator_response()'s SenatorSchema, which
    only exposes vote *counts* (totalVotes, votedWithPartyCount, ...), not
    the per-vote votedWithParty/partyAlignmentWeight fields the scoring
    formulas actually read.
    """
    from app.pipeline.analyze.score_calculator import explain_scores

    senator = (
        db.query(Senator)
        .options(*_senator_eager_options())
        .filter(Senator.id == senator_id)
        .first()
    )
    if senator is None:
        return None

    def _vote_dict(v: KeyVote) -> dict:
        return {
            "votedWithParty": v.voted_with_party,
            "partyAlignmentWeight": v.party_alignment_weight,
        }

    voting_record = {
        "effectiveParty": None,  # not persisted — see score_calculator.py note; only matters for Independents
        "keyVotes": [_vote_dict(v) for v in senator.key_votes if v.vote_category == "key"],
        "recentVotes": [_vote_dict(v) for v in senator.key_votes if v.vote_category == "recent"],
    }

    funding = {
        "totalRaised": senator.total_raised,
        "totalFromPACs": senator.total_from_pacs,
        "smallDonorPercentage": senator.small_donor_percentage,
        "outsideSpendingFor": senator.outside_spending_for,
        "topDonors": [
            {"name": d.name, "total": d.total, "type": d.type}
            for d in senator.donors
        ],
        "industryBreakdown": [
            {"industry": ind.industry, "total": ind.total}
            for ind in senator.industry_donations
        ],
    }

    lobbying_matches = [
        {
            "donationToSenator": lm.donation_to_senator,
            "isConsensusVote": lm.is_consensus_vote,
        }
        for lm in senator.lobbying_matches
    ]

    sponsored_bills = [
        {
            "billType": sb.bill_type,
            "congress": sb.congress,
            "isLaw": sb.is_law,
            "latestAction": sb.latest_action,
        }
        for sb in senator.sponsored_bills
    ]

    entity = {
        "funding": funding,
        "votingRecord": voting_record,
        "lobbyingMatches": lobbying_matches,
        "sponsoredBills": sponsored_bills,
        "state": senator.state,
        "party": senator.party,
        "district": None,
        "bipartisanshipScore": senator.bipartisanship_score,
        "leadershipScore": senator.leadership_score,
        "yearsInOffice": senator.years_in_office,
    }
    return explain_scores(entity)


def get_states_with_counts(db: Session) -> list[StateCountSchema]:
    """Return a list of states that have senators, with counts."""
    rows = (
        db.query(Senator.state, func.count(Senator.id).label("cnt"))
        .group_by(Senator.state)
        .order_by(Senator.state)
        .all()
    )
    return [
        StateCountSchema(
            code=row.state,
            name=STATE_NAMES.get(row.state, row.state),
            senator_count=row.cnt,
        )
        for row in rows
    ]


def _compute_trend_map(db: Session) -> dict[str, ScoreTrendSchema]:
    """Compare latest snapshots to the best available prior snapshot.

    Prefers a snapshot from ~7 days ago; falls back to the oldest available
    snapshot that is at least 1 day older than the latest.
    """
    raw = compute_score_trend_map(db, "senator")
    return {
        sid: ScoreTrendSchema(
            direction=d["direction"], change=d["change"], previous_score=d["previousScore"],
        )
        for sid, d in raw.items()
    }


def get_leaderboard(db: Session) -> list[LeaderboardEntrySchema]:
    """Return all senators ranked by weighted representation score (higher = better representative)."""
    senators = db.query(Senator).all()

    top_industry_map: dict[str, str] = {}
    ind_rows = (
        db.query(IndustryDonation.senator_id, IndustryDonation.name)
        .order_by(IndustryDonation.senator_id, IndustryDonation.total.desc())
        .all()
    )
    for senator_id, name in ind_rows:
        if senator_id not in top_industry_map:
            top_industry_map[senator_id] = name

    trend_map = _compute_trend_map(db)

    senators.sort(key=compute_overall_score, reverse=True)

    return [
        LeaderboardEntrySchema(
            id=s.id,
            name=s.name,
            state=s.state,
            party=s.party,
            years_in_office=s.years_in_office,
            initials=_compute_initials(s.name) or s.initials,
            representation_score=RepresentationScoreSchema(
                funding_independence=s.score_funding_independence,
                promise_persistence=s.score_promise_persistence,
                independent_voting=s.score_independent_voting,
                funding_diversity=s.score_funding_diversity,
                legislative_effectiveness=s.score_legislative_effectiveness,
            ),
            total_raised=s.total_raised,
            total_from_pacs=s.total_from_pacs,
            small_donor_percentage=s.small_donor_percentage,
            top_industry=top_industry_map.get(s.id),
            trend=trend_map.get(s.id, ScoreTrendSchema()),
        )
        for s in senators
    ]


def upsert_senator(db: Session, senator_data: dict) -> Senator:
    """Insert or update a senator and all related records.

    ``senator_data`` is expected to match the SenatorSchema structure (camelCase keys).
    Used by the pipeline to persist results.
    """
    sid = senator_data["id"]

    # Upsert the senator row
    existing = db.query(Senator).filter(Senator.id == sid).first()
    if existing is None:
        existing = Senator(id=sid)
        db.add(existing)

    cs = senator_data.get("representationScore", senator_data.get("corruptionScore", {}))
    funding = senator_data.get("funding", {})

    existing.name = senator_data.get("name", existing.name)
    existing.state = senator_data.get("state", existing.state)
    existing.party = senator_data.get("party", existing.party)
    existing.years_in_office = senator_data.get("yearsInOffice", existing.years_in_office)
    existing.initials = senator_data.get("initials", existing.initials)
    existing.leadership_title = senator_data.get("leadershipTitle", existing.leadership_title)
    if "committees" in senator_data:
        existing.committees = json.dumps(senator_data["committees"])

    # Only overwrite scores when representationScore is explicitly provided.
    # A partial update (bio/contact only) must not zero out previously computed scores.
    if cs:
        existing.score_funding_independence = cs.get("fundingIndependence", existing.score_funding_independence)
        existing.score_promise_persistence = cs.get("promisePersistence", existing.score_promise_persistence)
        existing.score_independent_voting = cs.get("independentVoting", existing.score_independent_voting)
        existing.score_funding_diversity = cs.get("fundingDiversity", existing.score_funding_diversity)
        existing.score_legislative_effectiveness = cs.get("legislativeEffectiveness", existing.score_legislative_effectiveness)
        if cs.get("confidence") is not None:
            existing.score_confidence = json.dumps(cs["confidence"])

    existing.total_raised = funding.get("totalRaised", 0)
    existing.total_from_pacs = funding.get("totalFromPACs", 0)
    existing.small_donor_percentage = funding.get("smallDonorPercentage", 0)
    voting_record = senator_data.get("votingRecord", {})
    existing.voting_summary = voting_record.get("votingSummary", "")
    existing.platform_summary = senator_data.get("platformSummary", "")

    db.flush()

    # Replace related records (delete old, insert new)
    db.query(Donor).filter(Donor.senator_id == sid).delete()
    for rank, d in enumerate(funding.get("topDonors", []), start=1):
        db.add(Donor(
            senator_id=sid,
            name=d["name"],
            total=d["total"],
            type=d["type"],
            industry=d.get("industry", "OTHER"),
            rank=rank,
            pac_sponsor=d.get("pacSponsor"),
            pac_industry=d.get("pacIndustry"),
            pac_analysis=d.get("pacAnalysis"),
        ))

    db.query(IndustryDonation).filter(IndustryDonation.senator_id == sid).delete()
    for ind in funding.get("industryBreakdown", []):
        db.add(IndustryDonation(
            senator_id=sid,
            industry=ind["industry"],
            name=ind["name"],
            total=ind["total"],
            percentage=ind["percentage"],
        ))

    db.query(KeyVote).filter(KeyVote.senator_id == sid).delete()
    all_votes = (
        [(v, "recent") for v in voting_record.get("recentVotes", [])]
        + [(v, "key") for v in voting_record.get("keyVotes", [])]
    )
    # Fallback: if data uses old flat keyVotes list, treat all as "key"
    if not all_votes and voting_record.get("keyVotes"):
        all_votes = [(v, "key") for v in voting_record["keyVotes"]]
    for v, category in all_votes:
        db.add(KeyVote(
            senator_id=sid,
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

    db.query(LobbyingMatch).filter(LobbyingMatch.senator_id == sid).delete()
    for lm in senator_data.get("lobbyingMatches", []):
        db.add(LobbyingMatch(
            senator_id=sid,
            lobbyist_org=lm.get("lobbyistOrg") or "Unknown",
            industry=lm.get("industry") or "OTHER",
            lobbying_spend=lm.get("lobbyingSpend") or 0,
            donation_to_senator=lm.get("donationToSenator") or 0,
            bills_influenced=json.dumps(lm.get("billsInfluenced") or []),
            senator_vote_aligned=lm.get("senatorVoteAligned"),
            description=lm.get("description") or "",
        ))

    db.query(CampaignPromise).filter(CampaignPromise.senator_id == sid).delete()
    for cp in senator_data.get("campaignPromises", []):
        db.add(CampaignPromise(
            senator_id=sid,
            promise_text=cp.get("promiseText") or "",
            category=cp.get("category") or "other",
            alignment=cp.get("alignment") or PromiseAlignment.UNCLEAR,
            related_votes=json.dumps(cp.get("relatedVotes") or []),
            related_bills=json.dumps(cp.get("relatedBills") or []),
            analysis=cp.get("analysis") or "",
            party_alignment=cp.get("partyAlignment"),
        ))

    partisan_depth_data = senator_data.get("partisanDepth")
    if partisan_depth_data:
        existing.partisan_depth = json.dumps(partisan_depth_data)

    db.commit()
    db.refresh(existing)
    return existing


def get_senator_votes(
    db: Session,
    senator_id: str,
    category: str = "recent",
    page: int = 1,
    per_page: int = 15,
    vote_filter: str = "all",
) -> PaginatedVotesSchema | None:
    """Return paginated, filterable votes for a senator."""
    senator = db.query(Senator).filter(Senator.id == senator_id).first()
    if senator is None:
        return None

    base_q = db.query(KeyVote).filter(
        KeyVote.senator_id == senator_id,
        KeyVote.vote_category == category,
    )

    count_all = base_q.count()
    count_yea = base_q.filter(KeyVote.vote == "Yea").count()
    count_nay = base_q.filter(KeyVote.vote == "Nay").count()
    count_against = base_q.filter(KeyVote.voted_with_party == False).count()  # noqa: E712
    counts = VoteCountsSchema(all=count_all, yea=count_yea, nay=count_nay, against_party=count_against)

    query = base_q
    if vote_filter == "yea":
        query = query.filter(KeyVote.vote == "Yea")
    elif vote_filter == "nay":
        query = query.filter(KeyVote.vote == "Nay")
    elif vote_filter == "against-party":
        query = query.filter(KeyVote.voted_with_party == False)  # noqa: E712

    total = query.count()
    total_pages, page = paginate_bounds(total, page, per_page)

    votes_db = query.order_by(KeyVote.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

    def _build(v: KeyVote) -> KeyVoteSchema:
        raw_areas = []
        if getattr(v, "policy_areas", None):
            try:
                raw_areas = json.loads(v.policy_areas)
            except (json.JSONDecodeError, TypeError):
                raw_areas = []

        policy_area_details = [
            PolicyAreaDetail(
                area=a.get("area", ""),
                confidence=a.get("confidence", 0.0),
                party=a.get("party", "bipartisan"),
            )
            for a in raw_areas
            if isinstance(a, dict)
        ]

        return KeyVoteSchema(
            bill_name=v.bill_name,
            bill_id=v.bill_id,
            date=v.date,
            vote=v.vote,
            policy_area=v.policy_area or "PROCEDURAL",
            policy_areas=policy_area_details,
            party_alignment_weight=getattr(v, "party_alignment_weight", 0.0) or 0.0,
            stance=v.stance or "neutral",
            description=v.description or "",
            party_leaning=v.party_leaning,
            voted_with_party=v.voted_with_party,
            vote_category=v.vote_category or "key",
            key_vote_reasoning=v.key_vote_reasoning,
        )

    return PaginatedVotesSchema(
        votes=[_build(v) for v in votes_db],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        category=category,
        filter=vote_filter,
        counts=counts,
    )


def get_senator_stock_trades(
    db: Session,
    senator_id: str,
    page: int = 1,
    per_page: int = 15,
) -> PaginatedStockTradesSchema | None:
    """Return paginated STOCK Act trade disclosures for a senator.

    Informational only — not part of the weighted score dimensions, since
    trade-disclosure completeness varies too much per member to score
    fairly (see issue #45).
    """
    senator = db.query(Senator).filter(Senator.id == senator_id).first()
    if senator is None:
        return None

    query = db.query(StockTrade).filter(StockTrade.senator_id == senator_id)
    total = query.count()
    late_count = query.filter(StockTrade.days_to_disclose > 45).count()
    total_pages, page = paginate_bounds(total, page, per_page)

    trades_db = (
        query.order_by(StockTrade.transaction_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return PaginatedStockTradesSchema(
        trades=[
            StockTradeSchema(
                ticker=t.ticker,
                asset_name=t.asset_name,
                owner=t.owner,
                transaction_type=t.transaction_type,
                transaction_date=t.transaction_date,
                disclosure_date=t.disclosure_date,
                days_to_disclose=t.days_to_disclose,
                amount_low=t.amount_low,
                amount_high=t.amount_high,
                industry=t.industry,
                source_url=t.source_url,
                parse_confidence=t.parse_confidence,
            )
            for t in trades_db
        ],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        late_count=late_count,
    )
