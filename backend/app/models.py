from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, VisitsBase
from app.time_utils import utcnow


class PipelineStatus(StrEnum):
    """Lifecycle status shared by PipelineRun/HousePipelineRun/
    StockTradesPipelineRun — was independently redefined as bare string
    literals ("running"/"completed"/"failed") across 10+ files, so a typo
    in any one comparison site would silently produce a job that's never
    detected as stuck/failed. Not the same vocabulary as senate_pipeline.py's
    ProgressTracker per-step status (pending/active/done/skipped) — that
    tracks phases *within* one run, this tracks the run itself.

    A StrEnum, not a SQLAlchemy Enum column type: members compare and
    serialize identically to plain strings, so this is a drop-in
    replacement for the existing `Mapped[str]` columns with zero schema
    migration and zero behavior change for already-stored rows.
    """
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # House-only: some reps succeeded, some failed
    STALE = "stale"  # Senate-only: a "running" row exceeded the timeout with no update


class PromiseAlignment(StrEnum):
    """Campaign-promise-vs-record verdict, shared by CampaignPromise/
    RepCampaignPromise — was independently redefined as bare string
    literals across promise_quality.py, policy_alignment.py,
    cross_reference.py, senate_pipeline.py, score_calculator.py,
    senator_service.py, representative_service.py, and highlights.py.

    A StrEnum: members compare and serialize identically to plain
    strings, so this is a drop-in replacement for the existing
    `Mapped[str]` columns with zero schema migration.
    """
    KEPT = "kept"
    BROKEN = "broken"
    PARTIAL = "partial"
    UNCLEAR = "unclear"


class MonitorStatus(StrEnum):
    """Lifecycle status of a NationalMonitor, compared/assigned as bare
    strings across action_center.py and api/action.py — same typo-risk
    pattern as PipelineStatus/PromiseAlignment above.

    A StrEnum: members compare and serialize identically to plain
    strings, so this is a drop-in replacement for the existing
    `Mapped[str]` column with zero schema migration.
    """
    ACTIVE = "active"
    WATCHING = "watching"  # inactive >7 days, still tracked
    CLOSED = "closed"  # inactive >30 days, archived


