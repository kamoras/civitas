from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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
    and senator_service.py / representative_service.py.

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


class ActionIssueStatus(StrEnum):
    """Lifecycle of an ActionIssue's confidence, not its retirement state
    (see ActionIssue.is_current for that — orthogonal: a DEVELOPING issue
    can retire unconfirmed same as a CONFIRMED one can).

    DEVELOPING issues are drafted from a primary source (e.g. a Senate
    roll-call vote) before any press coverage exists — see
    pipeline/analyze/early_signal.py. They never post to Bluesky and rank
    below every CONFIRMED issue. A DEVELOPING row either gets promoted to
    CONFIRMED once matching press coverage appears, or is retired
    unconfirmed after its deadline — never silently deleted, same
    render-the-true-state approach as BallotMeasure.status.
    """
    DEVELOPING = "developing"
    CONFIRMED = "confirmed"


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
    # Still stored in the score_independent_voting column. Renaming the column
    # is the contract step of a two-release migration (migrations/README.md):
    # the previous image reads the old name, and Swarm's start-first update
    # plus automatic rollback can run it against this schema.
    score_constituent_alignment: Mapped[float] = mapped_column("score_independent_voting", Float, default=50.0)
    score_funding_diversity: Mapped[float] = mapped_column(Float, default=50.0)
    score_legislative_effectiveness: Mapped[float] = mapped_column(Float, default=50.0)
    # Per-dimension data-sufficiency ("high"/"medium"/"low") as JSON —
    # see score_calculator.calculate_confidence.
    score_confidence: Mapped[str] = mapped_column(Text, default="{}")

    total_raised: Mapped[float] = mapped_column(Float, default=0.0)
    # Contributions + candidate self-loans: the denominator funding shares
    # are taken over (normalize_finance.summarize_election_totals). NULL on
    # rows scored before it existed — readers fall back to total_raised.
    total_contributions: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Party the member's votes are scored against: their own, or for an
    # Independent the party they caucus with (normalize_votes). Read back by
    # the score-breakdown API so it scores Independents as the pipeline did.
    caucus_party: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # The member's party-line record over the whole current Congress, as
    # JSON (party_line_record.party_line_records): what Constituent
    # Alignment's break rate is measured on and the breaks the scorecard
    # lists. NULL until a pipeline run measures it; readers then fall back
    # to the stored votes.
    party_line_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_from_pacs: Mapped[float] = mapped_column(Float, default=0.0)
    small_donor_percentage: Mapped[float] = mapped_column(Float, default=0.0)

    partisan_depth: Mapped[str | None] = mapped_column(Text, nullable=True)

    leadership_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideology_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    bipartisanship_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Receive-only cross-party cosponsorship rate (share of cosponsors
    # attracted to own bills from the other party, cohort-median-normalized)
    # — feeds Legislative Effectiveness's bipartisan-coalition-attraction
    # component (score_calculator v6.11; Harbridge-Yong/Volden/Wiseman 2023).
    # bipartisanship_score above stays the Lugar-style give+receive blend
    # used for profile display.
    attracted_bipartisanship_score: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    financial_disclosures: Mapped[list["FinancialDisclosure"]] = relationship(back_populates="senator", cascade="all, delete-orphan")


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
    # etc.) for the donor's own committee, when this donor is one (FEC
    # committee master). Reported, not scored since v6.22. None for
    # non-committee donors or when no registration resolved.
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
    voted_with_party: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vote_category: Mapped[str] = mapped_column(String, default="key")  # "recent" or "key"
    # Which roll call ("senate-119-2-242"; normalize_votes.roll_call_ref),
    # so the vote API can show the Congress record's own question, date and
    # party tallies. NULL for votes stored before 2026-09-28.
    roll_call: Mapped[str | None] = mapped_column(String(24), nullable=True)

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
    # Senator.total_contributions above — previously only lived in
    # policy_alignment.py's transient match dict.
    is_consensus_vote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Bills this member voted on that LDA filings for clients of the donor's
    # name name, with the client (JSON list; fetch/lda.lobbied_bills_for).
    # NULL for rows written before the column existed.
    lobbied_bills: Mapped[str | None] = mapped_column(Text, nullable=True)
    # lobbying_spend by the registry's client names (JSON list of
    # {"client", "amount"}): a name beginning with the donor's can be a
    # separate company, so the total is never shown as one company's.
    lobbying_clients: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether the LDA lookup succeeded: False means lobbying_spend is
    # unknown, not zero. NULL when no lookup was attempted.
    lobbying_checked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

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
    bill_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
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
    # Volden & Wiseman's commemorative tier (a post-office naming, a Gold
    # Medal): weighted 1x in Legislative Effectiveness, not 5x. Set by the
    # pipeline (analyze/commemorative.py); stored so scoring never loads a model.
    commemorative: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))

    senator: Mapped["Senator"] = relationship(back_populates="sponsored_bills")


class StockTrade(Base):
    """STOCK Act periodic transaction report (PTR) entry — informational only,
    not part of the weighted score (see senator_service.get_senator_stock_trades
    for the rationale)."""
    __tablename__ = "stock_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)  # not all disclosed assets are tickered equities
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, default="self")  # self | spouse | joint | dependent | unknown
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)  # purchase | sale_full | sale_partial | exchange
    # NULL for a scanned row whose date isn't legible (ptr_common.ocr_extract_rows).
    transaction_date: Mapped[str | None] = mapped_column(String, nullable=True)
    disclosure_date: Mapped[str] = mapped_column(String, nullable=False)
    days_to_disclose: Mapped[int] = mapped_column(Integer, default=0)
    amount_low: Mapped[float] = mapped_column(Float, default=0.0)
    amount_high: Mapped[float] = mapped_column(Float, default=0.0)
    industry: Mapped[str] = mapped_column(String, default="UNCLASSIFIED")
    # The eFD table's Asset Type cell as printed ("Stock", "Cryptocurrency"):
    # the filer's own statement of what the asset is, which the nightly
    # industry pass reads (stock_pipeline._reclassify_stored_trades). The
    # House prints its code inside asset_name; the 278-T states none.
    asset_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str] = mapped_column(String, default="")
    filing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)  # dedupe key
    # "text" = parsed from a PDF/HTML text layer, "ocr" = OCR fallback on a
    # scanned filing. Surfaced to the frontend/API so low-confidence OCR
    # rows can be visually flagged rather than presented as equally
    # reliable — see grounding.py's precedent of never silently trusting
    # unverified extracted content.
    parse_confidence: Mapped[str] = mapped_column(String, default="text")
    # ptr_common.PARSER_VERSION that read the filing; one read by an older
    # version is read again (stock_pipeline._reread_trades). Rows stored
    # before versions existed are 1.
    parser_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

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
    # ISO date the member was sworn in to the current Congress, from the
    # House Clerk's member list (fetch/house_clerk.py). Legislative
    # Effectiveness prorates its bar for a member seated mid-Congress (a
    # special election) by the share of the Congress served (v6.23). Null
    # when the Clerk lists no date.
    sworn_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # The Congress whose district lines the stored scores were computed on
    # (fetch/district_pvi.lines_congress, recorded by upsert_representative,
    # or by the startup Constituent Alignment rescore from current_lines()).
    # The API's breakdown recomputes Constituent Alignment on these same
    # district lines (district_pvi.lines_of) — while a House run is part-way
    # through switching Congresses, after one that failed, and for a member
    # who left when the lines changed. Only the district table: the
    # Constituent Alignment reference and member ideal points it also reads
    # are the current files, which a House run rewrites before scoring, so
    # a member not yet rescored can still differ. Null: scored before this
    # was recorded, or on a pre-pinning table.
    district_lines_congress: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # Still stored in the score_independent_voting column. Renaming the column
    # is the contract step of a two-release migration (migrations/README.md):
    # the previous image reads the old name, and Swarm's start-first update
    # plus automatic rollback can run it against this schema.
    score_constituent_alignment: Mapped[float] = mapped_column("score_independent_voting", Float, default=50.0)
    score_funding_diversity: Mapped[float] = mapped_column(Float, default=50.0)
    score_legislative_effectiveness: Mapped[float] = mapped_column(Float, default=50.0)
    # Per-dimension data-sufficiency ("high"/"medium"/"low") as JSON —
    # see score_calculator.calculate_confidence.
    score_confidence: Mapped[str] = mapped_column(Text, default="{}")

    total_raised: Mapped[float] = mapped_column(Float, default=0.0)
    # Contributions + candidate self-loans: the denominator funding shares
    # are taken over (normalize_finance.summarize_election_totals). NULL on
    # rows scored before it existed — readers fall back to total_raised.
    total_contributions: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Party the member's votes are scored against: their own, or for an
    # Independent the party they caucus with (normalize_votes). Read back by
    # the score-breakdown API so it scores Independents as the pipeline did.
    caucus_party: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # The member's party-line record over the whole current Congress, as
    # JSON (party_line_record.party_line_records): what Constituent
    # Alignment's break rate is measured on and the breaks the scorecard
    # lists. NULL until a pipeline run measures it; readers then fall back
    # to the stored votes.
    party_line_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_from_pacs: Mapped[float] = mapped_column(Float, default=0.0)
    small_donor_percentage: Mapped[float] = mapped_column(Float, default=0.0)

    partisan_depth: Mapped[str | None] = mapped_column(Text, nullable=True)

    leadership_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideology_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    bipartisanship_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Receive-only cross-party cosponsorship rate (share of cosponsors
    # attracted to own bills from the other party, cohort-median-normalized)
    # — feeds Legislative Effectiveness's bipartisan-coalition-attraction
    # component (score_calculator v6.11; Harbridge-Yong/Volden/Wiseman 2023).
    # bipartisanship_score above stays the Lugar-style give+receive blend
    # used for profile display.
    attracted_bipartisanship_score: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    financial_disclosures: Mapped[list["FinancialDisclosure"]] = relationship(back_populates="representative", cascade="all, delete-orphan")


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
    # etc.) for the donor's own committee, when this donor is one (FEC
    # committee master). Reported, not scored since v6.22. None for
    # non-committee donors or when no registration resolved.
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
    voted_with_party: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vote_category: Mapped[str] = mapped_column(String, default="key")
    roll_call: Mapped[str | None] = mapped_column(String(24), nullable=True)  # see KeyVote.roll_call

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
    # Same as LobbyingMatch.lobbied_bills / lobbying_clients / lobbying_checked.
    lobbied_bills: Mapped[str | None] = mapped_column(Text, nullable=True)
    lobbying_clients: Mapped[str | None] = mapped_column(Text, nullable=True)
    lobbying_checked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

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
    bill_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
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
    # Volden & Wiseman's commemorative tier (a post-office naming, a Gold
    # Medal): weighted 1x in Legislative Effectiveness, not 5x. Set by the
    # pipeline (analyze/commemorative.py); stored so scoring never loads a model.
    commemorative: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))

    representative: Mapped["Representative"] = relationship(back_populates="sponsored_bills")


