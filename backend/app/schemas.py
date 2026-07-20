from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(string: str) -> str:
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# --- Sub-schemas ---


class DonorSchema(CamelModel):
    name: str
    total: float
    # "SKIP" = donor_classifier_ai.py's low-confidence sentinel (see
    # normalize_finance.py) — a real, first-class classification outcome
    # filtered out of certain aggregates elsewhere (policy_alignment.py,
    # cross_reference.py), not an error state, so it's a valid wire value.
    type: Literal["PAC", "Individual", "SuperPAC", "Org/Employees", "Party/Ideological", "CandidateAffiliated", "Self-Funded", "SKIP"]
    industry: str = "OTHER"
    pac_sponsor: str | None = None
    pac_industry: str | None = None
    pac_analysis: str | None = None
    # FEC committee_type code ("Q"=Qualified/multicandidate, "N"=Nonqualified)
    # for this donor's own committee, when known — see
    # score_calculator._funding_independence_core's PAC-utilization signal.
    committee_type: str | None = None


class IndustryDonationSchema(CamelModel):
    industry: str
    name: str
    total: float
    percentage: float


class RepresentationScoreSchema(CamelModel):
    funding_independence: float
    promise_persistence: float
    independent_voting: float
    funding_diversity: float
    legislative_effectiveness: float = 0.0
    # Backend-computed overall (score_calculator.compute_overall_score) — the
    # frontend must never recompute this from the sub-scores itself (see
    # lib/representation.ts's removed weightedScore).
    overall: float = 0.0
    # Per-dimension data-sufficiency: "high" | "medium" | "low"
    confidence: dict[str, str] | None = None


class PolicyAreaDetail(CamelModel):
    area: str
    confidence: float
    party: str = "bipartisan"


class KeyVoteSchema(CamelModel):
    bill_name: str
    bill_id: str
    date: str
    vote: Literal["Yea", "Nay", "Not Voting"]
    policy_area: str = "PROCEDURAL"
    policy_areas: list[PolicyAreaDetail] = []
    party_alignment_weight: float = 0.0
    stance: str = "neutral"
    description: str = ""
    party_leaning: Literal["R", "D", "bipartisan"] | None = None
    voted_with_party: bool | None = None
    vote_category: Literal["recent", "key"] = "key"
    key_vote_reasoning: str | None = None


class FundingSchema(CamelModel):
    total_raised: float
    total_from_pacs: float = Field(alias="totalFromPACs", serialization_alias="totalFromPACs")
    small_donor_percentage: float
    top_donors: list[DonorSchema]
    industry_breakdown: list[IndustryDonationSchema]


class VotingRecordSchema(CamelModel):
    total_votes: int
    voted_with_party_count: int = 0
    voted_against_party_count: int = 0
    party_loyalty_pct: float = 0.0
    voting_summary: str = ""
    recent_vote_count: int = 0
    key_vote_count: int = 0


class VoteCountsSchema(CamelModel):
    all: int
    yea: int
    nay: int
    against_party: int


class PaginatedVotesSchema(CamelModel):
    votes: list[KeyVoteSchema]
    total: int
    page: int
    per_page: int
    total_pages: int
    category: str
    filter: str
    counts: VoteCountsSchema


# Statutory disclosure deadline under the STOCK Act (2012) — see issue #45.
STOCK_ACT_DISCLOSURE_DEADLINE_DAYS = 45


class StockTradeSchema(CamelModel):
    ticker: str | None = None
    asset_name: str
    owner: Literal["self", "spouse", "joint", "dependent"] = "self"
    transaction_type: Literal["purchase", "sale_full", "sale_partial", "exchange"]
    transaction_date: str
    disclosure_date: str
    days_to_disclose: int
    late: bool = False
    amount_low: float
    amount_high: float
    industry: str = "UNCLASSIFIED"
    source_url: str
    parse_confidence: Literal["text", "ocr"] = "text"

    @model_validator(mode="after")
    def _compute_late(self) -> "StockTradeSchema":
        # Derived, not stored — see StockTrade model comment on
        # days_to_disclose for why this isn't a separate DB column.
        self.late = self.days_to_disclose > STOCK_ACT_DISCLOSURE_DEADLINE_DAYS
        return self