class Senator(Base):
    __tablename__ = "senators"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bioguide_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    party: Mapped[str] = mapped_column(String(1), nullable=False)
    years_in_office: Mapped[int] = mapped_column(Integer, default=0)
    initials: Mapped[str] = mapped_column(String(4), default="")
    # Seat vacancy — mirrors President.is_current / Justice.is_active.
    # Set manually via the admin panel (no automated detection — see
    # AGENTS.md). Historical scores/data are left untouched when a seat
    # goes vacant; the directory and profile page show a banner instead
    # of hiding or silently continuing to display the departed member
    # as if still serving.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    vacancy_reason: Mapped[str | None] = mapped_column(String, nullable=True)  # "deceased" | "resigned" | "expelled"
    left_office_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD

    # Chamber/party leadership title (e.g. "Senate Majority Leader") and
    # committee assignments. Ingested from unitedstates/congress-legislators
    # (CC0-1.0) via scripts/fetch_committee_data.py — Congress.gov's own API
    # exposes neither (confirmed 2026-07: member records carry no
    # committee/leadership fields, and committee-detail records list
    # bills/reports/nominations but never a member roster). committees is
    # a JSON list of {committeeName, chamber, title} — same JSON-TEXT-column
    # pattern already used elsewhere (e.g. ActionIssue.facts).
    leadership_title: Mapped[str | None] = mapped_column(String, nullable=True)
    committees: Mapped[str] = mapped_column(Text, default="[]")

    # New-insert default is the neutral prior (50), not 0: a row created
    # before its first scoring pass is "unknown", and per the scoring
    # standard (score_calculator: "Missing data yields a neutral 50, never a
    # perfect 100 or 0") unknown must not read as a fully-captured 0.
    score_funding_independence: Mapped[float] = mapped_column(Float, default=50.0)
    score_promise_persistence: Mapped[float] = mapped_column(Float, default=50.0)
    score_independent_voting: Mapped[float] = mapped_column(Float, default=50.0)
    score_funding_diversity: Mapped[float] = mapped_column(Float, default=50.0)
    score_legislative_effectiveness: Mapped[float] = mapped_column(Float, default=50.0)
    # Per-dimension data-sufficiency ("high"/"medium"/"low") as JSON —
    # see score_calculator.calculate_confidence.
    score_confidence: Mapped[str] = mapped_column(Text, default="{}")

    total_raised: Mapped[float] = mapped_column(Float, default=0.0)
    total_from_pacs: Mapped[float] = mapped_column(Float, default=0.0)
    small_donor_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    # Super PAC independent expenditures supporting the candidate (FEC
    # Schedule E). Persisted so the on-demand score-breakdown endpoint can
    # reproduce _funding_independence_core's exact PAC-ratio math — this
    # was previously only held in the pipeline's local `funding` dict and
    # discarded once folded into score_funding_independence.
    outside_spending_for: Mapped[float | None] = mapped_column(Float, nullable=True)

    voting_summary: Mapped[str] = mapped_column(Text, default="")
    platform_summary: Mapped[str] = mapped_column(Text, default="")
    partisan_depth: Mapped[str | None] = mapped_column(Text, nullable=True)

    leadership_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideology_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    bipartisanship_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sponsorship_description: Mapped[str] = mapped_column(String, default="")

    website_url: Mapped[str] = mapped_column(String, default="")
    contact_form_url: Mapped[str] = mapped_column(String, default="")
    office_phone: Mapped[str] = mapped_column(String(20), default="")
    office_address: Mapped[str] = mapped_column(String, default="")

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    donors: Mapped[list["Donor"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    industry_donations: Mapped[list["IndustryDonation"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    key_votes: Mapped[list["KeyVote"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    lobbying_matches: Mapped[list["LobbyingMatch"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    campaign_promises: Mapped[list["CampaignPromise"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    sponsored_bills: Mapped[list["SponsoredBill"]] = relationship(back_populates="senator", cascade="all, delete-orphan")
    stock_trades: Mapped[list["StockTrade"]] = relationship(back_populates="senator", cascade="all, delete-orphan")


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
    # FEC committee_type code ("Q"=Qualified/multicandidate, "N"=Nonqualified,
    # etc.) for the donor's own committee, when this donor is a PAC — used to
    # compute its per-election contribution cap for the PAC-utilization
    # signal in score_calculator._funding_independence_core. None for
    # non-PAC donors or when the lookup couldn't resolve a committee ID.
    committee_type: Mapped[str | None] = mapped_column(String, nullable=True)

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
    # For R/D-labeled crossing votes only: how unified the OPPOSING party
    # was on its own side (0.65-1.0 by construction of the 65/35 labeling
    # threshold — see normalize_votes.opposing_party_unity). None when the
    # vote is "bipartisan"-labeled or lacks enough roll-call data. Used by
    # Constituent Alignment to distinguish a crossing that reads as
    # consensus-building from one that reads as adopting the opposition's
    # own party line.
    opposing_party_unity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    voted_with_party: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vote_category: Mapped[str] = mapped_column(String, default="key")  # "recent" or "key"
    key_vote_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    senator: Mapped["Senator"] = relationship(back_populates="key_votes")


class LobbyingMatch(Base):
    __tablename__ = "lobbying_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    lobbyist_org: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    lobbying_spend: Mapped[float] = mapped_column(Float, default=0.0)
    donation_to_senator: Mapped[float] = mapped_column(Float, default=0.0)
    bills_influenced: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    senator_vote_aligned: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    # Whether the matched donor-related votes were consensus (near-unanimous)
    # votes. Persisted for the same on-demand breakdown-recompute reason as
    # Senator.outside_spending_for above — previously only lived in
    # policy_alignment.py's transient match dict.
    is_consensus_vote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")

    senator: Mapped["Senator"] = relationship(back_populates="lobbying_matches")


class CampaignPromise(Base):
    __tablename__ = "campaign_promises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    promise_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "healthcare", "economy", "defense"
    alignment: Mapped[str] = mapped_column(String, default=PromiseAlignment.UNCLEAR)
    related_votes: Mapped[str] = mapped_column(Text, default="[]")  # JSON array of vote bill IDs
    related_bills: Mapped[str] = mapped_column(Text, default="[]")  # JSON array of sponsored bill IDs
    analysis: Mapped[str] = mapped_column(Text, default="")  # factual reasoning citing evidence
    party_alignment: Mapped[str | None] = mapped_column(String, nullable=True)  # "R", "D", "bipartisan"

    senator: Mapped["Senator"] = relationship(back_populates="campaign_promises")


class SponsoredBill(Base):
    __tablename__ = "sponsored_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
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
    stage: Mapped[str] = mapped_column(String, default="", index=True)  # see config_definitions.BILL_STAGES

    senator: Mapped["Senator"] = relationship(back_populates="sponsored_bills")


class StockTrade(Base):
    """STOCK Act periodic transaction report (PTR) entry. See issue #45."""
    __tablename__ = "stock_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)  # not all disclosed assets are tickered equities
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, default="self")  # self | spouse | joint | dependent
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)  # purchase | sale_full | sale_partial | exchange
    transaction_date: Mapped[str] = mapped_column(String, nullable=False)
    disclosure_date: Mapped[str] = mapped_column(String, nullable=False)
    days_to_disclose: Mapped[int] = mapped_column(Integer, default=0)
    amount_low: Mapped[float] = mapped_column(Float, default=0.0)
    amount_high: Mapped[float] = mapped_column(Float, default=0.0)
    industry: Mapped[str] = mapped_column(String, default="UNCLASSIFIED")
    source_url: Mapped[str] = mapped_column(String, default="")
    filing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)  # dedupe key
    # "text" = parsed from a PDF/HTML text layer, "ocr" = OCR fallback on a
    # scanned filing. Surfaced to the frontend/API so low-confidence OCR
    # rows can be visually flagged rather than presented as equally
    # reliable — see grounding.py's precedent of never silently trusting
    # unverified extracted content.
    parse_confidence: Mapped[str] = mapped_column(String, default="text")

    senator: Mapped["Senator"] = relationship(back_populates="stock_trades")


class Representative(Base):
    """U.S. House of Representatives member with representation scores."""
    __tablename__ = "representatives"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bioguide_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    district: Mapped[int] = mapped_column(Integer, default=0)
    party: Mapped[str] = mapped_column(String(1), nullable=False)
    years_in_office: Mapped[int] = mapped_column(Integer, default=0)
    initials: Mapped[str] = mapped_column(String(4), default="")

    # See Senator.leadership_title/committees for the rationale and source.
    leadership_title: Mapped[str | None] = mapped_column(String, nullable=True)
    committees: Mapped[str] = mapped_column(Text, default="[]")

    # New-insert default is the neutral prior (50), not 0: a row created
    # before its first scoring pass is "unknown", and per the scoring
    # standard (score_calculator: "Missing data yields a neutral 50, never a
    # perfect 100 or 0") unknown must not read as a fully-captured 0.
    score_funding_independence: Mapped[float] = mapped_column(Float, default=50.0)
    score_promise_persistence: Mapped[float] = mapped_column(Float, default=50.0)
    score_independent_voting: Mapped[float] = mapped_column(Float, default=50.0)
    score_funding_diversity: Mapped[float] = mapped_column(Float, default=50.0)
    score_legislative_effectiveness: Mapped[float] = mapped_column(Float, default=50.0)
    # Per-dimension data-sufficiency ("high"/"medium"/"low") as JSON —
    # see score_calculator.calculate_confidence.
    score_confidence: Mapped[str] = mapped_column(Text, default="{}")

    total_raised: Mapped[float] = mapped_column(Float, default=0.0)
    total_from_pacs: Mapped[float] = mapped_column(Float, default=0.0)
    small_donor_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    # Super PAC independent expenditures supporting the candidate (FEC
    # Schedule E). Persisted so the on-demand score-breakdown endpoint can
    # reproduce _funding_independence_core's exact PAC-ratio math — this
    # was previously only held in the pipeline's local `funding` dict and
    # discarded once folded into score_funding_independence.
    outside_spending_for: Mapped[float | None] = mapped_column(Float, nullable=True)

    voting_summary: Mapped[str] = mapped_column(Text, default="")
    platform_summary: Mapped[str] = mapped_column(Text, default="")
    partisan_depth: Mapped[str | None] = mapped_column(Text, nullable=True)

    leadership_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideology_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    bipartisanship_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sponsorship_description: Mapped[str] = mapped_column(String, default="")

    website_url: Mapped[str] = mapped_column(String, default="")
    contact_form_url: Mapped[str] = mapped_column(String, default="")
    office_phone: Mapped[str] = mapped_column(String(20), default="")
    office_address: Mapped[str] = mapped_column(String, default="")
    # See Senator.is_current for the rationale.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    vacancy_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    left_office_date: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    donors: Mapped[list["RepDonor"]] = relationship(back_populates="representative", cascade="all, delete-orphan")
    industry_donations: Mapped[list["RepIndustryDonation"]] = relationship(back_populates="representative", cascade="all, delete-orphan")
    key_votes: Mapped[list["RepKeyVote"]] = relationship(back_populates="representative", cascade="all, delete-orphan")
    lobbying_matches: Mapped[list["RepLobbyingMatch"]] = relationship(back_populates="representative", cascade="all, delete-orphan")
    campaign_promises: Mapped[list["RepCampaignPromise"]] = relationship(back_populates="representative", cascade="all, delete-orphan")
    sponsored_bills: Mapped[list["RepSponsoredBill"]] = relationship(back_populates="representative", cascade="all, delete-orphan")
    stock_trades: Mapped[list["RepStockTrade"]] = relationship(back_populates="representative", cascade="all, delete-orphan")


class RepDonor(Base):
    __tablename__ = "rep_donors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    type: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    industry: Mapped[str] = mapped_column(String, default="OTHER")
    pac_sponsor: Mapped[str | None] = mapped_column(String, nullable=True)
    pac_industry: Mapped[str | None] = mapped_column(String, nullable=True)
    pac_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FEC committee_type code ("Q"=Qualified/multicandidate, "N"=Nonqualified,
    # etc.) for the donor's own committee, when this donor is a PAC — used to
    # compute its per-election contribution cap for the PAC-utilization
    # signal in score_calculator._funding_independence_core. None for
    # non-PAC donors or when the lookup couldn't resolve a committee ID.
    committee_type: Mapped[str | None] = mapped_column(String, nullable=True)

    representative: Mapped["Representative"] = relationship(back_populates="donors")


class RepIndustryDonation(Base):
    __tablename__ = "rep_industry_donations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, default=0.0)

    representative: Mapped["Representative"] = relationship(back_populates="industry_donations")


class RepKeyVote(Base):
    __tablename__ = "rep_key_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    bill_name: Mapped[str] = mapped_column(String, nullable=False)
    bill_id: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    vote: Mapped[str] = mapped_column(String, nullable=False)
    policy_area: Mapped[str] = mapped_column(String, default="PROCEDURAL")
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")
    party_alignment_weight: Mapped[float] = mapped_column(Float, default=0.0)
    stance: Mapped[str] = mapped_column(String, default="neutral")
    description: Mapped[str] = mapped_column(Text, default="")
    party_leaning: Mapped[str | None] = mapped_column(String, nullable=True)
    # See KeyVote.opposing_party_unity_pct — same field, House side.
    opposing_party_unity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    voted_with_party: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vote_category: Mapped[str] = mapped_column(String, default="key")
    key_vote_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    representative: Mapped["Representative"] = relationship(back_populates="key_votes")


class RepLobbyingMatch(Base):
    __tablename__ = "rep_lobbying_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    lobbyist_org: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    lobbying_spend: Mapped[float] = mapped_column(Float, default=0.0)
    donation_to_representative: Mapped[float] = mapped_column(Float, default=0.0)
    bills_influenced: Mapped[str] = mapped_column(Text, default="[]")
    representative_vote_aligned: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    is_consensus_vote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")

    representative: Mapped["Representative"] = relationship(back_populates="lobbying_matches")


class RepCampaignPromise(Base):
    __tablename__ = "rep_campaign_promises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    promise_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    alignment: Mapped[str] = mapped_column(String, default=PromiseAlignment.UNCLEAR)
    related_votes: Mapped[str] = mapped_column(Text, default="[]")
    related_bills: Mapped[str] = mapped_column(Text, default="[]")
    analysis: Mapped[str] = mapped_column(Text, default="")
    party_alignment: Mapped[str | None] = mapped_column(String, nullable=True)

    representative: Mapped["Representative"] = relationship(back_populates="campaign_promises")


class RepSponsoredBill(Base):
    __tablename__ = "rep_sponsored_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    bill_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    introduced_date: Mapped[str] = mapped_column(String, default="")
    latest_action: Mapped[str] = mapped_column(Text, default="")
    latest_action_date: Mapped[str] = mapped_column(String, default="")
    policy_area: Mapped[str] = mapped_column(String, default="")
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")
    party_leaning: Mapped[str | None] = mapped_column(String, nullable=True)
    congress: Mapped[int] = mapped_column(Integer, default=0)
    bill_type: Mapped[str] = mapped_column(String, default="")
    is_law: Mapped[bool] = mapped_column(Boolean, default=False)
    stage: Mapped[str] = mapped_column(String, default="", index=True)  # see config_definitions.BILL_STAGES

    representative: Mapped["Representative"] = relationship(back_populates="sponsored_bills")


class RepStockTrade(Base):
    """STOCK Act periodic transaction report (PTR) entry. See issue #45."""
    __tablename__ = "rep_stock_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, default="self")
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    transaction_date: Mapped[str] = mapped_column(String, nullable=False)
    disclosure_date: Mapped[str] = mapped_column(String, nullable=False)
    days_to_disclose: Mapped[int] = mapped_column(Integer, default=0)
    amount_low: Mapped[float] = mapped_column(Float, default=0.0)
    amount_high: Mapped[float] = mapped_column(Float, default=0.0)
    industry: Mapped[str] = mapped_column(String, default="UNCLASSIFIED")
    source_url: Mapped[str] = mapped_column(String, default="")
    filing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parse_confidence: Mapped[str] = mapped_column(String, default="text")

    representative: Mapped["Representative"] = relationship(back_populates="stock_trades")


class President(Base):
    __tablename__ = "presidents"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "obama-44"
    name: Mapped[str] = mapped_column(String, nullable=False)
    party: Mapped[str] = mapped_column(String, nullable=False)  # D, R, W(hig), F(ederalist), DR
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # 44th, 45th, etc.
    term_start: Mapped[str] = mapped_column(String, nullable=False)  # "2009-01-20"
    term_end: Mapped[str | None] = mapped_column(String, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

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
    # Persisted so the on-demand score-breakdown endpoint can recompute
    # calc_effectiveness/calc_agency_alignment's exact inputs without a
    # live re-fetch from FRED/Federal Register — these were previously
    # only held in president_pipeline.py's local `live` dict and discarded
    # once folded into score_effectiveness/score_agency_alignment.
    gdp_growth_adjusted: Mapped[float | None] = mapped_column(Float, nullable=True)
    rulemaking_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rulemaking_finalized_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    summary: Mapped[str] = mapped_column(Text, default="")
    key_achievements: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    key_failures: Mapped[str] = mapped_column(Text, default="[]")  # JSON array

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


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

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

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

    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ActionIssue(Base):
    """Daily action center issues derived from news + legislative activity."""
    __tablename__ = "action_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    facts: Mapped[str] = mapped_column(Text, default="[]")
    actions: Mapped[str] = mapped_column(Text, default="[]")
    source_urls: Mapped[str] = mapped_column(Text, default="[]")
    source_names: Mapped[str] = mapped_column(Text, default="[]")
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")
    related_bill_ids: Mapped[str] = mapped_column(Text, default="[]")
    related_explore_ids: Mapped[str] = mapped_column(Text, default="[]")
    related_senators: Mapped[str] = mapped_column(Text, default="[]")
    related_officials: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    related_monitor_slugs: Mapped[str] = mapped_column(Text, default="[]")
    concerned_count: Mapped[int] = mapped_column(Integer, default=0)
    not_priority_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    full_story: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    bsky_posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    bsky_posted_rank: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Text of the most recent Bluesky post published for this issue. Used to
    # suppress near-duplicate reposts when a topic gets fresh coverage whose
    # post would say essentially the same thing as the last one.
    bsky_last_post_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    primary_article_date: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)


class ScoreSnapshot(Base):
    """Daily score snapshot for tracking leaderboard trends over time."""
    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # "senator", "president", "justice"
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    score_1: Mapped[float] = mapped_column(Float, default=0.0)
    score_2: Mapped[float] = mapped_column(Float, default=0.0)
    score_3: Mapped[float] = mapped_column(Float, default=0.0)
    score_4: Mapped[float] = mapped_column(Float, default=0.0)
    score_5: Mapped[float] = mapped_column(Float, default=0.0)
    # Scoring algorithm version that produced this snapshot (e.g. "v4.1").
    # Lets trend charts annotate methodology changes so a score shift from
    # an algorithm update isn't read as a behavior change.
    algorithm_version: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


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
    learned_at: Mapped[datetime] = mapped_column(default=utcnow)


class TimelineEntry(Base):
    """Permanent record of each day's top issue for year-in-review tracking."""
    __tablename__ = "timeline_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    monitor_slug: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WeekSummary(Base):
    """LLM-generated 'week in review' for a completed ISO week."""
    __tablename__ = "week_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week_num: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)  # Monday YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String(10), nullable=False)    # Sunday YYYY-MM-DD
    summary: Mapped[str] = mapped_column(Text, default="")
    top_policy_areas: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow)
    bsky_posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)


class MonthSummary(Base):
    """LLM-generated 'month in review' for a completed calendar month."""
    __tablename__ = "month_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-12
    summary: Mapped[str] = mapped_column(Text, default="")
    top_policy_areas: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow)


class YearSummary(Base):
    """LLM-generated 'year in review' for a completed calendar year."""
    __tablename__ = "year_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    top_policy_areas: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow)


class NationalMonitor(Base):
    """An ongoing national concern tracked over time (wars, crises, etc.)."""
    __tablename__ = "national_monitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="general")
    status: Mapped[str] = mapped_column(String(20), default=MonitorStatus.ACTIVE, index=True)
    policy_areas: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    last_article_date: Mapped[str | None] = mapped_column(String(10), nullable=True)

    updates = relationship("MonitorUpdate", back_populates="monitor",
                           cascade="all, delete-orphan", 
                           order_by="desc(MonitorUpdate.date), desc(MonitorUpdate.created_at)")


class MonitorUpdate(Base):
    """A dated development in a monitored national concern, sourced from articles."""
    __tablename__ = "monitor_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monitor_id: Mapped[int] = mapped_column(Integer, ForeignKey("national_monitors.id"), index=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), default="")
    article_title: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    monitor = relationship("NationalMonitor", back_populates="updates")


class ApiCache(Base):
    __tablename__ = "api_cache"

    tier: Mapped[str] = mapped_column(String, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String, primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(default=utcnow)


class AnalysisCache(Base):
    __tablename__ = "analysis_cache"

    prompt_version: Mapped[str] = mapped_column(String, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String, primary_key=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, default=PipelineStatus.RUNNING)
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
    # JSON list of ground-truth reference-check failures from this run
    # (see analyze/ground_truth.py). Empty/"[]" = all checks passed.
    ground_truth_failures: Mapped[str | None] = mapped_column(Text, nullable=True)


class HousePipelineRun(Base):
    """Tracks each House representative pipeline run — mirrors PipelineRun for senators."""
    __tablename__ = "house_pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, default=PipelineStatus.RUNNING)
    reps_processed: Mapped[int] = mapped_column(Integer, default=0)
    reps_total: Mapped[int] = mapped_column(Integer, default=0)
    reps_failed: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ground_truth_failures: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupplementaryPipelineRun(Base):
    """Tracks explore-document ingestion + SCOTUS justice scoring +
    president scoring as one combined run.

    Extracted from PipelineRun (2026-07): these three had no data
    dependency on Senate's own fetch/analyze work — they were only
    sequenced as extra phases inside run_senate_pipeline() because that
    was the pipeline that already existed. Genuinely independent
    domains get their own tracking row, mirroring HousePipelineRun,
    instead of piggybacking on Senate's.
    """
    __tablename__ = "supplementary_pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, default=PipelineStatus.RUNNING)
    current_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    explore_docs_ingested: Mapped[int] = mapped_column(Integer, default=0)
    justices_scored: Mapped[int] = mapped_column(Integer, default=0)
    justices_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    presidents_updated: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class StockTradesPipelineRun(Base):
    """Tracks each STOCK Act PTR ingestion run — mirrors HousePipelineRun."""
    __tablename__ = "stock_trades_pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, default=PipelineStatus.RUNNING)
    house_trades_ingested: Mapped[int] = mapped_column(Integer, default=0)
    senate_trades_ingested: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class BskySenatorSpotlight(Base):
    """Tracks which senators have been highlighted in daily Bluesky score posts."""
    __tablename__ = "bsky_senator_spotlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    posted_at: Mapped[datetime] = mapped_column(default=utcnow)
    post_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class SiteVisit(VisitsBase):
    """One row per unique visitor per day — never raw IP/PII.

    `visitor_hash` is an HMAC of (IP, User-Agent, date) keyed by a secret
    derived from ADMIN_TOKEN (see api/visits.py) — the same real visitor
    produces a different hash every day, and the raw IP is never stored or
    recoverable from the hash. The (date, visitor_hash) primary key means a
    second request from the same visitor on the same day is a no-op insert,
    so this table grows by unique visitors, not by page views.

    2026-07: lives on VisitsBase (its own SQLite file), not the shared
    Base — see database.py's _derive_visits_database_url for why this
    table (fed by the highest-frequency write in the app) needed to be
    physically isolated from the nightly pipeline's writes.
    """
    __tablename__ = "site_visits"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    visitor_hash: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Coarse buckets parsed from User-Agent (see api/visits.py _parse_ua) —
    # never the raw UA string. A handful of low-cardinality categories
    # (~5 browsers x ~6 OSes x 3 device types) isn't personally identifying
    # on its own, and reveals nothing the hash didn't already consume: the
    # full UA string is already an input to visitor_hash above.
    browser: Mapped[str] = mapped_column(String(20), default="")
    os: Mapped[str] = mapped_column(String(20), default="")
    device_type: Mapped[str] = mapped_column(String(10), default="")


class PageView(VisitsBase):
    """Per-page view counts, by day — a raw hit counter, not deduped by visitor.

    Deliberately a separate table from SiteVisit: that table's (date,
    visitor_hash) primary key exists specifically to dedupe repeat visits
    from the same person in a day, which is right for "how many unique
    people visited" but wrong for "which pages get read" — a visitor
    hitting 5 pages in one session must count as 5 page views, not 1.
    `path` is a normalized route template (e.g. "/politicians/[id]", not
    "/politicians/chuck-grassley") so the count reflects page popularity
    rather than fragmenting across every individual politician/issue id —
    see api/visits.py's _normalize_path.
    """
    __tablename__ = "page_views"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    path: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