class RepStockTrade(Base):
    """STOCK Act periodic transaction report (PTR) entry — informational only,
    not part of the weighted score (see representative_service.get_rep_stock_trades
    for the rationale)."""
    __tablename__ = "rep_stock_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    representative_id: Mapped[str] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, default="self")
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    # NULL for a scanned row whose date isn't legible (ptr_common.ocr_extract_rows).
    transaction_date: Mapped[str | None] = mapped_column(String, nullable=True)
    disclosure_date: Mapped[str] = mapped_column(String, nullable=False)
    days_to_disclose: Mapped[int] = mapped_column(Integer, default=0)
    amount_low: Mapped[float] = mapped_column(Float, default=0.0)
    amount_high: Mapped[float] = mapped_column(Float, default=0.0)
    industry: Mapped[str] = mapped_column(String, default="UNCLASSIFIED")
    source_url: Mapped[str] = mapped_column(String, default="")
    filing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parse_confidence: Mapped[str] = mapped_column(String, default="text")
    # ptr_common.PARSER_VERSION that read the filing; one read by an older
    # version is read again (stock_pipeline._reread_trades). Rows stored
    # before versions existed are 1.
    parser_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    representative: Mapped["Representative"] = relationship(back_populates="stock_trades")


class FinancialDisclosure(Base):
    """A member's or the sitting president's most recent annual financial
    disclosure report — the one whose asset list (House Schedule A / Senate
    Part 3 / the 278e's Parts 2, 5 and 6) backs the holdings breakdown on
    their scorecard. Informational only, not scored.

    Exactly one of senator_id / representative_id / president_id is set. Only the latest
    report per member is kept: a report describes holdings at one year end,
    so an older one is superseded rather than accumulated.

    `parsed` is False for a report that exists but couldn't be read (a
    scanned paper filing): the scorecard then links to it instead of
    showing an empty breakdown that would read as "holds nothing".
    """
    __tablename__ = "financial_disclosures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str | None] = mapped_column(String, ForeignKey("senators.id", ondelete="CASCADE"), nullable=True, index=True)
    representative_id: Mapped[str | None] = mapped_column(String, ForeignKey("representatives.id", ondelete="CASCADE"), nullable=True, index=True)
    president_id: Mapped[str | None] = mapped_column(String, ForeignKey("presidents.id", ondelete="CASCADE"), nullable=True, index=True)
    filing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # What the report is, as the scorecard names it: "2025 annual report",
    # "2025 annual report (amended)", "new-filer report as of 2026-03-24".
    report_label: Mapped[str] = mapped_column(String, default="")
    filed_date: Mapped[str | None] = mapped_column(String, nullable=True)
    # The date the holdings describe (YYYY-MM-DD): the year end for an
    # annual report, the stated date for a Senate new-filer report, NULL
    # for a Senate paper filing (it states none). With `amended`, `seq`,
    # filed_date and filing_id it is the report's rank among the member's
    # filings (holdings_pipeline._rank), kept so a later run can compare
    # against it even when the stored filing is missing from that run's
    # index.
    as_of_date: Mapped[str | None] = mapped_column(String, nullable=True)
    amended: Mapped[bool] = mapped_column(Boolean, default=False)
    # The House document id (ordering amendments before the filing date,
    # which the index can inherit from the original) or the Senate title's
    # amendment number (breaking a same-day tie only) — see
    # holdings_pipeline._rank.
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # The newest Senate filing made on or after this report's filing date
    # whose as-of date can't be known — a paper filing (its page is page
    # images) or one whose title states no year — and so can't be ranked
    # against a dated report. It is
    # named here instead, as filed ("annual report filed 2026-05-14"), so
    # the scorecard can say a later filing exists rather than imply this one
    # is the latest.
    later_filing_label: Mapped[str | None] = mapped_column(String, nullable=True)
    later_filing_url: Mapped[str | None] = mapped_column(String, nullable=True)
    later_filing_filed: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str] = mapped_column(String, default="")
    parsed: Mapped[bool] = mapped_column(Boolean, default=True)
    # When not parsed: "scanned" (paper filing) or "unrecognized"
    # (electronic, in a layout the parser can't read) — the page words its
    # note differently for each.
    unreadable_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # The fetch module's PARSER_VERSION that read this report; a newer one
    # re-reads it (holdings_pipeline._is_current).
    parser_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(default=utcnow)

    senator: Mapped["Senator"] = relationship(back_populates="financial_disclosures")
    representative: Mapped["Representative"] = relationship(back_populates="financial_disclosures")
    president: Mapped["President"] = relationship(back_populates="financial_disclosures")
    holdings: Mapped[list["FinancialHolding"]] = relationship(back_populates="disclosure", cascade="all, delete-orphan")


class FinancialHolding(Base):
    """One asset from a FinancialDisclosure. Values are the disclosed
    *bracket*, never an exact figure — see fetch/fd_common.HoldingRow for
    the encoding (NULL = no bracket stated; low == high = open-ended top
    bracket)."""
    __tablename__ = "financial_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_disclosures.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    account: Mapped[str | None] = mapped_column(String, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_type: Mapped[str] = mapped_column(String, default="")  # as filed: House code or Senate label
    category: Mapped[str] = mapped_column(String, default="OTHER", index=True)  # config_definitions.HOLDING_CATEGORIES
    owner: Mapped[str] = mapped_column(String, default="self")  # self | spouse | joint | dependent | unknown
    value_text: Mapped[str] = mapped_column(String, default="")
    value_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    disclosure: Mapped["FinancialDisclosure"] = relationship(back_populates="holdings")