class PaginatedStockTradesSchema(CamelModel):
    trades: list[StockTradeSchema]
    total: int
    page: int
    per_page: int
    total_pages: int
    late_count: int


class CommitteeSchema(CamelModel):
    committee_name: str
    chamber: str
    title: str | None = None  # "Chairman" / "Ranking Member", else None


class LobbyingMatchSchema(CamelModel):
    lobbyist_org: str
    industry: str
    lobbying_spend: float
    donation_to_senator: float
    bills_influenced: list[str]
    senator_vote_aligned: bool | None = None
    description: str


class PolicyAlignmentSchema(CamelModel):
    area: str
    alignment: Literal["R", "D", "bipartisan"]
    strength: float


class PartisanDepthSchema(CamelModel):
    overall_lean: float
    overall_party: Literal["R", "D", "centrist"]
    depth: Literal["deep", "moderate", "centrist", "cross-cutting"]
    cross_party_count: int
    total_positions: int
    policy_breakdown: list[PolicyAlignmentSchema] = []


class CampaignPromiseSchema(CamelModel):
    promise_text: str
    category: str
    alignment: Literal["kept", "broken", "partial", "unclear"] = "unclear"
    related_votes: list[str] = []
    related_bills: list[str] = []
    analysis: str = ""
    party_alignment: Literal["R", "D", "bipartisan"] | None = None


class SponsoredBillSchema(CamelModel):
    bill_id: str
    title: str
    introduced_date: str = ""
    latest_action: str = ""
    latest_action_date: str = ""
    policy_area: str = ""
    policy_areas: list[PolicyAreaDetail] = []
    party_leaning: Literal["R", "D", "bipartisan"] | None = None
    congress: int = 0
    bill_type: str = ""
    is_law: bool = False
    stage: str = ""


class BillInFlightSchema(CamelModel):
    bill_id: str
    title: str
    chamber: Literal["senate", "house"]
    sponsor_id: str
    sponsor_name: str
    sponsor_party: Literal["D", "R", "I"]
    sponsor_state: str
    sponsor_thumbnail_url: str | None = None
    introduced_date: str = ""
    latest_action: str = ""
    latest_action_date: str = ""
    stage: str = ""
    policy_area: str = ""
    congress: int = 0
    bill_type: str = ""
    is_law: bool = False
    mention_count: int = 0


class PaginatedBillsSchema(CamelModel):
    bills: list[BillInFlightSchema]
    total: int
    page: int
    per_page: int
    total_pages: int
    stage_counts: dict[str, int]


class RelatedIssueSchema(CamelModel):
    id: int
    date: str
    title: str


class BillDetailSchema(BillInFlightSchema):
    policy_areas: list[PolicyAreaDetail] = []
    party_leaning: Literal["R", "D", "bipartisan"] | None = None
    related_issues: list[RelatedIssueSchema] = []


class SenatorSchema(CamelModel):
    id: str
    name: str
    state: str
    party: Literal["D", "R", "I"]
    years_in_office: int
    initials: str
    leadership_title: str | None = None
    committees: list[CommitteeSchema] = []
    representation_score: RepresentationScoreSchema
    funding: FundingSchema
    voting_record: VotingRecordSchema
    lobbying_matches: list[LobbyingMatchSchema]
    campaign_promises: list[CampaignPromiseSchema] = []
    platform_summary: str = ""
    partisan_depth: PartisanDepthSchema | None = None
    sponsored_bills: list[SponsoredBillSchema] = []
    leadership_score: float | None = None
    bipartisanship_score: float | None = None
    ideology_score: float | None = None
    sponsorship_description: str = ""
    website_url: str = ""
    contact_form_url: str = ""
    office_phone: str = ""
    office_address: str = ""


