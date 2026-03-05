from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Senator(Base):
    __tablename__ = "senators"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bioguide_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    party: Mapped[str] = mapped_column(String(1), nullable=False)
    years_in_office: Mapped[int] = mapped_column(Integer, default=0)
    initials: Mapped[str] = mapped_column(String(4), default="")
    punk_nickname: Mapped[str] = mapped_column(String, default="")

    score_funding_independence: Mapped[float] = mapped_column(Float, default=0.0)
    score_promise_persistence: Mapped[float] = mapped_column(Float, default=0.0)
    score_independent_voting: Mapped[float] = mapped_column(Float, default=0.0)
    score_funding_diversity: Mapped[float] = mapped_column(Float, default=0.0)

    total_raised: Mapped[float] = mapped_column(Float, default=0.0)
    total_from_pacs: Mapped[float] = mapped_column(Float, default=0.0)
    small_donor_percentage: Mapped[float] = mapped_column(Float, default=0.0)

    voting_summary: Mapped[str] = mapped_column(Text, default="")
    platform_summary: Mapped[str] = mapped_column(Text, default="")
    partisan_depth: Mapped[str | None] = mapped_column(Text, nullable=True)

    leadership_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideology_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sponsorship_description: Mapped[str] = mapped_column(String, default="")

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    donors: Mapped[list["Donor"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    industry_donations: Mapped[list["IndustryDonation"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    key_votes: Mapped[list["KeyVote"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    lobbying_matches: Mapped[list["LobbyingMatch"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    campaign_promises: Mapped[list["CampaignPromise"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    sponsored_bills: Mapped[list["SponsoredBill"]] = relationship(back_populates="senator", cascade="all, delete-orphan")


class Donor(Base):
    __tablename__ = "donors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    type: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    industry: Mapped[str] = mapped_column(String, default="OTHER")  # Industry classification for this donor
    pac_sponsor: Mapped[str | None] = mapped_column(String, nullable=True)
    pac_industry: Mapped[str | None] = mapped_column(String, nullable=True)
    pac_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)

    senator: Mapped["Senator"] = relationship(back_populates="donors")


class IndustryDonation(Base):
    __tablename__ = "industry_donations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, default=0.0)

    senator: Mapped["Senator"] = relationship(back_populates="industry_donations")


class KeyVote(Base):
    __tablename__ = "key_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    bill_name: Mapped[str] = mapped_column(String, nullable=False)
    bill_id: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    vote: Mapped[str] = mapped_column(String, nullable=False)
    policy_area: Mapped[str] = mapped_column(String, default="PROCEDURAL")
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{area, confidence, party}]
    party_alignment_weight: Mapped[float] = mapped_column(Float, default=0.0)
    stance: Mapped[str] = mapped_column(String, default="neutral")
    description: Mapped[str] = mapped_column(Text, default="")
    party_leaning: Mapped[str | None] = mapped_column(String, nullable=True)  # "R", "D", "bipartisan"
    voted_with_party: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vote_category: Mapped[str] = mapped_column(String, default="key")  # "recent" or "key"
    key_vote_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    senator: Mapped["Senator"] = relationship(back_populates="key_votes")


class LobbyingMatch(Base):
    __tablename__ = "lobbying_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False)
    lobbyist_org: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    lobbying_spend: Mapped[float] = mapped_column(Float, default=0.0)
    donation_to_senator: Mapped[float] = mapped_column(Float, default=0.0)
    bills_influenced: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    senator_vote_aligned: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    description: Mapped[str] = mapped_column(Text, default="")

    senator: Mapped["Senator"] = relationship(back_populates="lobbying_matches")


class CampaignPromise(Base):
    __tablename__ = "campaign_promises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False)
    promise_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "healthcare", "economy", "defense"
    alignment: Mapped[str] = mapped_column(String, default="unclear")  # "kept", "broken", "partial", "unclear"
    related_votes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array of bill IDs
    analysis: Mapped[str] = mapped_column(Text, default="")  # LLM explanation of alignment
    party_alignment: Mapped[str | None] = mapped_column(String, nullable=True)  # "R", "D", "bipartisan"

    senator: Mapped["Senator"] = relationship(back_populates="campaign_promises")


class SponsoredBill(Base):
    __tablename__ = "sponsored_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False)
    bill_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    introduced_date: Mapped[str] = mapped_column(String, default="")
    latest_action: Mapped[str] = mapped_column(Text, default="")
    latest_action_date: Mapped[str] = mapped_column(String, default="")
    policy_area: Mapped[str] = mapped_column(String, default="")
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{area, confidence, party}]
    party_leaning: Mapped[str | None] = mapped_column(String, nullable=True)  # "R", "D", "bipartisan"
    congress: Mapped[int] = mapped_column(Integer, default=0)
    bill_type: Mapped[str] = mapped_column(String, default="")
    is_law: Mapped[bool] = mapped_column(Boolean, default=False)

    senator: Mapped["Senator"] = relationship(back_populates="sponsored_bills")


class President(Base):
    __tablename__ = "presidents"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "obama-44"
    name: Mapped[str] = mapped_column(String, nullable=False)
    party: Mapped[str] = mapped_column(String, nullable=False)  # D, R, W(hig), F(ederalist), DR
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # 44th, 45th, etc.
    term_start: Mapped[str] = mapped_column(String, nullable=False)  # "2009-01-20"
    term_end: Mapped[str | None] = mapped_column(String, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    score_independence: Mapped[float] = mapped_column(Float, default=0.0)
    score_follow_through: Mapped[float] = mapped_column(Float, default=0.0)
    score_public_mandate: Mapped[float] = mapped_column(Float, default=0.0)
    score_effectiveness: Mapped[float] = mapped_column(Float, default=0.0)
    score_competence: Mapped[float] = mapped_column(Float, default=0.0)
    score_agency_alignment: Mapped[float] = mapped_column(Float, default=0.0)

    avg_approval: Mapped[float | None] = mapped_column(Float, nullable=True)
    gdp_growth_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    jobs_created_millions: Mapped[float | None] = mapped_column(Float, nullable=True)
    eo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eo_court_success_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cabinet_turnover_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    summary: Mapped[str] = mapped_column(Text, default="")
    key_achievements: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    key_failures: Mapped[str] = mapped_column(Text, default="[]")  # JSON array

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class Justice(Base):
    """Supreme Court justice with ideological consistency scores."""
    __tablename__ = "justices"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # oyez identifier
    name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    role_title: Mapped[str] = mapped_column(String, default="Associate Justice")
    appointing_president: Mapped[str | None] = mapped_column(String, nullable=True)
    appointing_party: Mapped[str | None] = mapped_column(String, nullable=True)  # R or D
    date_start: Mapped[str | None] = mapped_column(String, nullable=True)
    date_end: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)

    score_consistency: Mapped[float] = mapped_column(Float, default=0.0)
    score_independence: Mapped[float] = mapped_column(Float, default=0.0)
    score_bipartisan_agreement: Mapped[float] = mapped_column(Float, default=0.0)
    score_judicial_restraint: Mapped[float] = mapped_column(Float, default=0.0)

    cases_decided: Mapped[int] = mapped_column(Integer, default=0)
    majority_pct: Mapped[float] = mapped_column(Float, default=0.0)
    dissent_pct: Mapped[float] = mapped_column(Float, default=0.0)
    unanimous_pct: Mapped[float] = mapped_column(Float, default=0.0)
    authored_majority: Mapped[int] = mapped_column(Integer, default=0)
    authored_dissent: Mapped[int] = mapped_column(Integer, default=0)
    authored_concurrence: Mapped[int] = mapped_column(Integer, default=0)
    close_case_majority_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cross_bloc_pct: Mapped[float] = mapped_column(Float, default=0.0)

    agreement_matrix: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    summary: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    votes = relationship("JusticeVote", back_populates="justice", cascade="all, delete-orphan")


class JusticeVote(Base):
    """Per-case vote record for a justice."""
    __tablename__ = "justice_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    justice_id: Mapped[str] = mapped_column(String, ForeignKey("justices.id"), index=True)
    case_id: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "scotus-2024-23-191"
    case_name: Mapped[str] = mapped_column(String, default="")
    case_term: Mapped[str] = mapped_column(String, default="")
    decided_date: Mapped[str | None] = mapped_column(String, nullable=True)
    vote: Mapped[str] = mapped_column(String, nullable=False)  # majority, minority
    opinion_type: Mapped[str] = mapped_column(String, default="none")  # majority, dissent, concurrence, none
    is_unanimous: Mapped[bool] = mapped_column(Boolean, default=False)
    is_close: Mapped[bool] = mapped_column(Boolean, default=False)  # 5-4 or 5-3
    majority_votes: Mapped[int] = mapped_column(Integer, default=0)
    minority_votes: Mapped[int] = mapped_column(Integer, default=0)

    justice = relationship("Justice", back_populates="votes")


class ExploreDocument(Base):
    """Searchable government activity document for the Explore feature.

    Stores Senate/House floor proceedings, executive orders, proclamations,
    memoranda, and other official actions. Each document is also embedded in
    ChromaDB for semantic search and linked to a politician where applicable.
    """
    __tablename__ = "explore_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    politician_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    politician_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    chamber: Mapped[str | None] = mapped_column(String, nullable=True)
    agency_name: Mapped[str | None] = mapped_column(String, nullable=True)
    comment_url: Mapped[str | None] = mapped_column(String, nullable=True)
    comments_close_on: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")
    external_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class LearnedClassification(Base):
    """Persistent learning store for entity classifications.

    Each time the pipeline successfully classifies an entity (org, PAC, employer)
    via any method (rules, embeddings, LLM), the result is stored here.
    On subsequent runs, this table is checked FIRST, making the system faster
    and more consistent over time. A form of active learning.
    """
    __tablename__ = "learned_classifications"

    entity_name: Mapped[str] = mapped_column(String, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String, primary_key=True)  # "donor_type", "industry"
    value: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # 1.0=rule, 0.9=embedding, 0.7=LLM
    source: Mapped[str] = mapped_column(String, nullable=False)  # "rule", "embedding", "llm", "fec"
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)  # embedding model that produced this
    match_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: top scores, matched anchors
    learned_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ApiCache(Base):
    __tablename__ = "api_cache"

    tier: Mapped[str] = mapped_column(String, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String, primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class AnalysisCache(Base):
    __tablename__ = "analysis_cache"

    prompt_version: Mapped[str] = mapped_column(String, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String, primary_key=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")
    current_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    senators_processed: Mapped[int] = mapped_column(Integer, default=0)
    senators_total: Mapped[int] = mapped_column(Integer, default=0)
    senators_failed: Mapped[int] = mapped_column(Integer, default=0)
    bills_classified: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cache_misses: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