class President(Base):
    __tablename__ = "presidents"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "obama-44"
    name: Mapped[str] = mapped_column(String, nullable=False)
    party: Mapped[str] = mapped_column(String, nullable=False)  # D, R, W(hig), F(ederalist), DR, U (no party)
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # 44th, 45th, etc.
    term_start: Mapped[str] = mapped_column(String, nullable=False)  # "2009-01-20"
    term_end: Mapped[str | None] = mapped_column(String, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    # Nullable, no default (2026-07): these used to default to 0.0 and get
    # filled from a hand-set seed value for any president without live
    # data. Both are gone — a hand-set number presented as a computed
    # score undermined this platform's core promise (see president_
    # service.py's module docstring for the full account), and 0.0 was
    # actively misleading as a "no data yet" placeholder (it reads as the
    # worst possible score, not "unknown"). NULL means "not computed for
    # this president" — compute_president_overall_score renormalizes the
    # weighted sum over whichever dimensions are actually present per
    # president, the same pattern score_calculator.py already uses when a
    # senator/rep is missing a signal (e.g. Coalition Breadth's
    # breadth_weight=0). A dimension is only ever NULL when it is
    # genuinely inapplicable for that president (e.g. Public Mandate for
    # the five who never won a presidential election) or a fetch hasn't
    # completed yet — never as a stand-in for a real number.
    score_public_mandate: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_effectiveness: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Agency Alignment, removed in president v7: no longer scored and
    # cleared each run; dropped in a later release (migrations/README.md).
    score_agency_alignment: Mapped[float | None] = mapped_column(Float, nullable=True)
    # NULL for any currently-serving or just-departed president — C-SPAN's
    # Presidential Historians Survey only rates a completed term, and its
    # 2025 cycle was postponed entirely (see app.pipeline.fetch.
    # cspan_historians_survey). Genuinely unrated, not unmeasured.
    score_historical_legacy: Mapped[float | None] = mapped_column(Float, nullable=True)

    avg_approval: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Average approval among the president's own party, the other party and
    # independents (Gallup, via the American Presidency Project), and the
    # House party distance averaged over the term (Voteview): what Public
    # Mandate compares within an era since president v9
    # (president_scorer.approval_vs_era). NULL without the breakdown.
    approval_own_party: Mapped[float | None] = mapped_column(Float, nullable=True)
    approval_other_party: Mapped[float | None] = mapped_column(Float, nullable=True)
    approval_independents: Mapped[float | None] = mapped_column(Float, nullable=True)
    term_polarization: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Average election-margin percentage across a president's own election
    # win(s) — the pre-polling-era (pre-Truman) Public Mandate proxy, see
    # app.pipeline.fetch.presidential_elections. NULL for the five
    # presidents who never won a presidential election in their own right.
    election_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    gdp_growth_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Average annual real GDP growth per person over the years Effectiveness
    # credits, the 13 peer economies' median over the same years, and US
    # minus peers with the peers' catch-up growth set aside
    # (app.pipeline.fetch.peer_gdp.peer_relative_growth) — what a postwar
    # term's GDP component scores since president v8. NULL before 1947 (no
    # peer series covers those terms) and until the first run that fetches
    # them.
    gdp_growth_per_person: Mapped[float | None] = mapped_column(Float, nullable=True)
    gdp_growth_peer_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    gdp_growth_relative: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The unemployment rate the year the term began and its change to the
    # last credited year, consumer-price inflation that year and its average
    # over the credited years, and how many credited years they cover
    # (president_scorer.macro_window; FRED/BLS). Since president v10 a term
    # from 1947 on is judged on each against what its starting rate
    # predicts. NULL before 1947 and until measured.
    unemployment_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    unemployment_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    inflation_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    inflation_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    economy_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jobs_created_millions: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Informational only (2026-07): no longer a scoring input — Competence
    # (the dimension EO count used to feed) was removed entirely, see
    # PRESIDENT_SCORE_WEIGHTS's comment in config_definitions.py. Still
    # shown on a president's profile as a raw stat.
    eo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Agency Alignment's inputs (Federal Register rulemaking counts), no
    # longer fetched or read since president v7; dropped in a later release
    # (migrations/README.md).
    rulemaking_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rulemaking_finalized_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Last-quartile-minus-first-quartile average approval across the term
    # (see calc_public_mandate) — persisted for the same on-demand
    # score-breakdown-recompute reason as rulemaking_finalized_pct above.
    approval_trend: Mapped[float | None] = mapped_column(Float, nullable=True)
    # First-quartile average approval: where the term started. The trend
    # is judged against what presidents starting at that level went on to
    # do (president_scorer.fit_trend_on_start), since a president who
    # starts high has far further to fall.
    approval_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Raw C-SPAN 2021 Presidential Historians Survey point total (e.g.
    # Lincoln=897) — persisted alongside the normalized score_
    # historical_legacy for the same on-demand-recompute reason as
    # rulemaking_finalized_pct above.
    historical_legacy_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Same figure as avg_approval, but averaged only over the last 90 days
    # of polling rather than the full term — a rolling "how is this
    # changing lately" view, most meaningful for the currently-serving
    # president. NULL, not stale, once a president leaves office and no
    # new polls exist to populate the window. (A by-party version of this
    # — a "partisan approval gap" — was deliberately not built: the
    # number can't be attributed to the president's own conduct vs.
    # opposition messaging/media environment, so placing it on a
    # president's own page would imply a causal claim the data can't
    # support, however it's labeled — see presidential_approval.py's
    # module docstring for the full account.)
    recent_avg_approval: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    trades: Mapped[list["PresidentTrade"]] = relationship(
        back_populates="president", cascade="all, delete-orphan"
    )
    financial_disclosures: Mapped[list["FinancialDisclosure"]] = relationship(
        back_populates="president", cascade="all, delete-orphan"
    )


class PresidentTrade(Base):
    """One disclosed buy/sell/exchange from a president's OGE Form 278-T
    periodic transaction report.

    Deliberately the same shape as StockTrade/RepStockTrade: the STOCK Act
    imposes the same reporting duty on the President as on members of
    Congress (5 U.S.C. §13103; the executive-branch form is OGE 278-T
    rather than the House/Senate PTR, but the reported fields — asset,
    owner, transaction type, transaction date, notification date, amount
    *range* — are the same), so the same columns, the same 45-day
    timeliness math, and the same parser in ptr_common.py all apply
    unchanged.

    What this table deliberately does NOT have is any profit/gain column.
    278-T reports an amount *bracket* per transaction and no cost basis,
    share count, or realized gain anywhere on the form, so a P&L figure
    could only ever be an estimate this platform invented — exactly the
    class of number president_service.py's docstring documents removing
    (hand-set values presented as computed ones). Volumes are shown as the
    disclosed range and nothing is derived from them.
    """
    __tablename__ = "president_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    president_id: Mapped[str] = mapped_column(String, ForeignKey("presidents.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)  # crypto and bond lines carry none
    asset_name: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, default="self")  # self | spouse | joint | dependent | unknown
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)  # purchase | sale_full | sale_partial | exchange
    # NULL for a scanned row whose date isn't legible (ptr_common.
    # ocr_extract_rows, keep_undated): its filing's date is still known.
    transaction_date: Mapped[str | None] = mapped_column(String, nullable=True)
    disclosure_date: Mapped[str] = mapped_column(String, nullable=False)
    days_to_disclose: Mapped[int] = mapped_column(Integer, default=0)
    amount_low: Mapped[float] = mapped_column(Float, default=0.0)
    amount_high: Mapped[float] = mapped_column(Float, default=0.0)
    industry: Mapped[str] = mapped_column(String, default="UNCLASSIFIED")
    source_url: Mapped[str] = mapped_column(String, default="")
    filing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)  # dedupe key
    parse_confidence: Mapped[str] = mapped_column(String, default="text")
    # ptr_common.PARSER_VERSION that read the filing; one read by an older
    # version is read again (stock_pipeline._reread_trades). Rows stored
    # before versions existed are 1.
    parser_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # "periodic": from a 278-T. "annual": from the annual report's Part 7
    # (president_fd), the record for its year, which replaces that year's
    # periodic rows and states no notification date.
    report_kind: Mapped[str] = mapped_column(String(8), default="periodic", server_default="periodic")

    president: Mapped["President"] = relationship(back_populates="trades")


class Race(Base):
    """A single federal race for a given election cycle (one Senate seat in
    one state, or one House seat in one district).

    id is a human-readable composite (e.g. "2026-SEN-GA",
    "2026-HOUSE-CA-12") rather than an autoincrement int — races are
    naturally keyed by (cycle, office, state, district) and a readable id
    makes admin/debug queries legible without a join, matching President's
    id convention ("obama-44") rather than Donor/IndustryDonation's
    autoincrement-int convention (those are pure child rows with no
    natural external identity).

    PVI (competitiveness) is deliberately NOT a column here — it's read
    live from app/data/state_pvi.json / district_pvi.json via the same
    accessors score_calculator.py already uses (_state_pvi/_district_pvi),
    so there is exactly one source for that number, not a second copy that
    can drift out of sync with the scoring system's own view of it.
    """
    __tablename__ = "races"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # FEC's own office codes ("S"=Senate, "H"=House) — reused as-is rather
    # than translated to a new vocabulary, since every candidate row already
    # carries this code from the FEC API and translating it would just be
    # another place for the two to drift.
    office: Mapped[str] = mapped_column(String(1), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    # None for Senate races. 0 = at-large House district (matches FEC's own
    # "00" convention for single-district states, stored as int).
    district: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_special: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="race", cascade="all, delete-orphan",
    )
    coverage_items: Mapped[list["RaceCoverageItem"]] = relationship(
        back_populates="race", cascade="all, delete-orphan",
    )
    result: Mapped["RaceResult | None"] = relationship(
        back_populates="race", cascade="all, delete-orphan", uselist=False,
    )
    result_events: Mapped[list["ElectionResultEvent"]] = relationship(
        back_populates="race", cascade="all, delete-orphan",
    )


# Prefix of a Candidate id that is NOT an FEC candidate_id. FEC ids are a
# letter and digits ("H6MO01222"), so a colon can never collide with one.
BALLOT_ONLY_ID_PREFIX = "ballot:"