class RepresentativeSchema(CamelModel):
    """Mirrors SenatorSchema field-for-field (House and Senate detail
    responses share nearly the same shape — see the identical sub-schemas
    reused below), plus the House-specific `district`."""
    id: str
    name: str
    state: str
    district: int
    party: Literal["D", "R", "I"]
    years_in_office: int
    initials: str
    leadership_title: str | None = None
    committees: list[CommitteeSchema] = []
    representation_score: RepresentationScoreSchema
    funding: FundingSchema
    voting_record: VotingRecordSchema
    lobbying_matches: list[LobbyingMatchSchema]
    campaign_promises: list[CampaignPromiseSchema] = []
    platform_summary: str = ""
    partisan_depth: PartisanDepthSchema | None = None
    sponsored_bills: list[SponsoredBillSchema] = []
    leadership_score: float | None = None
    bipartisanship_score: float | None = None
    ideology_score: float | None = None
    sponsorship_description: str = ""
    website_url: str = ""
    contact_form_url: str = ""
    office_phone: str = ""
    office_address: str = ""


class PaginatedRepresentativesSchema(CamelModel):
    entries: list[RepresentativeSchema]
    total: int
    page: int
    per_page: int
    total_pages: int


class ScoreTrendSchema(CamelModel):
    direction: Literal["up", "down", "stable", "new"] = "new"
    change: float = 0.0
    previous_score: float | None = None


class LeaderboardEntrySchema(CamelModel):
    id: str
    name: str
    state: str
    party: Literal["D", "R", "I"]
    years_in_office: int
    initials: str
    representation_score: RepresentationScoreSchema
    total_raised: float
    total_from_pacs: float
    small_donor_percentage: float
    top_industry: str | None = None
    trend: ScoreTrendSchema = ScoreTrendSchema()
    # SVD-based, cosponsorship-derived (Tauberer 2012) — 0 = most-left,
    # 1 = most-right, computed without party labels as input. None when
    # too little cosponsorship data exists to compute it (see
    # sponsorship_analysis.compute_ideology_scores).
    ideology_score: float | None = None
    # Backend-computed via sponsorship_analysis.describe_senator_position —
    # frontend must never re-derive this from ideology_score itself, since
    # the party-relative bucketing (D/R use a 30/70 split, independents
    # 35/65) isn't reproducible from the number alone.
    ideology_label: str | None = None
    # PageRank cosponsorship centrality (sponsorship_analysis.
    # compute_leadership_scores), log-rescaled to [0, 1] to counter its
    # power-law distribution — most members cluster low, a few attract
    # disproportionate cosponsor weight. None when too little
    # cosponsorship data exists to compute it.
    leadership_score: float | None = None


# --- Pipeline / Health schemas ---


class PipelineRunSchema(CamelModel):
    id: int
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    current_phase: str | None = None
    senators_processed: int
    senators_total: int = 0
    senators_failed: int
    bills_classified: int
    llm_calls: int
    cache_hits: int
    cache_misses: int
    elapsed_seconds: float | None = None
    error_message: str | None = None


class PipelineStatusSchema(CamelModel):
    last_run: PipelineRunSchema | None = None
    next_scheduled: str | None = None
    is_running: bool = False


class HealthSchema(CamelModel):
    status: str
    database: str
    ollama: str
    last_pipeline_run: datetime | None = None


class StateCountSchema(CamelModel):
    code: str
    name: str
    senator_count: int


# --- Presidential schemas ---


class PresidentialScoreSchema(CamelModel):
    independence: float
    follow_through: float
    public_mandate: float
    effectiveness: float
    competence: float
    agency_alignment: float
    # Backend-computed overall (president_scorer.compute_president_overall_score).
    overall: float = 0.0


class PresidentSchema(CamelModel):
    id: str
    name: str
    party: str
    number: int
    term_start: str
    term_end: str | None = None
    is_current: bool = False
    score: PresidentialScoreSchema
    avg_approval: float | None = None
    gdp_growth_avg: float | None = None
    jobs_created_millions: float | None = None
    eo_count: int | None = None
    eo_court_success_pct: float | None = None
    cabinet_turnover_pct: float | None = None
    # True when this president's Competence score actually blended in
    # live EO-activity data (see calc_competence) rather than being pure
    # seed. Court-success and cabinet-turnover rates are never live for
    # any president (no fetch source exists), so even "live" Competence
    # here is partial — this flag distinguishes "partially computed" from
    # "entirely a one-time editorial estimate" for the frontend badge.
    competence_has_live_data: bool = False
    summary: str = ""
    key_achievements: list[str] = []
    key_failures: list[str] = []


