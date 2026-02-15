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

    score_corporate_funding: Mapped[float] = mapped_column(Float, default=0.0)
    score_lobbyist_alignment: Mapped[float] = mapped_column(Float, default=0.0)
    score_industry_concentration: Mapped[float] = mapped_column(Float, default=0.0)
    score_flip_flop_index: Mapped[float] = mapped_column(Float, default=0.0)
    score_revolving_door: Mapped[float] = mapped_column(Float, default=0.0)

    total_raised: Mapped[float] = mapped_column(Float, default=0.0)
    total_from_pacs: Mapped[float] = mapped_column(Float, default=0.0)
    small_donor_percentage: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    donors: Mapped[list["Donor"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    industry_donations: Mapped[list["IndustryDonation"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    key_votes: Mapped[list["KeyVote"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    lobbying_matches: Mapped[list["LobbyingMatch"]] = relationship(back_populates="senator", cascade="all, delete-orphan")


class Donor(Base):
    __tablename__ = "donors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    type: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    senator: Mapped["Senator"] = relationship(back_populates="donors")


class IndustryDonation(Base):
    __tablename__ = "industry_donations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, default=0.0)

    senator: Mapped["Senator"] = relationship(back_populates="industry_donations")


class KeyVote(Base):
    __tablename__ = "key_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False)
    bill_name: Mapped[str] = mapped_column(String, nullable=False)
    bill_id: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    vote: Mapped[str] = mapped_column(String, nullable=False)
    pro_business_vote: Mapped[str | None] = mapped_column(String, nullable=True)
    classification: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    corporate_interest: Mapped[str] = mapped_column(Text, default="")
    public_impact: Mapped[str] = mapped_column(Text, default="")
    relevant_donors: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    relevant_donor_total: Mapped[float] = mapped_column(Float, default=0.0)

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
    senator_vote_aligned: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")

    senator: Mapped["Senator"] = relationship(back_populates="lobbying_matches")


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
    senators_processed: Mapped[int] = mapped_column(Integer, default=0)
    senators_failed: Mapped[int] = mapped_column(Integer, default=0)
    bills_classified: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cache_misses: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