class Candidate(Base):
    """A declared candidate in a Race, sourced from FEC candidate filings.

    id reuses the FEC's own candidate_id (e.g. "S6ID00146") as the primary
    key — the same "reuse the source's own identifier" convention Senator/
    Representative/President already follow with bioguide_id/UCSB-derived
    ids, so this candidate's FEC record can always be looked up directly
    without a separate crosswalk table.

    The one exception is a candidate a state's own source puts on the
    ballot who never filed with the FEC (a minor-party or independent
    candidate under the FEC's reporting threshold, typically). They have
    no FEC id, so their id is BALLOT_ONLY_ID_PREFIX + race + name, and
    `fec_filed` is False. Without them a certified ballot page showed
    fewer people than the real ballot — 7 of Louisiana's 41 in 2026.
    """
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    race_id: Mapped[str] = mapped_column(String, ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    party: Mapped[str] = mapped_column(String, nullable=False)  # FEC party code: DEM, REP, IND, etc.
    # FEC incumbent_challenge code: "I"=Incumbent, "C"=Challenger, "O"=Open seat.
    incumbent_challenge: Mapped[str | None] = mapped_column(String(1), nullable=True)
    has_raised_funds: Mapped[bool] = mapped_column(Boolean, default=False)
    # FEC candidate_status code: "C"=statutory candidate this cycle,
    # "F"=future-cycle filer, "N"=not yet statutory, "P"=prior-cycle.
    # Stored raw (source data, not a classification decision) — the API/
    # frontend use it to separate active candidates from paper filers.
    candidate_status: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # True only when a registered state source (state_candidate_sources.json
    # / state_candidates.py — every state with an entry there) has confirmed this
    # candidate is actually on the general-election ballot, not just an
    # FEC filer. Only ever set True by a real match; never set False to
    # mean "lost" — for a state/race with no coverage yet, or a candidate
    # a source's data doesn't mention, this simply stays False, same
    # never-fabricate discipline as cash_on_hand/last_financials_sync.
    confirmed_general: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when a registered state source lists this candidate on a PRIMARY
    # ballot — the same never-fabricate discipline as confirmed_general, and
    # the answer for the months BEFORE a primary, when no results exist yet
    # and an FEC filer list is all there otherwise is. Weaker than
    # confirmed_general and never a substitute for it: being on the primary
    # ballot says nothing about surviving it, so once a state confirms
    # nominees, those win (see _confirmed_or_all).
    on_primary_ballot: Mapped[bool] = mapped_column(Boolean, default=False)
    # The name as the state prints it on its ballot ("Jane Doe"), from
    # whichever state source last matched this candidate. `name` stays the
    # FEC's own ("COOPER, ROY") — it is what the roster sync keys on and
    # what every non-ballot page shows. Null until a state source names
    # them, and never set from a "Last, First" printing (see
    # state_candidates._note_ballot_name).
    ballot_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # last_coverage_search (the removed Bluesky candidate search's watermark)
    # is no longer mapped; the next release drops it (migrations/README.md).

    # Fundraising totals from FEC's /candidate/{id}/totals/ endpoint — NULL
    # until this candidate's financial-refresh turn comes up (see
    # election_pipeline.py's prioritized/watermarked refresh), never a
    # fabricated 0.0 standing in for "not fetched yet" (same "NULL means
    # not computed, not zero" rule President's score columns follow).
    contributions: Mapped[float | None] = mapped_column(Float, nullable=True)
    disbursements: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_on_hand: Mapped[float | None] = mapped_column(Float, nullable=True)
    individual_itemized_contributions: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_financials_sync: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    race: Mapped["Race"] = relationship(back_populates="candidates")

    @property
    def fec_filed(self) -> bool:
        """False for a ballot-only candidate: there is no FEC record to
        link to, fetch totals for, or describe as "awaiting FEC sync"."""
        return not self.id.startswith(BALLOT_ONLY_ID_PREFIX)


class RaceCoverageItem(Base):
    """One news article or Bluesky post matched to a Race by candidate-name
    string match (see fetch/election_coverage.py) — deliberately not an
    LLM-summarized entry. title/summary/author are stored verbatim from the
    source (RSS description or Bluesky post text), never model-generated,
    so this table carries zero hallucination surface; the frontend renders
    these fields as-is with a link back to the source.
    """
    __tablename__ = "race_coverage_items"
    # DB-level dedup guard: the ingestion path also checks before insert,
    # but concurrent refreshes (15-min election-season job vs. nightly
    # pipeline) could otherwise both pass the check-then-insert race.
    __table_args__ = (UniqueConstraint("race_id", "url", name="uq_race_coverage_race_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[str] = mapped_column(String, ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "news" or "bluesky"
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String, nullable=True)  # Bluesky handle, if source_type="bluesky"
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Which candidate's name matched this item, and on what evidence:
    # "full_name" (surname + first name both present in the text) or
    # "surname_context" (surname + state-name corroboration). Only
    # full_name-matched items are eligible to be published as posts
    # (election_bluesky.py) — the weaker basis is display-only.
    # The state-name corroboration is worthless when the OUTLET is that
    # state's own newsroom, which names the state in nearly every
    # article; those matches must also clear the relevance bar
    # (election_coverage._corroboration_is_vacuous).
    matched_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    match_basis: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # NULL = not yet considered for a Civitas post about this race (the
    # feed and Bluesky; see analyze/election_bluesky.py) — most coverage items never get
    # posted at all (only a capped, prioritized subset per run), so this
    # also marks "already considered this run, don't re-evaluate" for the
    # ones that were skipped, not just the ones that were actually posted.
    bsky_posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # True only if a post was actually published (bsky_posted_at alone
    # means "considered") — the daily posting budget counts these.
    # "Published" is into the feed (BroadcastPost); whether Bluesky also
    # took it is that row's bsky_status.
    bsky_posted: Mapped[bool] = mapped_column(Boolean, default=False)
    # How much this item is ABOUT its matched race, 0..1 cosine in the
    # similarity-embedding space (analyze/race_relevance.py). Computed at
    # INGEST, not per request: the API cannot afford to embed a feed on
    # every page load. NULL means never scored, which the feed treats as
    # not-displayable — fail closed, and the next ingest fills it in.
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # Whether the source text tells a reader how to vote. Relevance and
    # suitability are different questions and a social feed needs both
    # asked: 31.5% of Bluesky items clear the relevance bar and 7% of
    # those are campaign advocacy, which a non-partisan platform must not
    # carry even attributed.
    has_advocacy: Mapped[bool] = mapped_column(Boolean, default=False)

    race: Mapped["Race"] = relationship(back_populates="coverage_items")


class RaceResult(Base):
    """The count a state's own election-night reporting system shows for
    one Race, as of the last read (live_results/sync.py).

    Every figure is the source's, copied — nothing here is a projection
    or a call. `official` is True only where the source itself says its
    count is official; a race is otherwise only ever *leading*, however
    far ahead, because no feed this reads says how many ballots are still
    uncounted, and "all precincts reporting" is not that (Colorado's
    counties all report on election night and keep counting for days).
    """
    __tablename__ = "race_results"

    race_id: Mapped[str] = mapped_column(
        String, ForeignKey("races.id", ondelete="CASCADE"), primary_key=True,
    )
    # ISO date of the election these are results of; the race's cycle
    # alone would not separate a November count from a later runoff's.
    election_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    # The page a reader can check the count on, not the API it came from.
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # JSON list, most votes first: {name, party, votes, candidateId}.
    # `name` is the source's printing; candidateId is set only for a
    # unique match to one of the race's Candidate rows.
    tallies: Mapped[str] = mapped_column(Text, default="[]")
    votes_counted: Mapped[int] = mapped_column(Integer, default=0)
    # What the source counts reporting in (precincts, or counties where a
    # state aggregates by county) — NULL where it states no such figure.
    reporting_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_label: Mapped[str] = mapped_column(String(20), default="precincts")
    official: Mapped[bool] = mapped_column(Boolean, default=False)
    # The party (as a partyGroup: DEM/REP/IND/...) that held this seat
    # going into the election, fixed at the first read so a member-table
    # refresh mid-count cannot redraw what a "flip" is measured against.
    # NULL when no single party held it knowably (an open Senate seat in a
    # state whose two senators differ).
    held_by_party: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # The Action Center DEVELOPING issue this race's flip opened, if any.
    developing_issue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether a change of party is announced for this count as it stands:
    # set by apply_count when it raises FLIP, cleared when it raises
    # FLIP_REVERSED, the same state the issue and the posts keep. What the
    # results page marks as a flip — not sync.is_flip, which also asks
    # whether enough of the count is in right now and so can lapse (a poll
    # dropping its reporting figures, units added, an official flag
    # switched off) with the same challenger ahead, or turn true by the
    # clock (COUNTY_FLIP_SETTLE) before the sync has said anything. NULL
    # (never announced) reads as False.
    flip_announced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The source's own update time and version for the count stored here;
    # a later read claiming an OLDER one is a rolled-back feed and refused.
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # When votes were first counted (the row's creation until then): the
    # clock a county-unit flip waits on (sync.COUNTY_FLIP_SETTLE).
    first_reported_at: Mapped[datetime] = mapped_column(default=utcnow)
    # When the vote totals last moved. The results page stays up until a
    # grace period after the latest of these (election_phase.py).
    last_change_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)

    race: Mapped["Race"] = relationship(back_populates="result")


class ElectionResultEvent(Base):
    """One thing that happened in a race's count — first returns, a change
    of leader, every unit reporting, the source calling its count official.
    `detail` is structured data (JSON), not prose: the page words it from a
    template, so no generated sentence ever describes a result."""
    __tablename__ = "election_result_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[str] = mapped_column(
        String, ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    election_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    # Same pair as RaceCoverageItem's: considered for a Bluesky post
    # (live_results/bluesky.py), and actually published — the
    # hourly and election-wide budgets count only the second.
    bsky_posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    bsky_posted: Mapped[bool] = mapped_column(Boolean, default=False)

    race: Mapped["Race"] = relationship(back_populates="result_events")


class LiveResultRead(Base):
    """How the last read of a state's live-results feed went, per election:
    what lets the page tell "the count hasn't started" from "Civitas
    couldn't read the state's feed" — the same null-is-not-zero rule
    MeasureCoverage applies to ballot measures. One row per state, updated
    every sync pass (live_results/sync.py)."""
    __tablename__ = "live_result_reads"

    state: Mapped[str] = mapped_column(String(2), primary_key=True)
    election_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    # ok / polls_open / untrusted / unavailable / stale / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    # The last read that was stored — NULL until one is.
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # For a refused read: which kind of refusal (sync.refusal_kind), so an
    # alert fires on the same refusal twice in a row, not on two different
    # one-off ones.
    reason_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Justice(Base):
    """Supreme Court justice: the voting record from Oyez, and loyalty to
    the appointing president, the score (pipeline/analyze/justice_loyalty)."""
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

    # score_consistency, score_independence, score_bipartisan_agreement and
    # score_judicial_restraint (unscored since justice v2 and v6.13) are no
    # longer mapped; 0020 made them nullable and the next release drops them
    # (migrations/README.md).

    # Loyalty to the appointing president (justice_loyalty): the score, the
    # shrunk effect (a share: 0.145 is 14.5 points) and its standard error,
    # the votes under the appointing president and under others with the
    # share of each for the government, and the Supreme Court Database term
    # the record runs through. NULL until measured, or for a justice the
    # Database doesn't cover yet.
    score_loyalty: Mapped[float | None] = mapped_column(Float, nullable=True)
    loyalty: Mapped[float | None] = mapped_column(Float, nullable=True)
    loyalty_se: Mapped[float | None] = mapped_column(Float, nullable=True)
    loyalty_votes_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loyalty_votes_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loyalty_rate_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    loyalty_rate_out: Mapped[float | None] = mapped_column(Float, nullable=True)
    loyalty_through_term: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Martin-Quinn position per term, [[term, position], ...] as JSON: shown,
    # not scored.
    ideal_points: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    """Per-case vote record for a justice.

    One row per (justice, case): a 2026-08 audit found upsert_justice's
    delete-then-recreate had no protection against a single Oyez case
    carrying more than one `decisions` entry (a real Oyez data shape —
    e.g. Moyle v. United States has both a "dismissal - improvidently
    granted" decision and a separate "per curiam" decision for the same
    docket) — justice_votes.fetch_case_votes flattened every decision's
    votes into one list, so an affected case wrote 2 rows per justice,
    sometimes with contradictory vote values. See fetch_case_votes'
    docstring for which decision it now keeps.
    """
    __tablename__ = "justice_votes"
    __table_args__ = (
        UniqueConstraint("justice_id", "case_id", name="uq_justice_vote_justice_case"),
    )

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
    memoranda, and other official actions.

    Each document feeds three search structures, all rebuilt from this table:
    a sentence-transformer embedding in `vec_explore` (sqlite-vec, see
    `pipeline/vector_store.py`), a BM25F inverted index in `explore_fts`
    (SQLite FTS5, see `pipeline/lexical_index.py`), and the citation graph
    behind `authority` (see `pipeline/analyze/document_authority.py`). The
    row is the source of truth; all three can be dropped and rebuilt from it.
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

    # When the full text behind `url` was successfully fetched, for the
    # sources whose listing endpoint yields only an abstract (Federal
    # Register rulemaking, presidential actions). The backfill selects on
    # the *shape* of the body — short, or starting "Document Headings" —
    # and that is a proxy a genuinely short document never stops matching:
    # 476 complete documents were re-fetched every night, converging on
    # nothing. This records the fact the proxy cannot: that the fetch
    # already happened. NULL means never fetched or the fetch failed, so
    # a real failure still retries; a published document is immutable, so
    # a success never needs repeating.
    body_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Canonical identifiers this document can be cited BY — its FR citation
    # ("89 FR 12345"), FR document number, executive order number, RINs.
    # JSON list of namespaced strings; see document_authority.declared_identifiers,
    # which also derives what it can from external_id/title so rows written
    # before this column existed still take part in the citation graph.
    identifiers: Mapped[str] = mapped_column(Text, default="[]")
    # PageRank over that graph, and the raw inbound-citation count it came
    # from. Both are pipeline outputs, recomputed nightly; `cited_by_count`
    # is also what the search ranker uses to decide whether a document is
    # eligible for the authority signal at all.
    authority: Mapped[float] = mapped_column(Float, default=0.0)
    cited_by_count: Mapped[int] = mapped_column(Integer, default=0)

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
    # Source name per fact, aligned with `facts` by index. JSON "[]"
    # when an issue predates the claim layer, so the API can render
    # attribution where it exists without a migration backfilling
    # guesses.
    fact_sources: Mapped[str] = mapped_column(Text, default="[]")
    # The article each fact was quoted from, aligned with `facts` (so a
    # reader can open it), and the outlet and article of the summary, which
    # is a quoted claim too. NULL / "[]" where an issue predates them.
    fact_source_urls: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")
    summary_source: Mapped[str | None] = mapped_column(String, nullable=True)
    summary_source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    related_monitor_slugs: Mapped[str] = mapped_column(Text, default="[]")
    # Unused: the "This concerns me / Not a priority" vote that counted into
    # these was removed in 2026-09. Still mapped, with their default, because
    # they are NOT NULL and the image before this one still reads them; the
    # drop is listed under "Pending contract" in backend/migrations/README.md.
    concerned_count: Mapped[int] = mapped_column(Integer, default=0)
    not_priority_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    full_story: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    bsky_posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    bsky_posted_rank: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Text of the most recent post published for this issue (the feed, and
    # Bluesky when configured — the bsky_* names predate the feed). Used to
    # suppress near-duplicate reposts when a topic gets fresh coverage whose
    # post would say essentially the same thing as the last one.
    bsky_last_post_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # `facts` as of the last time this issue was handed to the poster.
    # The repost gate needs "what have we already told readers", and `facts`
    # itself can't answer that: it is overwritten on every hourly refresh
    # whether or not anything was posted, so a development that surfaced on a
    # non-posting run would be silently absorbed into the baseline and never
    # read as new again (see _apply_matched_issue_update).
    bsky_posted_facts: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # `facts` as of the last time it genuinely CHANGED (see
    # _apply_matched_issue_update) — not on every hourly touch the way
    # bsky_posted_facts's docstring above warns against for that column.
    # This is a separate baseline, deliberately not reused from
    # bsky_posted_facts: that one only advances when the repost gate runs
    # (gated on the poster's own near-duplicate suppression logic, not on
    # whether the reader-facing content changed at all), so it isn't a
    # reliable "what did this issue say last time a reader would have seen
    # it" signal. Read by app/fact_diff.py to mark newly-added facts.
    previous_facts: Mapped[str] = mapped_column(Text, default="[]")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    # The row this one is a near-identical duplicate of, among the newest
    # rows the homepage feed reads (action_center.mark_recent_duplicates);
    # None for a representative, or a row never compared.
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    primary_article_date: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)
    # Only ever set from a source article whose feed explicitly granted
    # redistribution rights (see pipeline/fetch/news_feeds.py's
    # _rights_cleared_image) — null for every other issue, not a generic
    # "any image we found" field.
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # The source's own photo caption — real accessible alt text pulled from
    # the feed, not a generic/empty fallback. "" (not null) when the source
    # supplied no caption for an otherwise rights-cleared image.
    image_alt: Mapped[str] = mapped_column(Text, default="")
    # Photographer/wire-service credit shown alongside the image.
    image_credit: Mapped[str] = mapped_column(Text, default="")

    # Early-signal reporting (see pipeline/analyze/early_signal.py). Default
    # "confirmed" means every ordinary news-derived issue (and every
    # pre-existing row) needs no backfill — only a primary-source-drafted
    # issue is ever created as "developing".
    status: Mapped[str] = mapped_column(String(20), default=ActionIssueStatus.CONFIRMED)
    # e.g. "senate_roll_call_vote"/"house_roll_call_vote"; null for ordinary
    # news-derived issues. Distinct from `status` so later source types
    # (FEC filings, etc.) reuse this same column rather than adding a new
    # one each time.
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    primary_source_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Set only at DEVELOPING creation. Past this with no corroborating match,
    # _expire_stale_developing_issues retires the row unconfirmed — never
    # deletes it (see ActionIssueStatus docstring).
    confirmation_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # A seat-flip issue (source_type "election_results"): when the count
    # its facts quote was read, and whether the state called that count
    # official — stamped by live_results/signals.py in the same write as
    # the facts, so the page never pairs one poll's figures with another
    # poll's time or flag. NULL for every other issue.
    count_as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    count_official: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    # Set only by _promote_developing_issue. confirmed_at - created_at is
    # the real per-row lead time (how long the signal ran before press
    # coverage matched it) — durable, queryable success-metric data, not
    # just an aggregate action_metrics bucket.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)


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
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # per source: donor_classifier_ai._CONFIDENCE_MAP
    source: Mapped[str] = mapped_column(String, nullable=False)  # "fec", "semantic", "embedding", "embedding_correction", "nn"
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)  # embedding model that produced this
    match_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: top scores, matched anchors
    learned_at: Mapped[datetime] = mapped_column(default=utcnow)