class PresidentLeaderboardEntry(CamelModel):
    id: str
    name: str
    party: str
    number: int
    term_start: str
    term_end: str | None = None
    is_current: bool = False
    score: PresidentialScoreSchema
    avg_approval: float | None = None
    gdp_growth_avg: float | None = None


# ── Supreme Court Justices ──────────────────────────────────────────

class JusticeScoreSchema(CamelModel):
    consistency: float
    independence: float
    bipartisan_agreement: float
    judicial_restraint: float
    # Backend-computed overall (justice_service._build_score).
    overall: float = 0.0


class JusticeSchema(CamelModel):
    id: str
    name: str
    last_name: str
    role_title: str = "Associate Justice"
    appointing_president: str | None = None
    appointing_party: str | None = None
    date_start: str | None = None
    is_active: bool = True
    thumbnail_url: str | None = None
    score: JusticeScoreSchema
    cases_decided: int = 0
    majority_pct: float = 0.0
    dissent_pct: float = 0.0
    unanimous_pct: float = 0.0
    authored_majority: int = 0
    authored_dissent: int = 0
    authored_concurrence: int = 0
    close_case_majority_pct: float = 0.0
    cross_bloc_pct: float = 0.0
    agreement_matrix: dict[str, float] = {}
    summary: str = ""


class JusticeLeaderboardEntry(CamelModel):
    id: str
    name: str
    last_name: str
    role_title: str = "Associate Justice"
    appointing_president: str | None = None
    appointing_party: str | None = None
    is_active: bool = True
    thumbnail_url: str | None = None
    score: JusticeScoreSchema
    cases_decided: int = 0
    majority_pct: float = 0.0
    dissent_pct: float = 0.0
    cross_bloc_pct: float = 0.0


# ── Action Center ─────────────────────────────────────────────────

class RelatedExploreDoc(CamelModel):
    id: int
    title: str
    doc_type: str
    date: str
    url: str | None = None
    comment_url: str | None = None
    comments_close_on: str | None = None


class RelatedSenator(CamelModel):
    id: str
    name: str
    state: str
    party: Literal["D", "R", "I"]
    overall_score: float
    leadership_score: float | None = None
    bipartisanship_score: float | None = None
    chamber: str = "senate"
    match_reason: str | None = None


class ActionItemSchema(CamelModel):
    text: str
    type: str = "general"
    url: str | None = None


class RelatedBillSchema(CamelModel):
    name: str
    id: str
    url: str


class ActionIssueSchema(CamelModel):
    id: int
    date: str
    rank: int
    title: str
    summary: str
    facts: list[str] = []
    actions: list[ActionItemSchema] = []
    source_urls: list[str] = []
    source_names: list[str] = []
    policy_areas: list[str] = []
    related_bills: list[RelatedBillSchema] = []
    related_explore_docs: list[RelatedExploreDoc] = []
    related_senators: list[RelatedSenator] = []
    related_monitor_slugs: list[str] = []
    concerned_count: int = 0
    not_priority_count: int = 0
    full_story: str | None = None


class MonitorUpdateSchema(CamelModel):
    id: int
    date: str
    summary: str
    source_url: str
    source_name: str
    article_title: str
    created_at: str = ""


class NationalMonitorSchema(CamelModel):
    id: int
    slug: str
    title: str
    description: str
    category: str
    status: str
    policy_areas: list[str] = []
    created_at: str
    updated_at: str
    last_article_date: str | None = None
    update_count: int = 0


class NationalMonitorDetailSchema(NationalMonitorSchema):
    updates: list[MonitorUpdateSchema] = []


class TimelineEntrySchema(CamelModel):
    date: str
    title: str
    summary: str
    policy_areas: list[str] = []
    source_url: str | None = None
    source_name: str | None = None
    monitor_slug: str | None = None
