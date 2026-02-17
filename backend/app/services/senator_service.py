import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CampaignPromise, Donor, IndustryDonation, KeyVote, LobbyingMatch, Senator
from app.schemas import (
    CampaignPromiseSchema,
    CorruptionScoreSchema,
    DonorSchema,
    FundingSchema,
    IndustryDonationSchema,
    KeyVoteSchema,
    LobbyingMatchSchema,
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


def build_senator_response(senator: Senator, db: Session) -> SenatorSchema:
    """Assemble a full SenatorSchema from the ORM model and related records."""

    # 1. Query donors ordered by rank
    donors = (
        db.query(Donor)
        .filter(Donor.senator_id == senator.id)
        .order_by(Donor.rank.asc())
        .all()
    )

    # 2. Query industry donations ordered by total desc
    industry_donations = (
        db.query(IndustryDonation)
        .filter(IndustryDonation.senator_id == senator.id)
        .order_by(IndustryDonation.total.desc())
        .all()
    )

    # 3. Query key votes
    key_votes = (
        db.query(KeyVote)
        .filter(KeyVote.senator_id == senator.id)
        .all()
    )

    # 4. Query lobbying matches
    lobbying_matches = (
        db.query(LobbyingMatch)
        .filter(LobbyingMatch.senator_id == senator.id)
        .all()
    )

    # 4b. Query campaign promises
    campaign_promises = (
        db.query(CampaignPromise)
        .filter(CampaignPromise.senator_id == senator.id)
        .all()
    )

    # 5. Split votes by category
    recent_votes_db = [v for v in key_votes if v.vote_category == "recent"]
    key_votes_db = [v for v in key_votes if v.vote_category == "key"]

    all_votes = key_votes
    total_votes = len(all_votes)
    pro_corporate_votes = sum(1 for v in all_votes if v.classification == "pro-corporate")
    pro_consumer_votes = sum(1 for v in all_votes if v.classification == "pro-consumer")
    voted_with = sum(1 for v in all_votes if v.voted_with_party is True)
    voted_against = sum(1 for v in all_votes if v.voted_with_party is False)
    party_loyalty_pct = round(
        voted_with / max(voted_with + voted_against, 1) * 100, 1
    )

    def _build_vote_schema(v):
        return KeyVoteSchema(
            bill_name=v.bill_name,
            bill_id=v.bill_id,
            date=v.date,
            vote=v.vote,
            pro_business_vote=v.pro_business_vote,
            classification=v.classification,
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
    return SenatorSchema(
        id=senator.id,
        name=senator.name,
        state=senator.state,
        party=senator.party,
        years_in_office=senator.years_in_office,
        initials=senator.initials,
        punk_nickname=senator.punk_nickname,
        corruption_score=CorruptionScoreSchema(
            corporate_funding=senator.score_corporate_funding,
            lobbyist_alignment=senator.score_lobbyist_alignment,
            industry_concentration=senator.score_industry_concentration,
            flip_flop_index=senator.score_flip_flop_index,
            revolving_door=senator.score_revolving_door,
        ),
        funding=FundingSchema(
            total_raised=senator.total_raised,
            total_from_pacs=senator.total_from_pacs,
            small_donor_percentage=senator.small_donor_percentage,
            top_donors=[
                DonorSchema(
                    name=d.name,
                    total=d.total,
                    type=d.type,
                    pac_sponsor=d.pac_sponsor,
                    pac_industry=d.pac_industry,
                    pac_analysis=d.pac_analysis,
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
            pro_corporate_votes=pro_corporate_votes,
            pro_consumer_votes=pro_consumer_votes,
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
        campaign_promises=[
            CampaignPromiseSchema(
                promise_text=cp.promise_text,
                category=cp.category,
                alignment=cp.alignment,
                related_votes=json.loads(cp.related_votes) if cp.related_votes else [],
                analysis=cp.analysis,
            )
            for cp in campaign_promises
        ],
        platform_summary=senator.platform_summary or "",
    )


def get_senators_by_state(db: Session, state: str) -> list[SenatorSchema]:
    """Return all senators for a given state code."""
    senators = db.query(Senator).filter(Senator.state == state.upper()).all()
    return [build_senator_response(s, db) for s in senators]


def get_senator_by_id(db: Session, senator_id: str) -> SenatorSchema | None:
    """Return a single senator by ID, or None if not found."""
    senator = db.query(Senator).filter(Senator.id == senator_id).first()
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

    cs = senator_data.get("corruptionScore", {})
    funding = senator_data.get("funding", {})

    existing.name = senator_data.get("name", existing.name)
    existing.state = senator_data.get("state", existing.state)
    existing.party = senator_data.get("party", existing.party)
    existing.years_in_office = senator_data.get("yearsInOffice", existing.years_in_office)
    existing.initials = senator_data.get("initials", existing.initials)
    existing.punk_nickname = senator_data.get("punkNickname", existing.punk_nickname)

    existing.score_corporate_funding = cs.get("corporateFunding", 0)
    existing.score_lobbyist_alignment = cs.get("lobbyistAlignment", 0)
    existing.score_industry_concentration = cs.get("industryConcentration", 0)
    existing.score_flip_flop_index = cs.get("flipFlopIndex", 0)
    existing.score_revolving_door = cs.get("revolvingDoor", 0)

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