class LlmGenerationSample(Base):
    """One LLM generation attempt for a narrow, high-volume task, captured
    as training data for eventually fine-tuning a small local model to be
    a domain expert at that task (2026-08 research: the local 1.2B model's
    dominant failure on Action Center issue generation is a behavior/style
    problem — hedging, editorializing, ungrounded "former"-status claims —
    not a knowledge gap, which is exactly the class of problem a modest
    LoRA fine-tune on a few hundred real examples can fix, cheaply, with
    no change to inference cost on the Pi).

    Every attempt is recorded, not just failures: a first-try pass is a
    positive example, and a rejected-then-corrected pair is the single
    most valuable training signal this pipeline produces, since the
    mechanical grounding/role checks already work as a free, automatic
    labeling function. Before this table existed, every one of these
    triples was computed fresh on every pipeline run and thrown away —
    only ever `logger.warning`'d, never persisted (see
    action_center.py's _retry_until_grounded).
    """
    __tablename__ = "llm_generation_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # "action_center_issue"
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = first try, 2/3 = retries
    input_text: Mapped[str] = mapped_column(Text, nullable=False)  # the user_prompt actually sent
    output_json: Mapped[str] = mapped_column(Text, nullable=False)  # {"title","summary","facts"} as generated
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Why this attempt was rejected — null when passed. Free-text reasons
    # from the same mechanical checks the pipeline already gates on
    # (hedge_and_editorializing_violations, ungrounded_former_official_
    # claims, _check_summary_roles), not a separate judgment call.
    violations: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


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
    committee_leadership_refreshed: Mapped[bool] = mapped_column(Boolean, default=False)
    committee_leadership_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    district_pvi_refreshed: Mapped[bool] = mapped_column(Boolean, default=False)
    district_pvi_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
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
    president_trades_ingested: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class ElectionPipelineRun(Base):
    """Tracks each election-cycle candidate roster + financials + coverage
    ingestion run — mirrors HousePipelineRun/SupplementaryPipelineRun.
    Independent pipeline (app.pipeline.election_pipeline): no data
    dependency on Senate/House/President's own runs, same reasoning as
    SupplementaryPipelineRun's own docstring.
    """
    __tablename__ = "election_pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String, default=PipelineStatus.RUNNING)
    current_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    candidates_synced: Mapped[int] = mapped_column(Integer, default=0)
    financials_refreshed: Mapped[int] = mapped_column(Integer, default=0)
    coverage_items_ingested: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class BallotMeasure(Base):
    """One statewide ballot measure, as published by the state's own
    office (see pipeline/fetch/ballot_measures_pdf.py and the per-state
    readers it dispatches to).

    Keyed on ELECTION DATE, not cycle year. Ohio can run an "Issue 1" in a
    May primary and a different "Issue 1" in the November general; a
    cycle-year key collides them and the later sync silently overwrites
    the earlier measure's text. The same reasoning rules the ballot NUMBER
    out of the primary key: numbers are assigned late and get reassigned,
    so `id` is the upstream source's own stable identifier (the same
    "reuse the source's identifier" convention Candidate follows with
    FEC's candidate_id) and `number` is an ordinary mutable column.

    Every text column here is stored VERBATIM from its source and is
    never model-generated — same contract as RaceCoverageItem, and for
    the same reason: this is the one surface on the platform where a
    fabricated sentence could change how somebody votes. There is
    deliberately no `plain_summary`-style column, and adding one is
    ruled out rather than deferred — see AGENTS.md, Core Design
    Principle 7 ("Ballot content is quoted, never generated").
    """
    __tablename__ = "ballot_measures"
    __table_args__ = (
        # Partial, not a plain UniqueConstraint: `number` defaults to ""
        # whenever a source doesn't publish one (North Carolina, South
        # Carolina and Kentucky print none), and a
        # state routinely has more than one such measure at once early in
        # a cycle. A non-partial constraint on (state, election_date,
        # number) collides on the second blank-numbered measure and the
        # per-measure try/except in _sync_ballot_measures silently drops
        # it — reproduced against this exact schema. Scoping the index to
        # number != '' keeps the real guarantee (two DIFFERENT source ids
        # never claim the same printed ballot number) without punishing
        # the common case of a not-yet-numbered measure.
        Index(
            "uq_ballot_measure_state_date_number",
            "state", "election_date", "number",
            unique=True,
            sqlite_where=text("number != ''"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    # ISO date string ("2026-11-03") rather than a Date column, matching
    # ScoreSnapshot.date's convention — these are compared and grouped as
    # strings everywhere they're used, never date-arithmetic'd.
    election_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    election_type: Mapped[str] = mapped_column(String(16), nullable=False, default="general")
    # Ballot number as printed ("Proposition 50", "Issue 1"). Mutable.
    number: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    measure_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # certified | removed | withdrawn | under_appeal. A measure pulled from
    # the ballot is rendered AS removed, never silently deleted — a voter
    # who saw it last week needs to be told it's gone, and a bare absence
    # can't say that. `under_appeal` exists because a flat "removed"
    # during a pending appeal is itself a false statement.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="certified")

    official_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lifted verbatim from the state's own yes/no framing where it
    # publishes one. NULL means "this source publishes no such framing" —
    # never inferred, because the inference that looks easiest (yes =
    # enact) is exactly backwards on a veto referendum, where "approved"
    # RETAINS the law under challenge.
    yes_means: Mapped[str | None] = mapped_column(Text, nullable=True)
    no_means: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who wrote the title/fiscal note. Not decoration: ballot titles are
    # among the most litigated documents in election law precisely because
    # they are contested, so reprinting one unattributed under a
    # non-partisan masthead launders its author's framing. Disclosing the
    # author is MORE neutral than the bare quote.
    title_authority: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fiscal_authority: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # The county election office whose republication of the state's own
    # document this was read from, when the state's site couldn't be read
    # at all (Georgia's and Nevada's are behind bot walls). source_name
    # stays the state's — it is the state's document — and the card names
    # both. Null whenever the state's own copy was read.
    republished_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Watermark for the reconciliation grace period (see
    # election_pipeline._sync_ballot_measures): a measure absent from a
    # sync isn't deleted on the spot, because one truncated upstream
    # response would otherwise blank a state's ballot.
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    as_of: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class MeasureCoverage(Base):
    """Per-state, per-election record of what we actually know about that
    state's measures — the difference between "this state has none" and
    "we haven't ingested this state yet".

    Without this row those two render identically (an empty section), and
    a Texan looking at an empty section on a page titled as their state's
    ballot concludes there is nothing to research. That is the same
    null-is-not-zero discipline Candidate.last_financials_sync already
    enforces per field ("awaiting FEC sync", never "$0"), applied at the
    collection level.

    Default for an unknown state is NOT_YET_COVERED, so a state we have
    never synced is loud rather than blank.
    """
    __tablename__ = "measure_coverage"
    __table_args__ = (
        UniqueConstraint("state", "election_date", name="uq_measure_coverage_state_date"),
    )

    NOT_YET_COVERED = "not_yet_covered"
    COVERED = "covered"
    CONFIRMED_NONE = "confirmed_none"
    INGEST_FAILED = "ingest_failed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    election_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=NOT_YET_COVERED)
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    measure_count: Mapped[int] = mapped_column(Integer, default=0)
    # When a read was last ATTEMPTED — failures included.
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    # When the status shown was last established by a read that worked.
    # Never advanced by an ingest failure, so a page still showing earlier
    # measures after a failed read can say how old they are.
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A read that shrank past MEASURE_SHRINK_FLOOR is held back until the
    # same shorter list repeats (election_pipeline._accept_shrink): the
    # ids of that list (JSON) and how many consecutive runs returned it.
    pending_shrink: Mapped[str | None] = mapped_column(Text, nullable=True)
    shrink_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set when an operator accepted this state's absence for the election
    # (admin accept-absence): who/why, verbatim. A reader answering again
    # clears it.
    operator_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Every accept-absence action ever taken for this state/election (JSON
    # list of {at, note, force, marked}) — the audit trail. Never cleared.
    operator_actions: Mapped[str | None] = mapped_column(Text, nullable=True)


class PipelinePhaseTiming(Base):
    """One row per completed pipeline step, durable across runs.

    ProgressTracker already recorded startedAt/completedAt per step, but
    only into the run row's `progress_detail` JSON blob — which is
    overwritten by the next run and is not queryable. That made
    "which step grew?" unanswerable: the only cross-run number retained
    was the run's total `elapsed_seconds`, so a pipeline going from 90
    minutes to 12 hours gave no signal about *where* the time went.

    Deliberately a separate table rather than more columns on the five
    run models: the step list differs per pipeline and changes over
    time, so a row-per-step keyed by (run_kind, run_id) survives step
    definitions being added, renamed, or dropped without a migration.

    `run_kind` is the run model's __tablename__ (e.g. "pipeline_runs"),
    derived by ProgressTracker from the row it was handed — no call site
    passes it, so the five existing pipelines get timings with no change
    to any of them.

    `phase` is the coarse grouping already present in each pipeline's
    STEPS tuples (fetch/transform/analyze/finalize). Rolling up on it is
    the point: it separates time spent waiting on rate-limited external
    APIs from time spent on local compute, which is the distinction that
    decides whether more hardware would help at all.
    """
    __tablename__ = "pipeline_phase_timings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # "done" | "skipped" | "failed" — the ProgressTracker step vocabulary,
    # not PipelineStatus (which describes a whole run).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="done")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Null for a step that was skipped before it ever began, or whose
    # start timestamp was lost to a mid-run restart.
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "run_kind", "run_id", "step_key",
            name="uq_phase_timing_run_step",
        ),
    )


class PipelineRateLimitStat(Base):
    """Per-step, per-source rate-limiter accounting for one pipeline run.

    A phase duration says a step took four hours. It does not say whether
    those hours were spent computing or spent inside RateLimiter.acquire()
    deliberately sleeping to stay under the FEC's 0.25 RPS. Those two
    readings point at opposite remedies — restructure the fetch phase, or
    add compute — so the run data has to distinguish them.

    `blocked_seconds` is wall time callers spent waiting for a grant, not
    time spent on the HTTP request itself. A step whose blocked_seconds
    approaches its phase duration is throughput-bound on someone else's
    rate limit, and no amount of local hardware changes it.

    Caveat, deliberately recorded rather than engineered away: counters
    are global per limiter, and attribution is by time window. If two
    pipelines genuinely overlap on the same source, each attributes the
    other's waiting to whatever step it had open. The pipelines are
    sequenced in practice (see house_pipeline's tracker and the hourly
    action refresh's skip), so this is a known imprecision at the edges,
    not a routine one — but it means a single surprising row deserves a
    look at what else was running before it is believed.
    """
    __tablename__ = "pipeline_rate_limit_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Limiter name — the fetch module it was constructed in ("fec",
    # "congress", "govinfo"), or an explicit name where a module has more
    # than one limiter.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint(
            "run_kind", "run_id", "step_key", "source",
            name="uq_rate_limit_stat_run_step_source",
        ),
    )


class MemberIdAlias(Base):
    """A member id that was renamed (app/member_ids.py), and the id it
    became: posts and search engines still hold URLs under the old one, so
    /politicians/<old_id> and the API answer it with the member it now
    names. One row per old id; a later rename repoints every alias of the
    person to the newest id. A live member's own id always wins over an
    alias of the same string."""
    __tablename__ = "member_id_aliases"

    old_id: Mapped[str] = mapped_column(String, primary_key=True)
    new_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    bioguide_id: Mapped[str | None] = mapped_column(String, nullable=True)
    renamed_at: Mapped[datetime] = mapped_column(default=utcnow)


class BskySenatorSpotlight(Base):
    """Tracks which senators/representatives have been highlighted in daily
    Bluesky score posts.

    `senator_id` holds either a Senator's or a Representative's `id`,
    distinguished by `chamber` — kept as its original name rather than
    renamed to something generic when House support was added, since this
    codebase's lightweight ADD-COLUMN-only migration helper
    (database.py's _migrate_columns) has no rename primitive, and a real
    rename would need one. `chamber` defaults to "senate" so every
    pre-existing row (all senators, from before House support existed)
    backfills correctly with no manual migration.
    """
    __tablename__ = "bsky_senator_spotlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    senator_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chamber: Mapped[str] = mapped_column(String(10), nullable=False, default="senate")
    posted_at: Mapped[datetime] = mapped_column(default=utcnow)
    post_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class BroadcastPost(Base):
    """One thing Civitas published: the entry the Atom feed serves, and the
    record every outbound channel (Bluesky today) delivers from.

    Written by `app/broadcast.publish` and nowhere else, before any channel
    is tried, so the feed never depends on a third party accepting a post.
    Channels keep their own delivery state here (`bsky_*`); the feed has
    none, since readers pull it.

    `id` is never reused (sqlite_autoincrement): it is the entry's Atom id,
    and a feed reader that saw id 41 once must never see a different post
    under it.
    """
    __tablename__ = "broadcast_posts"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # broadcast.KINDS.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # What the post is about, as "<kind>:<id>" ("race:2026-SEN-GA",
    # "congress-day:2026-09-24", "issue:<public id>", "member:<chamber>:<id>"): the
    # key the posting modules' dedupe, budgets and cooldowns read, so they
    # hold across a data reset, which keeps this table (RESET_KEEPS).
    subject: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # The outside item a post restates, when it restates one (a race
    # update's news article): published once, ever, whatever its dates say.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    # Two-letter state the post is about, when it is about one (a race, a
    # member) — what the per-state feeds filter on.
    state: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    # off (no account configured when published) | pending | sending | sent | failed.
    bsky_status: Mapped[str] = mapped_column(String(10), nullable=False, default="off", server_default="off")
    bsky_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # When the last send was started, so retries are spaced (broadcast.RETRY_AFTER).
    bsky_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bsky_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The linked page's card (its Open Graph tags, bluesky_utils.og_card):
    # the same image and description the Bluesky link card shows, kept for
    # the feed entry. card_image is NULL until the page has been read, ""
    # when it was read and sets no image.
    card_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_image_alt: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SiteVisit(VisitsBase):
    """One row per unique visitor per day — never raw IP/PII.

    `visitor_hash` is an HMAC of the visitor's IP keyed by that UTC day's
    random salt (VisitSalt; see api/visits.py). The salt exists only while
    its day is current and is then deleted, so after the day ends nobody —
    the operator included — can recompute a hash from an IP; with the IPv4
    space small enough to enumerate, a permanent key would have made every
    stored hash reversible. The (date, visitor_hash) primary key means a
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
    # on its own.
    browser: Mapped[str] = mapped_column(String(20), default="")
    os: Mapped[str] = mapped_column(String(20), default="")
    device_type: Mapped[str] = mapped_column(String(10), default="")


class VisitSalt(VisitsBase):
    """The random salt for the current UTC day's visitor hashes.

    Lives in the visits database so both API workers hash a visitor
    identically (a per-process salt would count one person twice). At most
    one row exists: api/visits.py deletes every other day's salt when it
    creates the current one, which is what makes older hashes unlinkable.
    """
    __tablename__ = "visit_salts"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD, UTC
    salt: Mapped[str] = mapped_column(String(64), nullable=False)  # hex


class VisitsMigration(VisitsBase):
    """One-time data migrations applied to the visits database."""
    __tablename__ = "visits_migrations"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(default=utcnow)


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


class IssueView(VisitsBase):
    """Per-issue daily view counts, from /issue/{public_id} page loads —
    the signal behind ActionIssue's "trending" badge (app/trending.py).

    Deliberately its own table rather than folded into PageView: that
    table normalizes every dynamic route (including /issue/[id]) down to
    its template specifically so per-id storage never happens — see
    _normalize_path's docstring. This table is the one deliberate
    exception, safe for the same reason PageView's normalization exists to
    avoid it elsewhere: ActionIssue rows are inherently few (is_current
    scopes to a handful at a time), nothing like the politician/bill
    catalog PageView protects against fragmenting across.

    Keyed on issue_public_id (app/issue_ids.py), not the internal
    autoincrement id — that's what actually appears in track_visit's raw
    path, and it's the id ActionIssueSchema.is_trending has to match back
    against on read.
    """
    __tablename__ = "issue_views"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    issue_public_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class PageLoadTiming(VisitsBase):
    """Page-load durations as a daily histogram per route — never per visit.

    Each hard page load's Navigation Timing (TTFB, first contentful paint,
    load event) is reported by the browser (`POST /api/track-timing`) and
    folded into a fixed bucket ladder (api/visits.py's LOAD_TIMING_BUCKETS_MS):
    one counter per (date, route template, metric, bucket). No hash, no
    User-Agent, no exact duration is kept — only "one more load of
    /leaderboard landed in the 750-1000ms bucket today" — so there is nothing
    here that could single out a visitor, and the table is bounded by
    days x routes x 3 metrics x ~20 buckets regardless of traffic.

    A histogram rather than a running mean because the admin dashboard
    charts p50/p95: one slow outlier moves a mean, and a mean can't be
    turned back into percentiles later. Percentiles are interpolated inside
    the bucket (admin.py's _histogram_percentile), so their precision is the
    bucket width — plenty to see a regression, not a benchmark.
    """
    __tablename__ = "page_load_timings"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    path: Mapped[str] = mapped_column(String(100), primary_key=True)
    metric: Mapped[str] = mapped_column(String(8), primary_key=True)  # ttfb | fcp | load
    # Upper bound of the bucket, in ms (a member of LOAD_TIMING_BUCKETS_MS).
    bucket_ms: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class ApiRejectionCount(VisitsBase):
    """Why public API requests were refused as invalid (422), as daily
    counters: the parameter and the rule it broke ("doc_type",
    "literal_error"), never the value sent and nothing about the caller.
    Without it a run of 422s says only that callers got something wrong,
    not what to fix in the API or its documentation."""
    __tablename__ = "api_rejection_counts"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD, UTC
    endpoint: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(8), primary_key=True)  # http | mcp
    parameter: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(64), primary_key=True)  # the validation error type
    count: Mapped[int] = mapped_column(Integer, default=0)


class ApiRequestCount(VisitsBase):
    """Public API and MCP use, as daily counters — never per caller.

    One counter per (date, endpoint, channel, status): "one more GET of
    list_senators answered 200 over plain HTTP today". The endpoint is the
    route's operation id, the channel is how it arrived (`http`, or `mcp`
    for a tool call), and nothing about the caller is kept — no IP, no
    hash, no User-Agent — so this cannot be joined to a SiteVisit or used
    to follow anyone (AGENTS.md §8). Bounded by days x endpoints x 2
    channels x the handful of statuses a route returns, whatever the
    traffic. Kept apart from SiteVisit/PageView on purpose: a program
    calling the API is not a visitor reading the site.
    """
    __tablename__ = "api_request_counts"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD, UTC
    endpoint: Mapped[str] = mapped_column(String(64), primary_key=True)  # operation id, or tools/list
    channel: Mapped[str] = mapped_column(String(8), primary_key=True)  # http | mcp
    status: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class StatewideNominee(Base):
    """A confirmed nominee for a STATEWIDE EXECUTIVE office (Governor,
    Lieutenant Governor, Attorney General, Secretary of State, Treasurer).

    Deliberately NOT a Candidate row. Candidate is FEC-derived — it carries
    a filing id, campaign-finance totals and a Representation Score, none
    of which exist for a state office, and forcing one in would mean a row
    whose every financial field is permanently null while sitting in the
    same table the federal scorecard reads from.

    These come from the same per-state adapters that already resolve
    federal nominees (see fetch/state_candidates.py): the state's own
    primary results name them, so no new source is needed — the contests
    were being parsed and discarded. parse_statewide_office is the gate,
    and it is as conservative as parse_office is in the other direction:
    a county or municipal office that happens to contain "Treasurer" or
    "Attorney General" is refused rather than ranked.

    A missing row is "not on this state's ballot this cycle OR not yet
    ingested" — which of the two is answered by the sync marker
    api_cache_get("statewide", f"synced-{state}-{year}"), the same
    null-is-not-zero discipline MeasureCoverage enforces for measures.
    """
    __tablename__ = "statewide_nominees"
    __table_args__ = (
        # The NAME is part of the key, not just the seat and party. A
        # top-two state (California, Washington) runs one all-party
        # contest and advances two candidates, who can be of the SAME
        # party — and an unaffiliated candidate normalises to no party at
        # all, so two of them in one contest would share a key entirely.
        # Keyed on seat and party alone, the second nominee silently
        # overwrote the first. Stale rows are removed by the sync's own
        # delete-what-is-no-longer-reported pass, so a renamed candidate
        # still leaves exactly one row.
        UniqueConstraint(
            "state", "cycle_year", "office", "district", "party", "display_name",
            name="uq_statewide_nominee_seat_party_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    cycle_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # One of state_candidates_common.STATEWIDE_OFFICE_LABELS' keys.
    office: Mapped[str] = mapped_column(String(32), nullable=False)
    # Null for almost every statewide office, because almost none has a
    # seat: there is one Secretary of State. A few statewide BODIES seat
    # their members by district while still electing them statewide —
    # Georgia's Public Service Commission runs District 3 and District 5
    # as separate contests — and without this they would be one office,
    # the second overwriting the first.
    district: Mapped[str | None] = mapped_column(String(8), nullable=True)
    party: Mapped[str] = mapped_column(String(1), nullable=False)
    # The party exactly as the state printed it, set only when `party` is
    # state_candidates_common.OTHER_PARTY: a certified November list that
    # names a party the shared codes cannot (Vermont's "FREEDOM AND
    # UNITY", South Carolina's "Workers"). The page shows this label
    # rather than a code. Null for every recognised party.
    party_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The name the state itself printed, annotations stripped (Rhode
    # Island marks its party-endorsed candidates with a bare asterisk).
    # There is deliberately no separate surname column: a surname exists
    # on a Candidate to match FEC's own filing, and a statewide office
    # has no FEC filing to match — this name is rendered, never joined on.
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StateLegNominee(Base):
    """A confirmed nominee for a seat in a STATE legislature.

    The same shape as StatewideNominee and separate from it for the same
    reason it is separate from Candidate: there is no FEC filing behind a
    state house seat, so there is no finance data, no Representation
    Score, and no surname to join on — the name the state printed is the
    whole record. It is a distinct table rather than a nullable
    `district` column on StatewideNominee because the two answer
    different questions and are keyed differently: a statewide office is
    unique per state, a legislative seat is unique per district, and
    collapsing them would make the uniqueness constraint express neither.

    `chamber` is "upper" or "lower" rather than a state's own name for
    it, because those names do not generalise — Rhode Island's two
    chambers are both "the General Assembly", Nebraska has one, and
    "Assembly" means the lower house in New York and the whole body in
    Rhode Island. parse_state_leg_office resolves a state's wording to
    the neutral pair; STATE_LEG_CHAMBER_LABELS renders it back.

    Which towns a district covers is NOT stored here. It comes from
    app/data/state_leg_district_crosswalk.json, a static bundled file
    keyed "{state}-{chamber}-{district}", because it changes only when a
    state redistricts and has nothing to do with who is running.
    """
    __tablename__ = "state_leg_nominees"
    __table_args__ = (
        # Includes the name for the same reason StatewideNominee does:
        # under top-two two same-party (or two no-party) candidates
        # legitimately advance from one seat.
        UniqueConstraint(
            "state", "cycle_year", "chamber", "district", "seat",
            "party", "display_name",
            name="uq_state_leg_nominee_seat_party_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    cycle_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # "upper" | "lower" — see STATE_LEG_CHAMBER_LABELS.
    chamber: Mapped[str] = mapped_column(String(8), nullable=False)
    # A STRING, not an int, because legislative districts are not always
    # numbered. Minnesota splits each of its 67 senate districts into two
    # house districts named "10A" and "10B" (verified against its real
    # 2026 primary export), and other states use letters too. Storing an
    # int would work for Rhode Island and silently fail to represent half
    # the country. Sorting is natural-order, not lexical, so 9 precedes
    # 10 — see _district_sort_key.
    district: Mapped[str] = mapped_column(String(8), nullable=False)
    # Which seat OF that district, where a state elects more than one
    # member from it: Idaho's "A"/"B", Washington's "1"/"2". Null almost
    # everywhere else. The district still names the single geography the
    # seats share, which is what keeps the town crosswalk correct — and
    # is why this is a column rather than a suffix on `district`, since
    # Minnesota's "10A" really is a district of its own.
    seat: Mapped[str | None] = mapped_column(String(4), nullable=True)
    party: Mapped[str] = mapped_column(String(1), nullable=False)
    # As on StatewideNominee: the printed party, for OTHER_PARTY rows only.
    party_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class JudicialNominee(Base):
    """A confirmed nominee for an elected JUDGESHIP.

    Third of the same family as StatewideNominee and StateLegNominee,
    and separate from both for the same reason they are separate from
    each other: the three are keyed differently. A statewide office is
    unique per state, a legislative seat per district, and a judgeship
    per court AND seat — a state has a District Court seat 2 in each of
    many districts, and a Court of Appeals seat 4 with no district at
    all. Collapsing any two would make the uniqueness constraint
    express neither.

    `court` is "supreme" | "appeals" | "superior" | "district" rather
    than a state's own wording, for the same reason `chamber` is
    "upper"/"lower": the names do not generalise. What one state calls
    its Superior Court is another's Circuit Court, and "District Court"
    is a trial court in North Carolina and a federal court elsewhere.
    parse_judicial_office resolves a state's wording to the neutral set;
    JUDICIAL_COURT_LABELS renders it back.

    `district` is nullable because an appellate or supreme seat is
    elected statewide and has no district, unlike a legislative seat
    which always has one.

    Only PARTISAN judicial contests are stored. A non-partisan judicial
    election usually ELECTS a majority winner outright rather than
    nominating them, so reading one the same way would publish a judge
    who has already won as a candidate still standing — see
    parse_judicial_office's docstring for the full reasoning and the
    survey behind it.
    """
    __tablename__ = "judicial_nominees"
    __table_args__ = (
        # Name included for the same reason the other two include it:
        # under a top-two or top-four rule two same-party candidates can
        # legitimately advance from one seat.
        UniqueConstraint(
            "state", "cycle_year", "court", "district", "seat",
            "party", "display_name",
            name="uq_judicial_nominee_seat_party_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    cycle_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # "supreme" | "appeals" | "superior" | "district".
    court: Mapped[str] = mapped_column(String(10), nullable=False)
    # A STRING for the same reason a legislative district is: North
    # Carolina's real trial districts include "10A" and "16B".
    district: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Which seat of that court/district. Every judicial contest surveyed
    # names one — "SEAT 2", "Group 4", "Position #1" — because a
    # judgeship is a single office, never a multi-member body.
    seat: Mapped[str | None] = mapped_column(String(8), nullable=True)
    party: Mapped[str] = mapped_column(String(1), nullable=False)
    # As on StatewideNominee: the printed party, for OTHER_PARTY rows only.
    party_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CongressDay(Base):
    """What one chamber did on one day, as the record says it (/congress).

    One row per chamber per calendar day it appears in a source: the
    Congressional Record's Daily Digest once published (final), before
    that the chamber's own live floor log. `source` says which, and
    `is_final` whether the Digest has replaced the live log. A day the
    Digest says the chamber did not meet is stored with in_session False,
    so "not in session" is a finding and a missing row is "not fetched".
    """
    __tablename__ = "congress_days"
    __table_args__ = (UniqueConstraint("chamber", "date", name="uq_congress_day_chamber_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chamber: Mapped[str] = mapped_column(String(6), nullable=False)  # "senate" | "house"
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    in_session: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Verbatim from the record: "Senate convened at 10 a.m. and adjourned
    # at 4:05 p.m., until ..." / "The House met at 2:30 p.m. and ...".
    adjournment_text: Mapped[str] = mapped_column(Text, default="")
    convened_at: Mapped[str | None] = mapped_column(String(16), nullable=True)   # "10 a.m."
    adjourned_at: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "4:05 p.m."
    # The Digest's "Next Meeting of the SENATE": when, and its program.
    next_meeting: Mapped[str | None] = mapped_column(String(120), nullable=True)
    next_program: Mapped[str] = mapped_column(Text, default="")
    # Counts the record states in words ("Seventy-four bills and fourteen
    # resolutions were introduced"); NULL when the record gives none.
    bills_introduced: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolutions_introduced: Mapped[int | None] = mapped_column(Integer, nullable=True)
    introduced_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # "digest" | "floor_log"
    source_url: Mapped[str] = mapped_column(String(500), default="")
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CongressEvent(Base):
    """One entry of a chamber's day: a measure passed, failed or reported,
    a nomination confirmed, a committee meeting, or a floor-log line.

    `text` is the record's own wording, never rewritten. Record votes are
    not events: they live in RollCall, which has the tally and every
    member's position.
    """
    __tablename__ = "congress_events"
    __table_args__ = (Index("ix_congress_event_day", "chamber", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chamber: Mapped[str] = mapped_column(String(6), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    # "passed" | "failed" | "reported" | "confirmed" | "committee" | "floor"
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # order within the day
    name: Mapped[str] = mapped_column(String(500), default="")  # short title / committee
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Bill id in the site's form ("S.3257", "HCONRES.89"); NULL when none.
    bill_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    time: Mapped[str | None] = mapped_column(String(16), nullable=True)  # floor log only
    pages: Mapped[str] = mapped_column(String(60), default="")  # "Pages S5017-19"
    source: Mapped[str] = mapped_column(String(16), nullable=False)


class RollCall(Base):
    """A recorded vote, from the chamber's own roll-call XML."""
    __tablename__ = "roll_calls"
    __table_args__ = (
        UniqueConstraint("chamber", "congress", "session", "number", name="uq_roll_call"),
        Index("ix_roll_call_date", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chamber: Mapped[str] = mapped_column(String(6), nullable=False)
    congress: Mapped[int] = mapped_column(Integer, nullable=False)
    session: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    question: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(120), default="")
    rejected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    majority_requirement: Mapped[str] = mapped_column(String(10), default="")
    yeas: Mapped[int] = mapped_column(Integer, default=0)
    nays: Mapped[int] = mapped_column(Integer, default=0)
    present: Mapped[int] = mapped_column(Integer, default=0)
    not_voting: Mapped[int] = mapped_column(Integer, default=0)
    bill_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(String(300), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RollCallPosition(Base):
    """How one member voted on one roll call, as the chamber recorded it."""
    __tablename__ = "roll_call_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    roll_call_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roll_calls.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # House XML carries the bioguide id; Senate XML carries the LIS id.
    member_id: Mapped[str] = mapped_column(String(12), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), default="")
    party: Mapped[str] = mapped_column(String(2), default="")
    state: Mapped[str] = mapped_column(String(2), default="")
    # "Yea" | "Nay" | "Present" | "Not Voting" (the House's "Aye"/"No"
    # are stored as recorded).
    position: Mapped[str] = mapped_column(String(12), nullable=False)
