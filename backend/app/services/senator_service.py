import json
import re

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import CampaignPromise, Donor, IndustryDonation, KeyVote, LobbyingMatch, Senator

_FILLER_RE = re.compile(
    r"has received funding from|(?:^|[,.])\s*a political PAC"
    r"|opposes the removal of the United States Army"
    r"|which is (?:not )?(?:aligned with|related to) (?:his|her|their) (?:platform|stance|stated)",
    re.IGNORECASE,
)

_KEPT_RE = re.compile(
    r"align(?:s|ed|ing|ment)|support(?:s|ing|ed)?(?:\s+(?:for|of|this))?"
    r"|consistent|keeping|kept|match|fulfill|advance[sd]?|further[sd]?",
    re.IGNORECASE,
)
_BROKEN_RE = re.compile(
    r"contradict|voted\s+against|oppos(?:es|ing|ed)|undermin"
    r"|broke[n]?|fail(?:s|ed|ing)|violat|inconsistent"
    r"|does not (?:support|align|match)",
    re.IGNORECASE,
)
from app.schemas import (
    CampaignPromiseSchema,
    RepresentationScoreSchema,
    DonorSchema,
    FundingSchema,
    IndustryDonationSchema,
    KeyVoteSchema,
    LeaderboardEntrySchema,
    LobbyingMatchSchema,
    PolicyBreakdownSchema,
    SenatorSchema,
    StateCountSchema,
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


_ERROR_PAGE_RE = re.compile(
    r"(?:404\s*error|page\s*not\s*found|page\s*requested|"
    r"search\s+senate\.gov|e-?mail\s+webmaster|broken\s+link)",
    re.IGNORECASE,
)

_BILL_ID_RE = re.compile(
    r"(?:H\.R\.|S\.|H\.J\.Res\.|S\.J\.Res\.|S\.Res\.|H\.Res\.|Roll-|Amdt\.)"
)


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


_PROMISE_ARTIFACT_RE = re.compile(
    r"^On the (?:Amendment|Joint Resolution|Resolution|Bill|Motion|Cloture)"
    r"|^Pursuant to Senate Policy"
    r"|^Learn About \w+"
    r"|^See \w+'s Position",
    re.IGNORECASE,
)

def _fixup_donor_type(donor_name: str, donor_type: str, senator_name: str) -> str:
    """Read-time safety-net for donor types persisted before embedding classifier.

    Uses the same semantic classifier from the pipeline to correct
    already-persisted data that was classified with hardcoded patterns.
    Only runs when the donor_type looks suspicious (PAC without PAC keywords).
    """
    if donor_type != "PAC":
        return donor_type

    name_upper = donor_name.upper()
    has_pac_keywords = any(
        sig in name_upper for sig in ("PAC", "COMMITTEE", "FUND", "LEAGUE", "CAUCUS")
    )
    if has_pac_keywords:
        return donor_type

    try:
        from app.pipeline.analyze.donor_classifier_ai import classify_donor_type_semantic
        cand_name = ",".join(senator_name.split()[::-1]) if senator_name else ""
        sem_type = classify_donor_type_semantic(donor_name, candidate_name=cand_name)
        if sem_type and sem_type != "PAC":
            return sem_type
    except Exception:
        pass

    return donor_type


def _filter_promises(campaign_promises: list) -> list[CampaignPromiseSchema]:
    """Filter and correct campaign promise quality issues in persisted data."""
    from collections import Counter

    result = []
    seen_texts: set[str] = set()

    for cp in campaign_promises:
        analysis = cp.analysis or ""
        alignment = cp.alignment or "unclear"
        related = json.loads(cp.related_votes) if cp.related_votes else []
        promise_text = cp.promise_text or ""

        # Skip error page artifacts
        if _ERROR_PAGE_RE.search(promise_text) or _ERROR_PAGE_RE.search(analysis):
            continue

        # Skip promises that are actually browser/embed artifacts
        promise_lower = promise_text.lower()
        if any(sig in promise_lower for sig in (
            "browser does not support",
            "twitter feed",
            "skip to content",
            "menu menu menu",
            "javascript",
            "cookie",
        )):
            continue

        # Skip overly short or generic promises
        if len(promise_text.strip()) < 10:
            continue

        # Skip promises that are actually bill amendment titles or Senate policy boilerplate
        if _PROMISE_ARTIFACT_RE.search(promise_text.strip()):
            continue

        # Deduplicate by exact promise text
        text_key = promise_text.strip().lower()
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        if _FILLER_RE.search(analysis):
            analysis = ""

        kept = len(_KEPT_RE.findall(analysis))
        broken = len(_BROKEN_RE.findall(analysis))
        if kept > 0 and broken > 0:
            alignment = "unclear"
        elif alignment == "broken" and kept > 0 and broken == 0:
            alignment = "kept"
        elif alignment == "kept" and broken > 0 and kept == 0:
            alignment = "broken"

        # Downgrade bold claims that lack specific bill evidence
        if (
            alignment in ("kept", "broken")
            and analysis
            and not _BILL_ID_RE.search(analysis)
            and not related
        ):
            alignment = "unclear"
            analysis = ""

        result.append(CampaignPromiseSchema(
            promise_text=promise_text,
            category=cp.category,
            alignment=alignment,
            related_votes=related,
            analysis=analysis,
        ))

    if len(result) >= 2:
        bill_sets = [tuple(sorted(p.related_votes)) for p in result]
        counts = Counter(bill_sets)
        overused = {bs for bs, cnt in counts.items() if cnt >= 2 and bs}
        if overused:
            for p in result:
                if tuple(sorted(p.related_votes)) in overused:
                    p.alignment = "unclear"
                    p.related_votes = []
                    p.analysis = ""

    return result


def _senator_eager_options():
    """SQLAlchemy options to eager-load all senator relationships in one round-trip."""
    return [
        selectinload(Senator.donors),
        selectinload(Senator.industry_donations),
        selectinload(Senator.key_votes),
        selectinload(Senator.lobbying_matches),
        selectinload(Senator.campaign_promises),
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
    donor_aligned = 0
    donor_opposed = 0
    area_stats: dict[str, dict] = {}

    for v in all_votes:
        if (
            v.stance_vote
            and v.vote != "Not Voting"
            and v.policy_area
            and v.policy_area != "PROCEDURAL"
        ):
            area = v.policy_area
            if area not in area_stats:
                area_stats[area] = {"total": 0, "withStance": 0, "againstStance": 0}
            area_stats[area]["total"] += 1

            voted_with_stance = v.vote == v.stance_vote
            if voted_with_stance:
                area_stats[area]["withStance"] += 1
            else:
                area_stats[area]["againstStance"] += 1

            has_industry_signal = bool(v.corporate_interest)
            if not has_industry_signal:
                impacted = json.loads(v.impacted_groups) if v.impacted_groups else []
                has_industry_signal = bool(impacted)
            if has_industry_signal:
                if voted_with_stance:
                    donor_aligned += 1
                else:
                    donor_opposed += 1

    total_votes = len(all_votes)
    scoreable = donor_aligned + donor_opposed
    voted_with = sum(1 for v in all_votes if v.voted_with_party is True)
    voted_against = sum(1 for v in all_votes if v.voted_with_party is False)

    policy_breakdown = sorted(
        [
            PolicyBreakdownSchema(
                policy_area=area,
                total_votes=stats["total"],
                with_stance=stats["withStance"],
                against_stance=stats["againstStance"],
            )
            for area, stats in area_stats.items()
        ],
        key=lambda x: x.total_votes,
        reverse=True,
    )
    party_total = voted_with + voted_against
    party_loyalty_pct = round(voted_with / party_total * 100, 1) if party_total > 0 else 0.0

    def _build_vote_schema(v):
        return KeyVoteSchema(
            bill_name=v.bill_name,
            bill_id=v.bill_id,
            date=v.date,
            vote=v.vote,
            policy_area=v.policy_area or "PROCEDURAL",
            stance=v.stance or "neutral",
            stance_vote=v.stance_vote,
            impacted_groups=json.loads(v.impacted_groups) if v.impacted_groups else [],
            affected_industries=json.loads(v.affected_industries) if getattr(v, "affected_industries", None) else [],
            description=v.description,
            corporate_interest=v.corporate_interest,
            public_impact=v.public_impact,
            relevant_donors=json.loads(v.relevant_donors) if v.relevant_donors else [],
            relevant_donor_total=v.relevant_donor_total,
            party_leaning=v.party_leaning,
            voted_with_party=v.voted_with_party,
            vote_category=v.vote_category or "key",
            key_vote_reasoning=v.key_vote_reasoning,
        )

    # 6. Assemble the nested schema
    initials = _compute_initials(senator.name) or senator.initials
    return SenatorSchema(
        id=senator.id,
        name=senator.name,
        state=senator.state,
        party=senator.party,
        years_in_office=senator.years_in_office,
        initials=initials,
        approval_rating=senator.approval_rating,
        disapproval_rating=senator.disapproval_rating,
        representation_score=RepresentationScoreSchema(
            funding_independence=senator.score_funding_independence,
            promise_persistence=senator.score_promise_persistence,
            independent_voting=senator.score_independent_voting,
            funding_diversity=senator.score_funding_diversity,
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
            scoreable_votes=scoreable,
            donor_aligned_votes=donor_aligned,
            donor_opposed_votes=donor_opposed,
            policy_breakdown=policy_breakdown,
            voted_with_party_count=voted_with,
            voted_against_party_count=voted_against,
            party_loyalty_pct=party_loyalty_pct,
            voting_summary=senator.voting_summary or "",
            recent_votes=[_build_vote_schema(v) for v in recent_votes_db],
            key_votes=[_build_vote_schema(v) for v in key_votes_db],
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


from app.config_definitions import SCORE_WEIGHTS

_FIELD_TO_WEIGHT_KEY = {
    "score_funding_independence": "fundingIndependence",
    "score_promise_persistence":  "promisePersistence",
    "score_independent_voting":   "independentVoting",
    "score_funding_diversity":    "fundingDiversity",
}


def get_leaderboard(db: Session) -> list[LeaderboardEntrySchema]:
    """Return all senators ranked by weighted representation score (higher = better representative)."""
    senators = db.query(Senator).all()

    # Build top-industry map: senator_id -> industry name with highest total
    # Query all donations sorted by total desc; keep first seen per senator
    top_industry_map: dict[str, str] = {}
    ind_rows = (
        db.query(IndustryDonation.senator_id, IndustryDonation.name)
        .order_by(IndustryDonation.senator_id, IndustryDonation.total.desc())
        .all()
    )
    for senator_id, name in ind_rows:
        if senator_id not in top_industry_map:
            top_industry_map[senator_id] = name

    def _weighted_score(s: Senator) -> float:
        return sum(
            getattr(s, db_field, 0) * SCORE_WEIGHTS[weight_key]
            for db_field, weight_key in _FIELD_TO_WEIGHT_KEY.items()
        )

    senators.sort(key=_weighted_score, reverse=True)  # descending: best representatives (highest score) first

    return [
        LeaderboardEntrySchema(
            id=s.id,
            name=s.name,
            state=s.state,
            party=s.party,
            years_in_office=s.years_in_office,
            initials=_compute_initials(s.name) or s.initials,
            approval_rating=s.approval_rating,
            disapproval_rating=s.disapproval_rating,
            representation_score=RepresentationScoreSchema(
                funding_independence=s.score_funding_independence,
                promise_persistence=s.score_promise_persistence,
                independent_voting=s.score_independent_voting,
                funding_diversity=s.score_funding_diversity,
            ),
            total_raised=s.total_raised,
            total_from_pacs=s.total_from_pacs,
            small_donor_percentage=s.small_donor_percentage,
            top_industry=top_industry_map.get(s.id),
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
    existing.punk_nickname = senator_data.get("punkNickname", existing.punk_nickname)

    existing.score_funding_independence = cs.get("fundingIndependence", 0)
    existing.score_promise_persistence = cs.get("promisePersistence", 0)
    existing.score_independent_voting = cs.get("independentVoting", 0)
    existing.score_funding_diversity = cs.get("fundingDiversity", 0)

    existing.total_raised = funding.get("totalRaised", 0)
    existing.total_from_pacs = funding.get("totalFromPACs", 0)
    existing.small_donor_percentage = funding.get("smallDonorPercentage", 0)
    voting_record = senator_data.get("votingRecord", {})
    existing.voting_summary = voting_record.get("votingSummary", "")
    existing.platform_summary = senator_data.get("platformSummary", "")

    if "approvalRating" in senator_data:
        existing.approval_rating = senator_data.get("approvalRating")
        existing.disapproval_rating = senator_data.get("disapprovalRating")
        existing.approval_source = senator_data.get("approvalSource")

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
        impacted_groups = v.get("impactedGroups", [])
        affected_industries = v.get("affectedIndustries", [])
        db.add(KeyVote(
            senator_id=sid,
            bill_name=v.get("billName", "Unknown Bill"),
            bill_id=v.get("billId", ""),
            date=v.get("date", ""),
            vote=v.get("vote", "Not Voting"),
            policy_area=v.get("policyArea", "PROCEDURAL"),
            stance=v.get("stance", "neutral"),
            stance_vote=v.get("stanceVote"),
            impacted_groups=json.dumps(impacted_groups) if impacted_groups else None,
            affected_industries=json.dumps(affected_industries) if affected_industries else None,
            pro_business_vote=v.get("proBusinessVote"),
            classification=v.get("classification", "mixed"),
            description=v.get("description", ""),
            corporate_interest=v.get("corporateInterest", ""),
            public_impact=v.get("publicImpact", ""),
            relevant_donors=json.dumps(v.get("relevantDonors", [])),
            relevant_donor_total=v.get("relevantDonorTotal", 0),
            party_leaning=v.get("partyLeaning"),
            voted_with_party=v.get("votedWithParty"),
            vote_category=category,
            key_vote_reasoning=v.get("keyVoteReasoning"),
        ))

    db.query(LobbyingMatch).filter(LobbyingMatch.senator_id == sid).delete()
    for lm in senator_data.get("lobbyingMatches", []):
        db.add(LobbyingMatch(
            senator_id=sid,
            lobbyist_org=lm["lobbyistOrg"],
            industry=lm["industry"],
            lobbying_spend=lm["lobbyingSpend"],
            donation_to_senator=lm["donationToSenator"],
            bills_influenced=json.dumps(lm.get("billsInfluenced", [])),
            senator_vote_aligned=lm.get("senatorVoteAligned", False),
            description=lm.get("description", ""),
        ))

    db.query(CampaignPromise).filter(CampaignPromise.senator_id == sid).delete()
    for cp in senator_data.get("campaignPromises", []):
        db.add(CampaignPromise(
            senator_id=sid,
            promise_text=cp.get("promiseText", ""),
            category=cp.get("category", "other"),
            alignment=cp.get("alignment", "unclear"),
            related_votes=json.dumps(cp.get("relatedVotes", [])),
            analysis=cp.get("analysis", ""),
        ))

    db.commit()
    db.refresh(existing)
    return existing
