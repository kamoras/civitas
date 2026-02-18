from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    type: Literal["PAC", "Individual", "SuperPAC", "Org/Employees", "Party/Ideological"]
    pac_sponsor: str | None = None
    pac_industry: str | None = None
    pac_analysis: str | None = None


class IndustryDonationSchema(CamelModel):
    industry: str
    name: str
    total: float
    percentage: float


class RepresentationScoreSchema(CamelModel):
    constituent_funding: float
    independence_index: float
    donor_diversity: float
    promise_fulfillment: float
    accountability: float


class KeyVoteSchema(CamelModel):
    bill_name: str
    bill_id: str
    date: str
    vote: Literal["Yea", "Nay", "Not Voting"]
    pro_business_vote: Literal["Yea", "Nay"] | None = None
    classification: Literal["pro-corporate", "pro-consumer", "mixed"]
    description: str
    corporate_interest: str
    public_impact: str
    relevant_donors: list[str]
    relevant_donor_total: float
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
    pro_corporate_votes: int
    pro_consumer_votes: int
    voted_with_party_count: int = 0
    voted_against_party_count: int = 0
    party_loyalty_pct: float = 0.0
    voting_summary: str = ""
    recent_votes: list[KeyVoteSchema] = []
    key_votes: list[KeyVoteSchema] = []


class LobbyingMatchSchema(CamelModel):
    lobbyist_org: str
    industry: str
    lobbying_spend: float
    donation_to_senator: float
    bills_influenced: list[str]
    senator_vote_aligned: bool
    description: str


class CampaignPromiseSchema(CamelModel):
    promise_text: str
    category: str
    alignment: Literal["kept", "broken", "partial", "unclear"] = "unclear"
    related_votes: list[str] = []
    analysis: str = ""


class SenatorSchema(CamelModel):
    id: str
    name: str
    state: str
    party: Literal["D", "R", "I"]
    years_in_office: int
    initials: str
    punk_nickname: str
    representation_score: RepresentationScoreSchema
    funding: FundingSchema
    voting_record: VotingRecordSchema
    lobbying_matches: list[LobbyingMatchSchema]
    campaign_promises: list[CampaignPromiseSchema] = []
    platform_summary: str = ""


class LeaderboardEntrySchema(CamelModel):
    id: str
    name: str
    state: str
    party: Literal["D", "R", "I"]
    years_in_office: int
    initials: str
    punk_nickname: str
    representation_score: RepresentationScoreSchema
    total_raised: float
    total_from_pacs: float
    small_donor_percentage: float
    top_industry: str | None = None


# --- Pipeline / Health schemas ---


class PipelineRunSchema(CamelModel):
    id: int
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    senators_processed: int
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
