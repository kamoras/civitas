import re
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.config_definitions import BILL_STAGES


def to_camel(string: str) -> str:
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])



# Public API v1's composite fields (a member's `overall` and `rank`, a
# president's `rank`): the site stopped showing the composite in 2026-10
# (AGENTS.md section 3). Kept for one deprecation cycle, marked deprecated in
# the OpenAPI schema (and so on /developers), with Deprecation and Sunset
# headers on the responses that carry them (api/public.py); removed in v2.
API_V1_SUNSET = "2027-01-04"
_DEPRECATED = f"Deprecated: the composite is no longer shown on the site (2026-10); removed in API v2, sunset {API_V1_SUNSET}. "
_DEPRECATED_FIELD = {"deprecated": True}
# A member's 0-100 part scores and how they are built: member pages stopped
# showing them too (2026-10), so they go with the composite in v2. A
# president's scores are not deprecated.
_DEPRECATED_PART = (f"Deprecated: member pages no longer show 0-100 scores (2026-10); removed in API v2, sunset "
                    f"{API_V1_SUNSET}. Read the breakdown's peerComparison and each dimension's facts instead. ")

class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# --- Sub-schemas ---


class DonorSchema(CamelModel):
    name: str
    total: float = Field(description="Given in the election period, in dollars")
    # "SKIP" = donor_classifier_ai.py's low-confidence sentinel (see
    # normalize_finance.py) — a real, first-class classification outcome
    # filtered out of certain aggregates elsewhere (policy_alignment.py,
    # cross_reference.py), not an error state, so it's a valid wire value.
    type: Literal["PAC", "Individual", "SuperPAC", "Org/Employees", "Party/Ideological", "CandidateAffiliated", "Self-Funded", "SKIP"]
    industry: str = "OTHER"
    pac_sponsor: str | None = None
    pac_industry: str | None = None
    pac_analysis: str | None = None
    # FEC committee_type code ("Q"=Qualified/multicandidate, "N"=Nonqualified,
    # ...) for this donor's own committee, when the donor is one. Reported,
    # not scored (the PAC-utilization signal that read it left in v6.22).
    committee_type: str | None = None


class IndustryDonationSchema(CamelModel):
    industry: str = Field(description="Industry code. LARGE_INDIVIDUAL is itemized individual gifts whose "
                                      "industry couldn't be told; UNCLASSIFIED, money no classification reached")
    name: str = Field(description="The code as words")
    total: float = Field(description="In dollars")
    percentage: float = Field(description="Share of the breakdown's total, 0-100, rounded to a whole number")


class HeldScoreSchema(CamelModel):
    failed_on: str = Field(description="The run (YYYY-MM-DD) that could not score the member")
    source: str | None = Field(description="What that run could not read (\"FEC filings\", \"Congress.gov\"), or "
                                           "null when the failure names no source")
    scored_on: str | None = Field(description="The run (YYYY-MM-DD) whose scores the member keeps")
    algorithm_version: str | None = Field(description="The scoring version of that run")


class RepresentationScoreSchema(CamelModel):
    funding_independence: float = Field(
        description=_DEPRECATED_PART + "0-100: how free the member is from PAC and large-donor dependence. "
                                       "Weighted into overall", json_schema_extra=_DEPRECATED_FIELD)
    promise_persistence: float | None = Field(
        None,
        description="Always null. It measured campaign promises kept, and campaign-promise tracking was removed in 2026-07 (its LLM evaluations were unreliable); the stored value since then was a constant, not a measurement. Kept so existing clients still find the field")
    constituent_alignment: float = Field(
        description=_DEPRECATED_PART + "0-100: how the member's votes compare with what the seat's partisan lean "
                                       "predicts. Weighted into overall", json_schema_extra=_DEPRECATED_FIELD)
    funding_diversity: float = Field(
        description=_DEPRECATED_PART + "0-100, informational: how widely funding spreads across industries and "
                                       "donor kinds", json_schema_extra=_DEPRECATED_FIELD)
    legislative_effectiveness: float = Field(
        0.0, description=_DEPRECATED_PART + "0-100: how far the member's bills advance, and the coalitions they "
                                            "draw. Weighted into overall", json_schema_extra=_DEPRECATED_FIELD)
    # Backend-computed overall (score_calculator.compute_overall_score) — the
    # frontend must never recompute this from the sub-scores itself (see
    # lib/representation.ts's removed weightedScore).
    overall: float = Field(0.0, description=_DEPRECATED + "The weighted overall score, 0-100 (weights: the API "
                                                         "index's scoreWeights)", json_schema_extra=_DEPRECATED_FIELD)
    confidence: dict[str, str] | None = Field(
        None, description='How much data backs each sub-score, by its name: "high", "medium" or "low". Also '
                          'constituentAlignmentVotePart, how that score\'s vote part was scored: "full"; '
                          '"shrunk:<share kept>" or "shrunk-neutral:<share kept>" (few votes, pulled toward the '
                          'party\'s typical score or toward 50); "typical:few-votes"; or "neutral:<reason>" (scored 50). And '
                          'legislativeEffectivenessBills, where that score\'s bills came from: "fetched:<date>" (read '
                          'that day), "carried:<date>" (this run couldn\'t read them; the bills read on that date) or '
                          '"unavailable" (none known, so the bill part is not measured)')
    held: HeldScoreSchema | None = Field(
        None, description="Set when the latest run could not score the member, so these scores are an earlier "
                          "run's (scoredOn, algorithmVersion), which other members' may not share")
    thin_data: list[str] = Field(
        default_factory=list,
        description="Weighted dimensions whose confidence is \"low\": too little source data for the dimension "
                    "to be more than a pull toward a neutral 50")


class PolicyAreaDetail(CamelModel):
    area: str
    confidence: float = Field(description="How closely the bill reads as this area, 0-1 (cosine similarity)")
    party: str = Field("bipartisan", description='"D", "R" or "bipartisan": which party\'s platform the area leans to')


class KeyVoteSchema(CamelModel):
    bill_name: str
    bill_id: str
    date: str
    vote: Literal["Yea", "Nay", "Present", "Not Voting"]
    policy_area: str = "PROCEDURAL"
    policy_areas: list[PolicyAreaDetail] = []
    party_alignment_weight: float = 0.0
    stance: str = "neutral"
    description: str = ""
    party_leaning: Literal["R", "D", "bipartisan"] | None = None
    voted_with_party: bool | None = None
    vote_category: Literal["recent", "key"] = "key"
    # The Congress record's roll call (bill_record.roll_call_summaries):
    # date, question, result and each party's tally. None for votes stored
    # before it was recorded.
    roll_call: dict | None = None


class FundingSchema(CamelModel):
    """The member's most recent completed election campaign (FEC)."""
    total_raised: float = Field(description="All receipts, in dollars")
    # Denominator for PAC / small-donor shares (contributions + candidate
    # self-loans; see normalize_finance.summarize_election_totals). None on
    # records scored before it existed — clients fall back to total_raised.
    total_contributions: float | None = Field(
        None, description="Contributions (and candidate loans), in dollars: what the shares are taken over; "
                          "null on a record scored before it was kept")
    # Read from the pipeline's "totalFromPACs" key; written as "totalFromPacs",
    # the spelling every other response (the leaderboards) uses.
    total_from_pacs: float = Field(
        validation_alias=AliasChoices("totalFromPACs", "totalFromPacs"), serialization_alias="totalFromPacs",
        description="From PACs, in dollars",
    )
    pac_share_pct: float = Field(0.0, description="PAC money as a percentage of contributions, 0-100")
    small_donor_percentage: float | None = Field(
        description="Share of contributions that were unitemized individual gifts (donors giving $200 or less), "
                    "0-100; null when the campaign itemizes every gift, so the filings can't say")
    top_donors: list[DonorSchema] = Field(description="Largest donors first")
    industry_breakdown: list[IndustryDonationSchema] = Field(description="Contributions by industry")


class VotingRecordSchema(CamelModel):
    """Roll-call votes in the current Congress."""
    total_votes: int = Field(description="Roll-call votes stored for the member: the key and recent votes listed")
    voted_with_party_count: int = Field(
        0, description="Party-line votes this Congress cast with the member's party, as Constituent Alignment counts them")
    voted_against_party_count: int = Field(
        0, description="Party-line votes this Congress cast against the member's party toward the other one "
                       "(breaks), each measure once, as Constituent Alignment counts them")
    party_loyalty_pct: float = Field(0.0, description="With-party share of party-line votes, 0-100")
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


# A holding's value stated as one exact figure ("$1,251.00") rather than a
# bracket — the House form lets a filer give the exact value instead
# (2025 reports: 15 holdings across 2 reports). Stored as low == high, the
# same encoding as the open-ended bracket, and told apart from it by the
# printed text, which every holding keeps (value_text).
EXACT_VALUE_RE = re.compile(r"^\$\s*\d[\d,]*(?:\.\d+)?$")


def is_open_ended(low: float | None, high: float | None, text: str | None = None) -> bool:
    """The disclosure forms' open-ended top bracket ("Over $50,000,000"),
    which states a floor and no ceiling. Stored as high == low — no real
    bracket on these forms has equal bounds (ptr_common.parse_amount_range).
    The one definition of that rule: trades, holdings, and every sum over
    holdings use it. A holding passes its value_text, so an exact stated
    value (EXACT_VALUE_RE), the one other low == high, isn't read as a floor."""
    if text is not None and EXACT_VALUE_RE.match(text.strip()):
        return False
    return low is not None and low > 0 and high == low


# Statutory disclosure deadline under the STOCK Act (2012) — see issue #45.
STOCK_ACT_DISCLOSURE_DEADLINE_DAYS = 45


_Owner = Literal["self", "spouse", "joint", "dependent", "unknown"]
_OWNERS = get_args(_Owner)


def _stated_owner(value: object) -> object:
    return value if value in _OWNERS else "unknown"


# Whose asset a disclosed trade or holding is. "unknown": the form's owner
# value wasn't one the parser recognizes — never guessed to be the member's.
# A stored value outside the set (a row written by another image's parser)
# reads the same way, rather than failing the member's whole response.
DisclosureOwner = Annotated[_Owner, BeforeValidator(_stated_owner)]


class StockTradeSchema(CamelModel):
    ticker: str | None = None
    asset_name: str
    owner: DisclosureOwner = "self"
    transaction_type: Literal["purchase", "sale_full", "sale_partial", "exchange"]
    # None: a scanned presidential row whose date isn't legible.
    transaction_date: str | None
    disclosure_date: str
    # None where the row can't support a timeliness figure (below).
    days_to_disclose: int | None
    late: bool | None = False
    amount_low: float
    amount_high: float
    # True when the filing used the open-ended top bracket ("Over
    # $50,000,000"), which states a floor and no ceiling. Derived from
    # amount_high == amount_low, the encoding parse_amount_range uses for
    # exactly this case — no real bracket on these forms has equal bounds.
    # Consumers must render these as "$X+", never as a closed range, since
    # the upper figure is this platform's placeholder and not a disclosed
    # number.
    amount_open_ended: bool = False
    industry: str = "UNCLASSIFIED"
    source_url: str
    parse_confidence: Literal["text", "ocr"] = "text"
    # "annual": a presidential annual report's transaction (PresidentTrade).
    report_kind: Literal["periodic", "annual"] = "periodic"
    # A president's trade dated before the term began: an annual report
    # covers the calendar year, so the first one lists the weeks before the
    # inauguration. Members are never flagged: a representative's sworn
    # date is this Congress's oath, which a returning member's earlier
    # trades also predate.
    before_term_start: bool = False

    @model_validator(mode="after")
    def _compute_derived_flags(self) -> "StockTradeSchema":
        # Derived, not stored — see StockTrade model comment on
        # days_to_disclose for why this isn't a separate DB column. None
        # when the row can't support it: an annual report states no date
        # the transaction was first reported, and a date read by OCR from a
        # scan may be misread by a digit (ptr_common.window_date), enough
        # to mark an on-time trade late.
        if self.report_kind == "annual" or self.parse_confidence == "ocr":
            self.days_to_disclose = None
            self.late = None
        else:
            self.late = self.days_to_disclose > STOCK_ACT_DISCLOSURE_DEADLINE_DAYS
        self.amount_open_ended = is_open_ended(self.amount_low, self.amount_high)
        return self


class PaginatedStockTradesSchema(CamelModel):
    trades: list[StockTradeSchema]
    total: int
    page: int
    per_page: int
    total_pages: int
    late_count: int


class HoldingSchema(CamelModel):
    """One asset from a member's latest annual financial disclosure. The
    value is the disclosed bracket: value_low/high are None when the filing
    states no bracket ("Undetermined"), 0/0 when the asset was held at no
    value at year end, and equal when the filing used an open-ended top
    bracket (value_open_ended) or, on the House form, stated the exact value
    instead of a bracket (value_open_ended false — see EXACT_VALUE_RE)."""
    asset_name: str
    account: str | None = None
    ticker: str | None = None
    asset_type: str
    category: str
    category_label: str
    owner: DisclosureOwner = "self"
    value_text: str
    value_low: float | None = None
    value_high: float | None = None
    # Same encoding and rendering rule as StockTradeSchema.amount_open_ended.
    value_open_ended: bool = False

    @model_validator(mode="after")
    def _compute_open_ended(self) -> "HoldingSchema":
        self.value_open_ended = is_open_ended(self.value_low, self.value_high, self.value_text)
        return self


class HoldingCategorySchema(CamelModel):
    """One slice of the holdings breakdown.

    `weight` is what the slice is sized by: the sum of each valued holding's
    bracket midpoint (the floor, for an open-ended top bracket). The forms
    disclose ranges, not values, so the midpoint is a stated convention for
    drawing proportions — value_low/value_high are the disclosed sums a
    reader should quote."""
    category: str
    label: str
    color: str
    count: int
    # Of `count`, those with no stated bracket: listed under the category,
    # but not in value_low/value_high/weight.
    unvalued_count: int = 0
    # Of `count`, those whose disclosed value was "None" — held at no value
    # at year end (sold or closed). Stated, but zero, so they draw nothing.
    zero_value_count: int = 0
    value_low: float
    value_high: float
    open_ended: bool
    weight: float
    share: float


class HoldingsSchema(CamelModel):
    # False when no annual report for this member has been ingested yet
    # (a newly seated member, or before the first ingest run) — every other
    # field is then at its default.
    available: bool = True
    # "2025 annual report", "new-filer report as of 2026-03-24" — what the
    # holdings describe, for the page to say in words.
    report_label: str = ""
    # The date the holdings describe: a year end for an annual report, the
    # stated date for a Senate new-filer report; None for a paper filing.
    as_of_date: str | None = None
    filed_date: str | None = None
    source_url: str = ""
    # False: the report exists but couldn't be read (a scanned paper
    # filing). The scorecard links to it rather than showing an empty
    # breakdown that would read as "holds nothing".
    parsed: bool = False
    # When not parsed: "scanned" (paper filing) or "unrecognized".
    unreadable_reason: Literal["scanned", "unrecognized"] | None = None
    # A Senate filing made on or after this report's date that states no year it can be
    # ranked by — a paper filing, or a title without one ("annual report
    # filed 2026-05-14") — named so the page doesn't imply this report is
    # the latest filed.
    later_filing_label: str | None = None
    later_filing_url: str | None = None
    holdings_count: int = 0
    # Holdings the form gave no bracket for ("Undetermined") — listed, but
    # in no slice.
    unvalued_count: int = 0
    total_low: float = 0.0
    total_high: float = 0.0
    total_open_ended: bool = False
    categories: list[HoldingCategorySchema] = Field(default_factory=list)
    category_filter: str | None = None
    holdings: list[HoldingSchema] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 15
    total_pages: int = 1


class CommitteeSchema(CamelModel):
    committee_name: str
    chamber: str
    title: str | None = None  # "Chairman" / "Ranking Member", else None


class LobbiedBillSchema(CamelModel):
    """A bill the member voted on that an LDA filing for a client of the
    donor's name names, one entry per client (fetch/lda.lobbied_bills_for).
    `client` is the registry's name for that client, which can be a separate
    company sharing the name."""
    bill_id: str
    label: str = ""
    bill_name: str = ""
    vote: str | None = None
    # What the vote shown decided ("passage", "cloture", "amendment" ...).
    motion_type: str | None = None
    # How the page says which vote is shown; "" for the vote on passage
    # (lda.vote_context). A row without one must not read as passage.
    vote_context: str = "on a motion, not necessarily passage"
    filing_year: int | None = None
    filing_url: str | None = None
    registrant: str | None = None
    # The registry's name for the filing's client (lda.is_same_client).
    client: str | None = None
    # The registrant when it isn't the client or named in it (lda._filed_by).
    filed_by: str | None = None
    filing_count: int = 1


class LobbyingClientSchema(CamelModel):
    """One registry client counted in a match's lobbying spend."""
    client: str
    amount: float
    # False when the year's filings ran past the page cap: amount is a floor.
    complete: bool = True


class RelatedVoteSchema(CamelModel):
    """The member's vote on one of a match's related bills
    (_scorecard_common.related_votes)."""
    bill_id: str | None = Field(description="The bill, in the site's spelling (\"SJRES.196\"); null when the "
                                            "record names none")
    label: str | None = Field(description="The bill as the Record prints it (\"S.J.Res. 196\")")
    title: str = Field(description="The roll call's title as the chamber recorded it, else the bill's")
    vote: Literal["Yea", "Nay"]
    date: str = Field(description="YYYY-MM-DD")
    roll_call: dict | None = Field(None, description="The roll call it was cast in (date, question, result, "
                                                     "each party's tally); null for a vote stored before "
                                                     "its roll call was recorded")


class LobbyingMatchSchema(CamelModel):
    lobbyist_org: str
    industry: str
    lobbying_spend: float
    donation_to_senator: float
    bills_influenced: list[str]
    # The member's own vote on each of bills_influenced, with its roll call.
    related_votes: list[RelatedVoteSchema] = []
    senator_vote_aligned: bool | None = None
    description: str
    lobbied_bills: list[LobbiedBillSchema] = []
    # lobbying_spend's parts by the registry's client names.
    lobbying_clients: list[LobbyingClientSchema] = []
    # False: the LDA lookup failed, so lobbying_spend is unknown, not zero.
    lobbying_checked: bool | None = None


class PolicyAlignmentSchema(CamelModel):
    area: str
    alignment: Literal["R", "D", "bipartisan"]
    strength: float = Field(description="How strongly, 0-1")


class PartisanDepthSchema(CamelModel):
    overall_lean: float = Field(description="Negative leans Democratic, positive Republican: the Yea/Nay votes "
                                            "on party-leaning bills, per policy area; the cosponsorship ideology "
                                            "fills in only while the member has fewer than 15 such votes")
    overall_party: Literal["R", "D", "centrist"]
    depth: Literal["deep", "moderate", "centrist", "cross-cutting"] = Field(
        description="The member's tercile of overallLean within their own party, from roll-call votes; "
                    "cross-cutting when many areas lean to the other party. A different measure from "
                    "ideologyLabel, which reads cosponsorship, so the two can disagree (a member can vote "
                    "with the party deep in its ranks while cosponsoring near its center)")
    cross_party_count: int = Field(description="Policy areas leaning to the other party")
    total_positions: int = Field(description="Policy areas with a lean")
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
    stage: str = Field("", description=f"Furthest stage reached: {', '.join(BILL_STAGES)}")
    # Content read as commemorative (analyze/commemorative.py): Legislative
    # Effectiveness weights it 1x, not 5x, like Volden & Wiseman.
    commemorative: bool = False


class BillInFlightSchema(CamelModel):
    bill_id: str
    # The title the sponsor's listing gives (the official one for a bill
    # introduced without a short title).
    title: str
    # The bill's short or popular title, verbatim from Congress.gov's titles
    # list, when it has one the listing title isn't, with its title type as
    # Congress.gov names it ("Short Title(s) as Passed House", "Popular
    # Titles"); fetch/bill_display_titles.display_title.
    short_title: str | None = None
    short_title_type: str | None = None
    # The official title from that list, shown under the short title.
    official_title: str | None = None
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
    # Bills past introduction and referral among stage_counts (the "bills
    # in motion" headline).
    in_motion: int = 0
    # sort=hot only: "mentioned" (named by a live Action Center issue) or,
    # when no issue names one, "moved" (an action since moved_since).
    shown: Literal["mentioned", "moved"] | None = None
    moved_since: str | None = None


class RelatedIssueSchema(CamelModel):
    id: int
    # `date` is the trending-day the issue was listed under (the fallback
    # /action?date= link when public_id is missing), not when this
    # happened — see ActionIssueSchema.first_surfaced's docstring. The label
    # shown next to this title has to use first_surfaced, not date, or this
    # list carries the exact same "today" drift the rest of the site fixed.
    date: str
    first_surfaced: str
    title: str
    # The issue's own page, /issue/{public_id}: the bill page links there
    # rather than to the whole day on /action.
    public_id: str | None = None


class StageBaseRateSchema(CamelModel):
    """What happens to this Congress's bills at a stage (bill_service.stage_base_rate)."""
    total: int
    at_stage: int
    reached_stage: int
    became_law: int
    sentence: str


class StageMilestoneSchema(CamelModel):
    label: str
    bills: int
    share: float
    share_text: str


class StageMilestonesSchema(CamelModel):
    """How far this Congress's bills have got, and this one (bill_service.stage_milestones)."""
    total: int
    milestones: list[StageMilestoneSchema]
    reached: int


class BillDetailSchema(BillInFlightSchema):
    policy_areas: list[PolicyAreaDetail] = []
    party_leaning: Literal["R", "D", "bipartisan"] | None = None
    related_issues: list[RelatedIssueSchema] = []
    stage_base_rate: StageBaseRateSchema | None = None
    stage_milestones: StageMilestonesSchema | None = None


class ConstituentApprovalPartySchema(CamelModel):
    party: Literal["D", "R", "I"]
    # Share approving, shrunk toward the typical member; None when the
    # survey can't tell members apart for this group (see
    # scripts/fetch_ces_approval.py).
    approve: float | None = Field(
        None, description="Share approving, 0-1 (not 0-100), shrunk toward the typical member; null when the "
                          "survey can't tell members apart for this group")
    # How much of `approve` is the member's own respondents (0-1).
    own_weight: float | None = Field(None, description="How much of approve is the member's own respondents, 0-1")
    respondents: int = Field(description="The member's own constituents of this party who answered")


class ConstituentApprovalSchema(CamelModel):
    """How the member's own constituents rated them in the CES, by the
    respondent's party (services/constituent_survey.py). Scored for senators
    (Constituent Alignment's approval part, v6.29); context for House members."""
    survey: str
    fielded: str = Field(description="When the survey was in the field, YYYY-MM/YYYY-MM")
    surveyed_as: str = Field(description="The name the survey asked about")
    by_party: list[ConstituentApprovalPartySchema] = Field(description="By the respondent's party")


_IDEOLOGY = ("From cosponsorship alone (SVD, no party labels as input), 0 most left to 1 most right; "
             "null with too little data")
_LEADERSHIP = ("Cosponsorship centrality (PageRank), log-rescaled to 0-1; most members sit low; "
               "null with too little data")


class _PersonDetailBase(CamelModel):
    """Shared detail-response shape for Senators and Representatives.

    House and Senate detail responses are identical except for the House-only
    `district` (added by RepresentativeSchema), so the common fields live here
    once — previously they were maintained field-for-field in two places and
    could silently drift.
    """
    id: str
    name: str
    bioguide_id: str | None = Field(None, description="The member's Biographical Directory id (Congress.gov's)")
    state: str
    party: Literal["D", "R", "I"]
    years_in_office: int = Field(description="Whole years in this chamber")
    initials: str
    leadership_title: str | None = Field(None, description="A party leadership post, if the member holds one")
    committees: list[CommitteeSchema] = []
    representation_score: RepresentationScoreSchema
    funding: FundingSchema
    voting_record: VotingRecordSchema
    lobbying_matches: list[LobbyingMatchSchema] = Field(
        description="Donors who also lobby, with the bills they lobbied on that the member voted on")
    campaign_promises: list[CampaignPromiseSchema] = Field(
        [], description="Always empty: campaign-promise tracking was removed in 2026-07 (see "
                        "representationScore.promisePersistence). Kept so existing clients still find the field")
    partisan_depth: PartisanDepthSchema | None = Field(
        None, description="How strongly the member's roll-call votes lean to one party, by policy area (votes, "
                          "not cosponsorship: see ideologyLabel for that)")
    sponsored_bills: list[SponsoredBillSchema] = Field([], description="Bills the member sponsored this Congress")
    leadership_score: float | None = Field(None, description=_LEADERSHIP)
    bipartisanship_score: float | None = Field(
        None, description="Cross-party cosponsorship, given and received (after the Lugar Center's Bipartisan "
                          "Index), 0-1: 0.5 is the chamber median, 1 twice it or more; null with too little data")
    ideology_score: float | None = Field(None, description=_IDEOLOGY)
    sponsorship_description: str = Field(
        "", description="Ideology and leadership in words, both from cosponsorship (ideologyScore, "
                        "leadershipScore); not the voting record's partisanDepth")
    constituent_approval: ConstituentApprovalSchema | None = Field(
        None, description="How the member's constituents rated them in the Cooperative Election Study, by party")
    website_url: str = ""
    contact_form_url: str = ""
    algorithm_version: str | None = Field(
        None, description="The scoring version that stored the member's scores (the newest history snapshot's); "
                          "null when none is recorded")
    scored_on: str | None = Field(
        None, description="When the member's stored scores were computed, YYYY-MM-DD; null when none is recorded")
    office_phone: str = ""
    office_address: str = ""


class SenatorSchema(_PersonDetailBase):
    pass


class RepresentativeSchema(_PersonDetailBase):
    """The shared person-detail shape plus the House-only district."""
    district: int


class PaginatedRepresentativesSchema(CamelModel):
    entries: list[RepresentativeSchema]
    total: int
    page: int
    per_page: int
    total_pages: int


class ScoreTrendSchema(CamelModel):
    """The overall score against the member's score about a week earlier."""
    direction: Literal["up", "down", "stable", "new", "reset"] = Field(
        "new", description='"new": no earlier score; "reset": earlier scores only under another scoring '
                           "version or Congress, so not comparable")
    change: float = Field(0.0, description="Points since the earlier score; 0 when new or reset")
    previous_score: float | None = Field(None, description="The earlier overall score; null when new or reset")


class LeaderboardEntrySchema(CamelModel):
    id: str
    name: str
    state: str
    party: Literal["D", "R", "I"]
    years_in_office: int = Field(description="Whole years in this chamber")
    initials: str
    representation_score: RepresentationScoreSchema
    total_raised: float = Field(description="All receipts of the most recent completed election campaign, in dollars")
    total_contributions: float | None = Field(
        None, description="Its contributions (and candidate loans), in dollars: what the shares are taken over")
    total_from_pacs: float = Field(description="From PACs, in dollars")
    pac_share_pct: float = Field(0.0, description="PAC money as a percentage of contributions, 0-100")
    small_donor_percentage: float | None = Field(
        None, description="Share of contributions in unitemized gifts of $200 or less, 0-100; null when unknown")
    top_industry: str | None = Field(
        None, description="The largest industry in the profile's funding.industryBreakdown, as words; small "
                          "donors, unattributed individuals, party and candidate committees and unclassified "
                          "money are not industries and never this; null when there is none")
    trend: ScoreTrendSchema = Field(default_factory=ScoreTrendSchema)
    # SVD-based, cosponsorship-derived (Tauberer 2012) — 0 = most-left,
    # 1 = most-right, computed without party labels as input. None when
    # too little cosponsorship data exists to compute it (see
    # sponsorship_analysis.compute_ideology_scores).
    ideology_score: float | None = Field(None, description=_IDEOLOGY)
    # Backend-computed via sponsorship_analysis.describe_senator_position —
    # frontend must never re-derive this from ideology_score itself, since
    # the party-relative bucketing (D/R use a 30/70 split, independents
    # 35/65) isn't reproducible from the number alone.
    ideology_label: str | None = Field(
        None, description="ideologyScore in words, relative to the member's own party: from cosponsorship, not "
                          "votes, so it can differ from partisanDepth.depth, which reads the voting record")
    # PageRank cosponsorship centrality (sponsorship_analysis.
    # compute_leadership_scores), log-rescaled to [0, 1] to counter its
    # power-law distribution — most members cluster low, a few attract
    # disproportionate cosponsor weight. None when too little
    # cosponsorship data exists to compute it.
    leadership_score: float | None = Field(None, description=_LEADERSHIP)
    # score_display.thin_dimensions: shown on the leaderboard so a rank built
    # on a shrunk-to-neutral dimension isn't read as a measurement.
    thin_data: list[str] = Field(
        default_factory=list,
        description="Weighted dimensions (keys of representationScore) whose confidence is \"low\": too little "
                    "source data for the dimension to be more than a pull toward a neutral 50")


# --- Public API v1 (api/public.py) ---
#
# Documentation of the public contract, not how it's built: those routes
# return JSONResponse directly, which FastAPI never validates against a
# response_model, so these could drift from the real bodies unnoticed.
# extra="forbid" plus tests/test_public_api_contract.py, which validates
# every endpoint's actual output against these, is what stops that — a
# field added to a response without being added here fails the test.


class _PublicModel(CamelModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


_SITE_URL = Field(description="This record's page on Civitas")
_RANK = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED + "Place in the whole chamber by overall score, whatever the filters, as the site shows it: "
                          "scores are compared as displayed, rounded to a whole number, and members who show the "
                          "same score share a rank (1, 2, 2, 4)")


class PublicLicenseSchema(_PublicModel):
    name: str = Field(description="The license, in words")
    url: str = Field(description="Where it is stated, with each source whose terms carry over")


class PublicApiIndexSchema(_PublicModel):
    name: str
    version: str
    rate_limit: str = Field(description="The rate limit, in words")
    score_weights: dict[str, float] = Field(description="How the sub-scores weigh into representationScore.overall")
    endpoints: dict[str, str] = Field(description='"METHOD path" -> what it returns')
    docs: str = Field(description="The documentation page")
    openapi: str = Field(description="This API's OpenAPI 3 description")
    mcp: str = Field(description="MCP server (streamable HTTP) exposing these endpoints as tools")
    source: str = Field(description="Civitas's source code (AGPL-3.0)")
    license: PublicLicenseSchema = Field(description="The license of the data this API serves")


class PublicStateSchema(_PublicModel):
    code: str = Field(description="Two-letter state code")
    name: str
    senator_count: int = Field(description="Serving senators")
    representative_count: int = Field(description="Serving representatives (delegates aren't scored)")


class PublicLeanInputSchema(_PublicModel):
    name: str
    url: str = Field(description="Where it is published (a DOI, a pinned commit, or a file in this repository)")
    version: str = Field(description="The version read")
    license: str
    use: str = Field(description="What the computation takes from it")


class PublicLeanCorrectionSchema(_PublicModel):
    state: str
    county: str | None = Field(description="The county's FIPS code; null for a correction that isn't one county's")
    county_name: str | None
    action: str = Field(description="What was done (county_total, municipal_totals, drop_rows, verify ...)")
    why: str = Field(description="The error in the precinct file, and why the replacement is exact")
    official_source: str = Field(description="The certified canvass the correction is taken from")
    official_url: str


class PublicLeanValidationSchema(_PublicModel):
    congress: int = Field(description="The Congress whose district lines were compared")
    districts: int = Field(description="Districts compared")
    r: float = Field(description="Pearson correlation with Cook's published PVI")
    mean_abs_diff: float = Field(description="Mean absolute difference from Cook's, in points")
    rounded_equal: int = Field(description="Districts whose figure rounds to Cook's whole number")
    within_one: int = Field(description="Districts within one point of Cook's")
    max_abs_diff: float = Field(description="Largest difference from Cook's, in points")


class PublicLeanUnavailableSchema(_PublicModel):
    congress: int
    district: str
    reason: str


class PublicLeanDistrictSchema(_PublicModel):
    district: str = Field(description='"ST-N"; N is 0 for an at-large seat')
    state: str
    number: int
    computed_lean: float = Field(description="Civitas's figure, one decimal; positive = Republican lean")
    cook_pvi: int | None = Field(description="Cook's published PVI for the same lines (whole points, positive = "
                                             "Republican); null where none is on file for those lines")


class PublicLeanCongressSchema(_PublicModel):
    congress: int = Field(description="The Congress elected on these district lines (the Nth Congress is "
                                      "elected in 1786 + 2N; a state that redrew between two Congresses has "
                                      "different lines in each)")
    districts: list[PublicLeanDistrictSchema]


class PublicDistrictLeanSchema(_PublicModel):
    source: str = Field(description="Where the figures come from, as a citation")
    method: str = Field(description="The formula and how votes were placed in districts")
    sign: str
    window: str = Field(description="The elections the figure is computed from")
    as_of: str = Field(description="When the table was built, YYYY-MM-DD")
    sha256: str | None = Field(description="SHA-256 of the data file served: names the exact version cited")
    regenerate: str = Field(description="The script in this repository that rebuilds the table")
    inputs: list[PublicLeanInputSchema] = Field(description="Every input, with its version and license")
    corrections: list[PublicLeanCorrectionSchema] = Field(
        description="Each error in the precinct files replaced from a certified canvass")
    against_cook: list[PublicLeanValidationSchema] = Field(description="Agreement with Cook's PVI, per map")
    unavailable: list[PublicLeanUnavailableSchema] = Field(
        description="Districts left without a figure, and why (none is filled in from elsewhere)")
    congresses: list[PublicLeanCongressSchema] = Field(description="Every district on each map, in state order")
    csv_url: str = Field(description="The same districts as CSV")
    site_url: str = _SITE_URL


class PublicSenatorRowSchema(LeaderboardEntrySchema):
    model_config = _PublicModel.model_config
    rank: int = _RANK
    site_url: str = _SITE_URL


class PublicRepresentativeRowSchema(LeaderboardEntrySchema):
    model_config = _PublicModel.model_config
    rank: int = _RANK
    district: int = Field(description="Congressional district; 0 for an at-large seat")
    site_url: str = _SITE_URL


_ALGORITHM_VERSION = Field(description="The scoring version that stored the member's scores (the newest "
                                        "history snapshot's), as the changelog names it; null when none is "
                                        "recorded")
_SCORED_ON = Field(description="When the member's stored scores were computed, YYYY-MM-DD (the newest "
                               "history snapshot); null when none is recorded")


class PublicSenatorProfileSchema(SenatorSchema):
    model_config = _PublicModel.model_config
    site_url: str = _SITE_URL
    algorithm_version: str | None = _ALGORITHM_VERSION
    scored_on: str | None = _SCORED_ON


class PublicRepresentativeProfileSchema(RepresentativeSchema):
    model_config = _PublicModel.model_config
    site_url: str = _SITE_URL
    algorithm_version: str | None = _ALGORITHM_VERSION
    scored_on: str | None = _SCORED_ON


class _PublicPage(_PublicModel):
    total: int = Field(description="Matching members across every page")
    page: int = Field(description="This page; a page past the last is answered as the last")
    per_page: int
    total_pages: int = Field(description="At least 1, even when nothing matches")


class PublicSenatorPageSchema(_PublicPage):
    entries: list[PublicSenatorRowSchema]


class PublicRepresentativePageSchema(_PublicPage):
    entries: list[PublicRepresentativeRowSchema]


class PublicPartySplitSchema(_PublicModel):
    party: str = Field(description="D, R, I or another party code as the chamber recorded it")
    yea: int
    nay: int
    present: int
    not_voting: int


class PublicTieBreakerSchema(_PublicModel):
    by_whom: str = Field(description="Who broke the tie, as Senate.gov records it "
                                     "(\"Vice President of the United States\")")
    vote: str = Field(description="How the tie was broken: Yea or Nay")


class PublicCandidateVotesSchema(_PublicModel):
    name: str = Field(description="The candidate, as the House Clerk records the vote (e.g. \"Doe (TX)\": a surname, with the state where two members share it)")
    votes: int


class PublicRollCallSchema(_PublicModel):
    chamber: Literal["senate", "house"]
    congress: int
    session: int
    number: int = Field(description="The roll call's number in its chamber and session")
    date: str = Field(description="YYYY-MM-DD")
    kind: Literal["question", "election", "quorum"] = Field(
        description="question: a Yea/Nay vote on a question. election: an election of the Speaker, where each "
                    "vote names a candidate (see candidates; the yea and nay counts are 0). quorum: a quorum "
                    "call, where members answer present and nothing is decided (result is the chamber's field, "
                    "but no question carried or failed)")
    question: str = Field(description="The question voted on, as the chamber recorded it")
    result: str = Field(description="The result, as the chamber recorded it. For an election of the Speaker "
                                    "the House Clerk names the candidate with the most votes, whether or not "
                                    "that candidate won the majority that elects")
    tie_breaker: PublicTieBreakerSchema | None = Field(
        description="A Senate tie broken by the Vice President, who is not a member: the vote is in no "
                    "member's position, so the yeas and nays are equal and this says how the tie was broken. "
                    "Null when the record names none")
    candidates: list[PublicCandidateVotesSchema] = Field(
        description="An election of the Speaker: each candidate's votes, most first; empty for any other vote")
    source_url: str = Field(description="The roll call at its official source (Senate.gov or the House Clerk); "
                                        "empty when not recorded")
    site_url: str = Field(description="The roll call's page on Civitas, with every member's position (the same "
                                      "as /roll-calls/{chamber}/{congress}/{session}/{number})")
    parties: list[PublicPartySplitSchema] = Field(description="How each party voted (an election's votes for "
                                                              "candidates are in candidates, not here)")


class PublicMemberVoteSchema(_PublicModel):
    bill_id: str | None = Field(description="The measure voted on (e.g. HR.1234), as Civitas names it; null for a "
                                            "roll call on no bill (a nomination, a quorum call, the election of "
                                            "the Speaker, most procedural motions)")
    bill_name: str = Field(description="The measure's short title where Congress.gov names one, else the "
                                       "chamber's title of the vote, else its question")
    date: str = Field(description="YYYY-MM-DD")
    vote: str = Field(description="Yea, Nay, Present or Not Voting (the House's Aye and No read as Yea and Nay); "
                                  "any other word as the chamber recorded it: Guilty or Not Guilty on an article "
                                  "of impeachment, the candidate's name in an election of the Speaker")
    voted_with_party: bool | None = Field(description="Whether the vote sided with the member's party's majority "
                                                      "on a roll call the parties split on (partyLeaning R or D), "
                                                      "read as the scoring run reads it: the member's caucus party, "
                                                      "and the majority leader's vote to be able to move to "
                                                      "reconsider is no signal. Null when the vote carries no "
                                                      "party signal (a bipartisan or housekeeping roll call, or a "
                                                      "vote that isn't Yea or Nay)")
    party_leaning: Literal["R", "D", "bipartisan"] | None = Field(
        description="How the parties split on it, from the roll call's own positions: R or D when 65% or more of "
                    "one party's Yea/Nay votes were Yea and 35% or fewer of the other's, bipartisan otherwise; "
                    "null for a housekeeping question (a quorum call, a motion to table or adjourn) or when either "
                    "party cast fewer than three Yea/Nay votes")
    counted_as_break: bool | None = Field(
        description="Whether Constituent Alignment counted this vote as a break with the party: a break toward the "
                    "other party, each measure once at its latest roll call (breaks from the party's flank and "
                    "repeat votes on one measure are not counted). The trues on every page are the score's whole "
                    "list. Null when the member has no party-line record")
    policy_area: str | None = Field(description="One of the index's policy areas (Civitas's classification), for the "
                                                "votes the scoring run stored with one; null for the rest")
    roll_call: PublicRollCallSchema = Field(description="The roll call this vote was cast in")


class PublicMemberVotesSchema(_PublicModel):
    id: str = Field(description="The member's id")
    congress: int = Field(description="The Congress the votes are from: the sitting one")
    chamber_roll_calls: int = Field(description="Roll calls Civitas holds for the member's chamber this Congress. "
                                                "total is lower by the votes taken before the member took office "
                                                "(or after they left)")
    total: int = Field(description="The member's positions on those roll calls, across every page")
    page: int = Field(description="This page; a page past the last is answered as the last")
    per_page: int
    total_pages: int = Field(description="At least 1, even when there are none")
    entries: list[PublicMemberVoteSchema] = Field(description="Newest first")


class PublicBillRollCallSchema(PublicRollCallSchema):
    yeas: int
    nays: int
    present: int
    not_voting: int


class PublicBillVotesSchema(_PublicModel):
    bill_id: str = Field(description="The bill, as Civitas names it (e.g. HR.1234)")
    congress: int
    covered: bool = Field(description="Whether Civitas holds this Congress's roll calls. False: entries is empty "
                                      "because the votes aren't held here, not because the bill had no recorded "
                                      "vote; the chambers' own records (Senate.gov, the House Clerk) have them")
    held_congresses: list[int] = Field(description="The Congresses whose roll calls Civitas holds, oldest first")
    site_url: str = _SITE_URL
    entries: list[PublicBillRollCallSchema] = Field(description="Every roll call on the bill Civitas holds, "
                                                                "newest first. Empty with covered true: the "
                                                                "bill has had no recorded vote")


class PublicSnapshotFactsSchema(_PublicModel):
    small_gifts: float | None = Field(description="Share of contributions in gifts of $200 or less, 0-1")
    pac_money: float | None = Field(description="Share of contributions from PACs, 0-1")
    breaks: float | None = Field(description="Breaks with the party over party-line votes, 0-1")
    bills_moved: float | None = Field(description="Bills sponsored this Congress that got past committee")
    party_votes: int | None = Field(description="Party-line votes behind breaks")
    break_count: int | None = Field(description="Breaks with the party")
    bills: int | None = Field(description="Bills sponsored this Congress")


class PublicScoreSnapshotSchema(_PublicModel):
    date: str = Field(description="When the scores were computed, YYYY-MM-DD")
    overall: float = Field(description=_DEPRECATED + "0-100, like the sub-scores here; read `facts` instead",
                           json_schema_extra=_DEPRECATED_FIELD)
    funding_independence: float = Field(description=_DEPRECATED_PART + "0-100", json_schema_extra=_DEPRECATED_FIELD)
    promise_persistence: float | None = Field(None, description="Always null: see representationScore")
    constituent_alignment: float = Field(description=_DEPRECATED_PART + "0-100", json_schema_extra=_DEPRECATED_FIELD)
    funding_diversity: float = Field(description=_DEPRECATED_PART + "0-100", json_schema_extra=_DEPRECATED_FIELD)
    legislative_effectiveness: float = Field(description=_DEPRECATED_PART + "0-100",
                                             json_schema_extra=_DEPRECATED_FIELD)
    algorithm_version: str | None = Field(description="The scoring version that computed this snapshot; a "
                                                      "change between two snapshots is a method change, not "
                                                      "only a change in the record. Null for snapshots taken "
                                                      "before versions were recorded")
    facts: PublicSnapshotFactsSchema | None = Field(
        description="The measures the site compares members on, as they stood that day (the history to read "
                    "in place of overall); null for snapshots recorded before 2026-10-19")


class PublicHistorySchema(_PublicModel):
    id: str = Field(description="The member's id")
    snapshots: list[PublicScoreSnapshotSchema] = Field(description="Oldest first")


class PublicSearchResultSchema(_PublicModel):
    id: int = Field(description="The document's row id on Civitas. Not permanent: link and cite by key")
    key: str = Field(description="The document's permanent id, from its official identifier: \"fr-2026-17123\" "
                                 "(Federal Register document number), \"crec-\" and the Congressional Record "
                                 "granule for a floor speech, \"scotus-\" and the docket for an opinion. "
                                 "Its page is siteUrl, /explore/{key}")
    title: str
    date: str = Field(description="YYYY-MM-DD")
    doc_type: str = Field(description="One of the doc_type filter's values")
    source: str
    politician_name: str = Field(description="The member who delivered or signed it; empty when none")
    politician_id: str = Field(description="Their Civitas id; empty when none")
    chamber: str = Field(description="Senate, House, Executive, Judicial or Regulatory (the chamber filter's "
                                     "values, capitalized)")
    agency_name: str = Field(description="The issuing agency, for a Federal Register document; empty otherwise")
    agency_slug: str = Field(description="That agency's Civitas id, as in /agencies/{slug} (the Federal Register's own "
                                         "slug for it); empty when there is none or it has not been read yet")
    url: str = Field(description="The document at its official source")
    site_url: str = _SITE_URL
    summary: str
    comment_url: str = Field(description="Where to comment while the comment period is open (commentsCloseOn is "
                                         "today or later, Eastern time); empty otherwise")
    comments_close_on: str = Field(description="When the document's comment period closes or closed, YYYY-MM-DD; "
                                               "empty when it has none")
    cited_by_count: int = Field(description="How many indexed documents cite this one")
    matched_by: list[Literal["semantic", "keyword", "identifier"]] = Field(description="Which search found it: by meaning, by exact words, and/or as the document a citation or document number in the query names (\"91 FR 48248\", \"2026-15603\"), which is listed first")
    distance: float | None = Field(description="Distance in meaning from the query; null when only the exact words matched")
    snippet: str = Field(description="Text around the matched words, plain")
    duplicate_count: int = Field(description="Near-identical copies folded into this result")


class PublicNotHeldSchema(_PublicModel):
    identifier: str = Field(description="The identifier as it is written, e.g. \"Executive Order 14148\"")
    sentence: str = Field(description="Why it isn't here: before the corpus starts, or not read yet")
    url: str = Field(description="Its official source")
    source: str = Field(description="That source's name")


class PublicSearchResponseSchema(_PublicModel):
    query: str
    results: list[PublicSearchResultSchema] = Field(description="Best match first")
    count: int = Field(description="Results returned (at most the request's limit), not a total")
    partial: bool = Field(description="True when only the exact-words search could answer, so the ranking is incomplete")
    index_building: bool = Field(description="True while the search index is being built; results are empty until it is")
    not_held: list[PublicNotHeldSchema] = Field(description="Each identifier the query names (an executive order, FR "
                                                         "citation or document number, RIN, docket, Congressional "
                                                         "Record citation) that no document here is known by, with "
                                                         "where its official copy is. Results, if any, cite it")


_BREAKDOWN_RULE = (
    "score = clamp(round(sum(w * s for mean components) / sum(w for mean components) "
    "+ sum(w * (s - 50) for points components)), 0, 100), over the components whose score is not null; "
    "a neutral 50 when none has one (note says why)"
)


class PublicScoreComponentSchema(_PublicModel):
    label: str = Field(description="The component, in words")
    weight: float = Field(description="Its weight within the dimension (rounded to 4 decimals)")
    score: float | None = Field(description="0-100, rounded to one decimal; null when it could not be measured, "
                                            "and then it is left out and the others carry its weight")
    combines: Literal["mean", "points"] = Field(
        description="mean: takes part in the weighted mean; points: adds weight x (score - 50) points to it")
    points: float | None = Field(
        None, description="A points component: the points it adds to the weighted mean (negative: takes away), "
                          "weight x (score - 50) to one decimal; absent otherwise")
    detail: str = Field(description="How the component was worked out, with the member's figures and the "
                                    "reference values it was compared against")


class PublicSinceScoredSchema(_PublicModel):
    score: int = Field(description="The dimension's score recomputed now on today's records")
    bills_changed: int | None = Field(
        description="Legislative Effectiveness: how many bills reached a new furthest stage (or were introduced) "
                    "after the run; null for other dimensions, or when the change is not in the bills")
    scored_at: str | None = Field(description="When the run worked the stored score out, UTC (YYYY-MM-DDTHH:MMZ)")


class PublicScoreDimensionSchema(_PublicModel):
    stored_score: float = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED_PART + "The dimension's score on the record (representationScore), "
                                            "from the last scoring run")
    score: int = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED_PART + "The score the components below give, by: " + _BREAKDOWN_RULE
                                   + ". With basis \"scored\" they are the components the run that stored the "
                                   "score worked out, so this equals storedScore")
    sum: float | None = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED_PART + "The components combined by that rule before rounding and the 0-100 "
                                          "clamp, from the components as published (one decimal); null when none "
                                          "was measured")
    clamped: bool = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED_PART + "True when sum fell outside 0-100 and the score was held at the bound")
    basis: Literal["scored", "recomputed"] = Field(
        description="scored: the components the run that stored the score worked out, kept with it. recomputed: "
                    "worked out now from the stored inputs with recomputedWith's code, for a score stored before "
                    "its run's components were kept; it can then differ from storedScore")
    since_scored: PublicSinceScoredSchema | None = Field(
        json_schema_extra=_DEPRECATED_FIELD,
        description=_DEPRECATED_PART + "Set when the score, recomputed now on today's records, would differ: records changed after "
                    "the run (a bill's stage, say), which the next nightly run scores. Null when it would not, or "
                    "basis is recomputed")
    weight_in_overall: float | None = Field(
        json_schema_extra=_DEPRECATED_FIELD,
        description=_DEPRECATED_PART + "Its weight in representationScore.overall; null for an informational dimension")
    components: list[PublicScoreComponentSchema] = Field(
        json_schema_extra=_DEPRECATED_FIELD,
        description=_DEPRECATED_PART + "Component scores are rounded to one decimal; the score is worked out from unrounded ones, "
                    "so recomputing from these lands within 0.1 of a value that rounds to score")
    note: str | None = Field(description="Why the dimension is a neutral 50, when no component could be measured")
    finding: str | None = Field(description="The one-sentence finding the profile states")
    facts: dict[str, Any] | None = Field(
        description="The figures the profile's sentences are written from, as numbers (shares 0-1): funding "
                    "shares, the party-line votes and every break counted, bills by furthest stage")


class PublicScoreDimensionsSchema(_PublicModel):
    funding_independence: PublicScoreDimensionSchema
    constituent_alignment: PublicScoreDimensionSchema
    funding_diversity: PublicScoreDimensionSchema
    legislative_effectiveness: PublicScoreDimensionSchema


class PublicSeatSchema(_PublicModel):
    state: str
    district: int | None = Field(description="House district, 0 at-large; null for senators")
    cook_pvi: int = Field(description="The seat's Cook PVI the score used, positive leaning Republican (the "
                                      "district's for a House seat, the state's for a senator; 0 when unknown)")
    district_lines_congress: int | None = Field(
        description="The Congress whose district lines the House score was computed on; null for senators, or "
                    "a score stored before this was recorded")
    computed_lean: float | None = Field(
        description="Civitas's own lean for the House district on those same lines, positive leaning Republican, "
                    "one decimal: Cook's formula worked out from open precinct returns (the elections and sources "
                    "are named in the dataset's own metadata), "
                    "for comparison with cook_pvi. Not used in any score; null for senators or lines with no "
                    "computed table")


class PublicScoreReferencesSchema(_PublicModel):
    funding: dict[str, Any] | None = Field(
        description="Funding Independence's chamber reference as the last run measured it from the members it "
                    "scored (PAC-share fit by campaign size, small-donor baselines, donor and industry "
                    "concentration deciles; computed_at UTC)")
    constituent: dict[str, Any] | None = Field(
        description="Constituent Alignment's chamber reference: per party, the fitted expected break rate by "
                    "seat lean (logit a + b x lean, b_opposed for seats leaning to the other party, fitted only "
                    "when it predicts better out of sample: kink records the comparison), its scale and the "
                    "party's typical score")
    legislative: dict[str, Any] | None = Field(
        description="Legislative Effectiveness's chamber reference: bill totals per stage, median credit by "
                    "majority/minority status, advancement rates and the majority party")
    positions: dict[str, Any] | None = Field(
        description="Position congruence's inputs: the roll-call ideal-point measure and Congress, the per-party "
                    "fit of position on seat lean, the chamber's extremity scale (extremity_p90) and reliability, "
                    "and this member's position and scaled vote count (null when Voteview has none)")
    seat: PublicSeatSchema
    legislative_bills: str | None = Field(
        description="Where Legislative Effectiveness's bills came from in the stored score: \"fetched:<date>\" "
                    "(read that day), \"carried:<date>\" (that run couldn't read them; the bills read on that date) "
                    "or \"unavailable\" (none known, so the bill part is not measured); null for a score stored "
                    "before this was recorded, which was scored on its bills as fetched")


class PublicPeerGroupSchema(_PublicModel):
    key: Literal["caucus", "chamber"] = Field(description="caucus: the chamber's members who caucus with the "
                                                          "member's party (independents with theirs); chamber: "
                                                          "every serving member")
    label: str = Field(description='The group as the page names it ("Senate Democrats and independents")')
    n: int = Field(description="Members in the group with a figure for this measure, the member included")


class PublicPeerSourceSchema(_PublicModel):
    name: str
    url: str


class PublicPeerMeasureSchema(_PublicModel):
    key: Literal["smallGifts", "pacMoney", "breaks", "billsMoved"]
    label: str
    question: str = Field(description='The voter question the measure answers ("Who funds the campaign?")')
    why: str = Field(description="What the figure is and why a voter might care, in one fixed, neutral line")
    measured: str = Field(description="How the figure is measured, for researchers: definitions, window, caveats")
    window: Literal["congress", "election"] = Field(description="The span the figure covers: this Congress, or "
                                                                "the member's most recent completed election")
    direction: str = Field(description="Which way is higher, in words; the word is neutral about which is better")
    unit: Literal["share", "rate", "count"] = Field(description="share and rate are 0-1; count is bills")
    value: float | None = Field(description="The member's figure; null when the record has none")
    shown: str | None = Field(description='The figure as the page prints it ("13%", "0 of 493", "9 of 89")')
    expected: float | None = Field(description="The figure expected: the scorer's seat-adjusted reference where it "
                                               "has one, else the peer median (expectedBasis)")
    expected_basis: Literal["seat", "peer median"]
    median: float | None = Field(description="The peer group's median")
    peer_group: PublicPeerGroupSchema
    distribution: list[float] = Field(description="Every peer's figure, the member's included, ascending")
    distribution_ids: list[str] = Field(description="The member id behind each distribution value, same order")
    distribution_names: list[str] = Field(description="The member's name behind each distribution value, same order")
    record: int | None = Field(description="How much record backs the figure (party-line votes for breaks, bills "
                                           "sponsored for bills moved, 1 when there are contributions)")
    source: PublicPeerSourceSchema
    percentile: float | None = Field(description="Mid-rank percentile among the peers, 0-100")
    word: str | None = Field(description='"well above peers", "above peers", "near peers", "below peers" or "well '
                                         'below peers", by rule; null when thin')
    sentence: str | None = Field(description="One template sentence: the fact and how many peers sit beyond it")
    thin: bool = Field(description="Too little record for a word: fewer than rule.minRecord peers or party-line "
                                   "votes, or a dimension the scorer graded low")
    gap_sd: float | None = Field(default=None, description="(value - expected) / the peers' standard deviation")


class PublicPeerRuleSchema(_PublicModel):
    well_outside: list[float] = Field(description="Percentiles outside which a word can be a 'well' word")
    outside: list[float] = Field(description="Percentiles outside which the word is above or below")
    well_gap_sd: float = Field(description="Distance from expected, in peer standard deviations, a 'well' word "
                                           "also needs")
    min_record: int = Field(description="Smallest peer group and party-line record a word is drawn from")


class PublicPeerComparisonSchema(_PublicModel):
    group: Literal["caucus", "chamber"]
    rule: PublicPeerRuleSchema = Field(description="The word's cut points: conventions, not fitted values")
    measures: list[PublicPeerMeasureSchema]
    stands_out: str = Field(description="The one line above the measures: the furthest 'well' row, or that the "
                                        "member is close to peers on every measure")
    stands_out_why: str = Field(description="Why that line was picked: the rule, and the measure's own why line")


class PublicScoreBreakdownSchema(_PublicModel):
    id: str = Field(description="The member's id")
    name: str
    chamber: Literal["senate", "house"]
    party: str
    site_url: str = _SITE_URL
    algorithm_version: str | None = _ALGORITHM_VERSION
    scored_on: str | None = _SCORED_ON
    recomputed_with: str = Field(description="The scoring version today's recompute used (sinceScored, and a "
                                             "dimension whose basis is recomputed)")
    overall: float = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED + "representationScore.overall: sum(weights[k] x dimensions[k].storedScore), "
                                       "rounded to 2 decimals")
    weights: dict[str, float] = Field(json_schema_extra=_DEPRECATED_FIELD,
                                      description=_DEPRECATED_PART + "Each weighted dimension's weight in overall "
                                                                     "(they sum to 1)")
    dimensions: PublicScoreDimensionsSchema
    references: PublicScoreReferencesSchema = Field(
        description="The population and seat reference values the components were compared against, as the "
                    "last run wrote them")
    peer_comparison: PublicPeerComparisonSchema | None = Field(
        description="Where the member stands among peers on each measure (the site's headline in place of the "
                    "composite); null for a member not serving")


class PublicPolicyStatementSchema(_PublicModel):
    title: str = Field(description="The statement's title as OMB lists it")
    url: str = Field(description="The signed statement (PDF) on whitehouse.gov")
    issued: str = Field(description="Date issued, YYYY-MM-DD")
    position: Literal["support", "oppose"] = Field(description="The position on the measure read from the statement")
    sentence: str = Field(description="The statement's sentence the position was read from, verbatim")


class PublicPresidentialSupportRollCallSchema(_PublicModel):
    chamber: Literal["senate", "house"]
    congress: int
    session: int
    roll_call: int = Field(description="Roll call number within the session")
    date: str = Field(description="YYYY-MM-DD")
    question: str = Field(description="The question as the chamber recorded it")
    title: str
    bill_id: str | None = Field(description='The measure, as the site writes it ("HR.9340")')
    result: str = Field(description="The result as the chamber recorded it")
    source_url: str = Field(description="The chamber's own roll-call record")
    president_side: Literal["Yea", "Nay"] = Field(description="The vote on the president's side of this question")
    statement: PublicPolicyStatementSchema


class PublicPresidentialSupportMemberSchema(_PublicModel):
    id: str
    name: str
    party: str = Field(description="The party the expectation is read for (an independent's caucus party)")
    state: str
    district: int | None = Field(description="House district, 0 at-large; null for senators")
    seat_lean: int | None = Field(description="The seat's Cook PVI, positive leaning Republican")
    with_president: int = Field(description="Counted votes cast on the president's side")
    votes: int = Field(description="Counted votes cast (Yea or Nay; present and not voting excluded)")
    rate: float = Field(description="withPresident / votes")
    rate_low: float = Field(description="Lower end of the rate's 95% Wilson score interval")
    rate_high: float = Field(description="Upper end of the rate's 95% Wilson score interval")
    expected: float | None = Field(description="What members of the same party in seats of the same lean vote "
                                               "with the president, by this Congress's fit; null when none was fitted")
    difference: float | None = Field(description="rate - expected")
    difference_low: float | None = Field(description="rateLow - expected")
    difference_high: float | None = Field(description="rateHigh - expected")
    compared_with_expected: Literal["more", "less", "within"] | None = Field(
        description="Whether the rate's interval lies above the expected rate (more), below it (less), or includes it")
    site_url: str = _SITE_URL


class PublicPresidentialSupportFitSchema(_PublicModel):
    a: float = Field(description="Intercept, log-odds")
    b: float = Field(description="Slope on party-signed seat alignment (-1..1)")
    b_opposed: float = Field(description="Extra slope for seats leaning to the other party (0 when not fitted)")
    members: int = Field(description="Members in the fit")
    votes: int = Field(description="Their counted votes (the fit's weights)")


class PublicPresidentialSupportMethodSchema(_PublicModel):
    source: str
    source_url: str = Field(description="OMB's index of Statements of Administration Policy")
    counted: str = Field(description="Which roll calls count")
    position: str = Field(description="How each statement's position is read")
    expected: str = Field(description="How the expected rate is measured")
    interval: str = Field(description="The uncertainty shown")
    neutral: str
    fits: dict[str, PublicPresidentialSupportFitSchema] = Field(description="Per party (D, R), this run's fit")
    curves: dict[str, list[list[float]]] = Field(
        description="Per party, [seat lean, expected rate] along the fit, seat lean from -30 to +30 (PVI, + leans Republican)")
    computed_at: str | None = Field(description="When the fit was measured (UTC)")


class PublicPresidentialSupportSchema(_PublicModel):
    chamber: Literal["senate", "house"]
    congress: int
    members: list[PublicPresidentialSupportMemberSchema] = Field(description="Serving members with a counted vote, by name")
    roll_calls: list[PublicPresidentialSupportRollCallSchema] = Field(description="Counted roll calls, newest first")
    method: PublicPresidentialSupportMethodSchema


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


class PipelineLinkSchema(CamelModel):
    """One link of the nightly chain, as its latest run left it.

    `state` is the reader's word for the row, decided by the API:
    "completed", "partial" (some members failed), "running" (a live run)
    or "failed" (anything else: a failed or stale row, or a RUNNING row
    whose run is dead)."""
    pipeline: str
    label: str
    state: str
    run_id: int
    started_at: datetime
    completed_at: datetime | None = None


class PipelineStatusSchema(CamelModel):
    last_run: PipelineRunSchema | None = None
    next_scheduled: str | None = None
    is_running: bool = False
    # Each chain link's latest run, in the order the chain runs them. The
    # records band reads these, not `last_run` (the Senate's alone), so one
    # failed link is named as that link rather than as the whole site.
    links: list[PipelineLinkSchema] = []
    links_completed: int = 0
    # The newest completion among the links that completed, or None.
    filed_at: datetime | None = None


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
    # Nullable (2026-07): None means this dimension is genuinely
    # inapplicable for this president (e.g. Public Mandate for one who
    # never won a presidential election) — never a hand-set fallback. See models.py
    # President's comment and president_scorer.py's
    # compute_president_overall_score for the renormalization this drives.
    public_mandate: float | None
    effectiveness: float | None
    # C-SPAN Presidential Historians Survey, z-scored (2026-07) — None for
    # any currently-serving or just-departed president (survey only rates
    # a completed term; the 2025 cycle was postponed entirely).
    historical_legacy: float | None
    # Backend-computed overall (president_scorer.compute_president_overall_score).
    overall: float = 0.0
    # How many of the 3 possible dimensions actually have a score (0-3) —
    # see president_scorer.dimensions_available. A composite built from
    # fewer signals shouldn't be read with the same confidence as one
    # built from all 3; this lets the UI disclose that plainly.
    dimensions_available: int = 0
    # Each scored dimension's actual share of `overall`
    # (president_scorer.president_effective_weights): what the scorecard
    # shows beside each score, so a president missing a dimension sees the
    # weights their score was really computed with.
    effective_weights: dict[str, float] = {}


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
    # Election-margin percentage (average across this president's own
    # election win(s)) — the pre-polling-era Public Mandate proxy. See
    # app.pipeline.fetch.presidential_elections.
    election_margin: float | None = None
    # Raw C-SPAN 2021 Presidential Historians Survey point total (e.g.
    # Lincoln=897) — see app.pipeline.fetch.cspan_historians_survey.
    historical_legacy_score: int | None = None
    # Average approval over a rolling last-90-days window rather than the
    # full term — informational, not part of any scored dimension. Null
    # once a president leaves office and no new polls populate it. See
    # app.pipeline.fetch.presidential_approval.recent_polls.
    recent_avg_approval: float | None = None


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
    # Always null since justice v3: no justice is scored, because no method
    # yet separates loyalty to the appointing president from career timing
    # for an individual justice (docs/research/justice-scores.md). Null
    # means "not scored", never 0.
    loyalty: float | None = None
    overall: float | None = None


class JusticeLoyaltySchema(CamelModel):
    """The appointing president's estimated effect, shown and not scored:
    points more often for the government while the appointing president is
    in office (a share, the justice's own unshrunk estimate), its standard
    error and 95% confidence interval, the votes under the appointing
    president and under others with the share of each for the government,
    and the Supreme Court Database term the record runs through."""
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    votes_in: int
    votes_out: int
    rate_in: float
    rate_out: float
    through_term: int | None = None


class JusticeAgreementSchema(CamelModel):
    id: str
    name: str
    share: float


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
    # Agreement with each sitting justice, most first: the share of cases
    # both decided that they decided the same way.
    agreement: list[JusticeAgreementSchema] = []
    loyalty: JusticeLoyaltySchema | None = None
    # Martin-Quinn position per term, [[term, position], ...]: shown, not scored.
    ideal_points: list[tuple[int, float]] = []
    # The terms the voting record covers: those with decided cases stored.
    record_terms: list[int] = []


class JusticeLeaderboardEntry(CamelModel):
    """A sitting justice, listed by seniority and not ranked (justice v3)."""
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
    loyalty: JusticeLoyaltySchema | None = None


# ── Action Center ─────────────────────────────────────────────────

class RelatedExploreDoc(CamelModel):
    id: int
    # The document's permanent address, /explore/{key} (services/explore_keys.py).
    key: str | None = None
    title: str
    doc_type: str
    date: str
    url: str | None = None
    comment_url: str | None = None
    comments_close_on: str | None = None
    # The issuing agency, for a Federal Register document: who takes the
    # comments and decides the rule. agency_slug: its page (/agencies/...).
    agency_name: str | None = None
    agency_slug: str | None = None


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
    # The action-center blob already stores these (action_center._make_entry);
    # without declaring them here, RelatedSenator(**s) silently strips them and
    # the issue card's per-member "CONTACT" button always fell back to a generic
    # senate.gov link — wrong for House members in the list.
    contact_form_url: str | None = None
    website_url: str | None = None
    # The office's Washington phone, read when the issue is served.
    office_phone: str | None = None


class CommitteeLeaderSchema(CamelModel):
    id: str
    name: str
    party: str
    state: str
    chamber: str


class CommitteeContactSchema(CamelModel):
    """A contact_committee action's committee (app/committee_routing.py),
    with its chair and ranking member as the stored rosters name them now —
    looked up when the issue is served, so a leadership change shows on
    issues written before it."""
    code: str
    name: str
    chamber: str
    url: str | None = None
    chair: CommitteeLeaderSchema | None = None
    ranking_member: CommitteeLeaderSchema | None = None


class ActionItemSchema(CamelModel):
    text: str
    type: str = "general"
    url: str | None = None
    # comment_period: the Explore document and when its comment period
    # closes (served only while open, the #903 rule).
    doc_id: int | None = None
    # Its permanent key, /explore/{key} (services/explore_keys.py).
    doc_key: str | None = None
    closes_on: str | None = None
    # contact_committee
    committee: CommitteeContactSchema | None = None


class RelatedBillSchema(CamelModel):
    name: str
    id: str
    url: str
    # Path to our own bill detail page ("/congress/bills/HR.22") when we host this
    # bill; None means the congress.gov `url` is the only link available.
    internal_url: str | None = None
    # Where the bill stands, from the stored record of a bill a sitting
    # member sponsored (bill_stage.py); None when the site holds no record.
    stage_name: str | None = None
    latest_action: str | None = None
    latest_action_date: str | None = None


class DeciderSchema(CamelModel):
    """Who can act on an issue next, derived from a linked bill's stage or
    an open comment period's agency; never guessed from the story."""
    body: str
    basis: str


class IssueVoteSchema(CamelModel):
    """A roll call linked to an issue or a monitor: the vote the issue
    reports, a vote on a bill the coverage names, or a vote matched to the
    story (app/roll_call_links.py, "Related votes in Congress")."""
    chamber: str
    congress: int
    session: int
    number: int
    date: str
    question: str
    result: str
    bill_id: str | None = None
    bill_label: str | None = None
    # The measure or nominee as the record titles it.
    title: str = ""
    yeas: int = 0
    nays: int = 0
    # The chamber's own result read as carried (False) or failed (True);
    # None when the record doesn't say.
    rejected: bool | None = None
    # Yeas minus nays: 0 is a tie.
    margin: int = 0


class IssueMemberSchema(CamelModel):
    """A state's sitting member, with the office's contact details and how
    they voted on the issue's roll calls ({"house-119-2-314": "Yea"})."""
    id: str
    name: str
    party: str
    chamber: str
    district: int | None = None
    office_phone: str = ""
    contact_form_url: str = ""
    website_url: str = ""
    positions: dict[str, str] = {}


class IssueMembersSchema(CamelModel):
    """GET /api/action/issues/{id}/members?state=: a state's members, for
    contacting them about one issue. The state is the reader's explicit
    pick, carried in the URL and kept nowhere (AGENTS.md section 8)."""
    state: str
    votes: list[IssueVoteSchema] = []
    members: list[IssueMemberSchema] = []


class RelatedMonitorSchema(CamelModel):
    """A national monitor tracking an issue, by its stored title. Pages used
    to print the slug back out as a name ("Democratic senator talks trumps
    russian diesel deal"), losing the apostrophe and the capitals."""
    slug: str
    title: str


class IssueBallotRaceSchema(CamelModel):
    """A race the issue names: by id ("2026-SEN-GA"), its label ("Senate",
    "GA-2") and the nominee the story named, as the story wrote it."""
    id: str
    label: str
    candidate: str | None = None


class IssueBallotSchema(CamelModel):
    """A state the issue names (app/ballot_links.py): its races, the voter
    calendar's next deadline (registration while it is open), where to register and
    the state's official voter lookup, read when the issue is served."""
    state: str
    state_name: str
    races: list[IssueBallotRaceSchema] = []
    deadline: dict | None = None
    register_link: dict | None = None
    lookup: dict | None = None


class ActionIssueSchema(CamelModel):
    id: int
    public_id: str
    # `date` is bumped to today on every pipeline run that so much as
    # re-matches this story to fresh coverage, whether or not anything
    # actually changed (action_center.py's _apply_matched_issue_update sets
    # match.date = today unconditionally on a match) — so an issue that has
    # simply stayed the top story for a week shows today's date forever,
    # which reads as "this happened today." `first_surfaced` is the row's
    # created_at, set once and never touched again: the closest available
    # proxy for when the underlying story actually broke. `date` still
    # drives day-based browsing (which stories were trending ON day X) and
    # keeps its own meaning there — the display layer is what has to stop
    # treating it as the story's origin.
    first_surfaced: str
    date: str
    rank: int
    title: str
    summary: str
    facts: list[str] = []
    # Source name per fact, aligned with `facts` by index. Facts are
    # verbatim spans of real reporting (see pipeline/analyze/claims.py),
    # so this is what lets a reader check any one of them against the
    # outlet that made it. Empty for issues that predate the claim layer.
    fact_sources: list[str] = []
    # The article each fact was quoted from, aligned with `facts`; the
    # summary's outlet and article (it is a quoted claim too). Empty or None
    # for issues that predate them.
    fact_source_urls: list[str] = []
    summary_source: str | None = None
    summary_source_url: str | None = None
    # Subset of `facts` (by exact text) not present as of this issue's last
    # genuine content change — empty for an issue that's never been updated,
    # not "every fact", since that would just mean the issue is new, not
    # that anything changed since a reader might have last seen it. See
    # app/fact_diff.py.
    new_facts: list[str] = []
    actions: list[ActionItemSchema] = []
    source_urls: list[str] = []
    source_names: list[str] = []
    policy_areas: list[str] = []
    related_bills: list[RelatedBillSchema] = []
    related_explore_docs: list[RelatedExploreDoc] = []
    related_senators: list[RelatedSenator] = []
    # Kept for pages built before related_monitors (expand, then contract).
    related_monitor_slugs: list[str] = []
    # Only monitors that still exist: a merged or deleted monitor's slug
    # stays on the issues it tracked, and linked nowhere.
    related_monitors: list[RelatedMonitorSchema] = []
    deciders: list[DeciderSchema] = []
    # The states and races the issue names, with their voter information.
    ballot: list[IssueBallotSchema] = []
    # Roll calls linked to the issue (IssueVoteSchema), so a page knows to
    # offer each member's record on them.
    vote_count: int = 0
    # Those roll calls themselves, newest first ("Related votes in Congress").
    related_votes: list[IssueVoteSchema] = []
    full_story: str | None = None
    # Computed only by the list endpoint (app/trending.py needs the whole
    # day's view-count spread to judge any one issue) — always False from
    # the single-issue endpoint, which is mostly hit for OG-crawler
    # metadata and doesn't have peers to compare against.
    is_trending: bool = False
    # "confirmed" for every ordinary news-derived issue; "developing" only
    # for a primary-source-only draft awaiting press corroboration — see
    # ActionIssueStatus and pipeline/analyze/early_signal.py. The frontend
    # uses this to show a disclosure badge and to exclude these from
    # normal top-story ranking competition.
    status: str = "confirmed"
    # False once the issue is off the Action Center. With status
    # "developing" the draft was retired (retired_reason says why), and the
    # page says so rather than "Developing".
    is_current: bool = True
    # Why a developing draft left the Action Center (models.RetiredReason):
    # "unconfirmed" (no coverage in time) or "covered" (a reported issue
    # covers it). None while current, and for a draft retired before reasons
    # were recorded until the next refresh gives it one.
    retired_reason: str | None = None
    # The issue this one was found to duplicate (duplicate_of_id): for a
    # retired vote draft, the reported issue covering the same vote.
    covered_by: str | None = None
    # What a DEVELOPING issue was drafted from ("senate_roll_call_vote",
    # "federal_register_significant_rule", "election_results", ...); None
    # for an ordinary news-derived issue. The page names the source in its
    # disclosure rather than calling every one a vote record.
    source_type: str | None = None
    # A seat-flip issue's count (source_type "election_results"): when its
    # figures were read (UTC ISO) and whether the state calls that count
    # official. None for every other issue, and once news coverage has
    # confirmed it (its facts are then the outlets', not the count).
    count_as_of: str | None = None
    count_official: bool | None = None
    # Only ever set from a source article whose feed explicitly granted
    # redistribution rights — see news_feeds._rights_cleared_image. None
    # for the large majority of issues; used for the OG image and the
    # in-page renders on the Action Center and issue pages.
    image_url: str | None = None
    # The source's own photo caption — real accessible alt text, not a
    # generic fallback. "" when the source supplied none.
    image_alt: str = ""
    # Photographer/wire-service credit shown alongside the image.
    image_credit: str = ""


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
    # A closed monitor's closing day (its last write, when it closed).
    closed_on: str | None = None


class NationalMonitorDetailSchema(NationalMonitorSchema):
    updates: list[MonitorUpdateSchema] = []
    # "On the record": the roll calls matched to the monitor's updates
    # (app/roll_call_links.py), oldest first.
    on_the_record: list[IssueVoteSchema] = []


class TimelineEntrySchema(CamelModel):
    date: str
    title: str
    summary: str
    policy_areas: list[str] = []
    source_url: str | None = None
    source_name: str | None = None
    monitor_slug: str | None = None


# --- Public API v1, continued: roll calls, presidents, justices, nominations
# and agencies (api/public.py). After the internal schemas they extend; the
# same contract as the section above (extra="forbid", contract-tested).


class PublicRollCallPositionSchema(_PublicModel):
    last_name: str = Field(description="The member's surname as the chamber's record prints it (the House Clerk "
                                       "adds the state where two members share one: \"Doe (TX)\")")
    first_name: str | None = Field(description="As the Senate's record prints it; null on a House roll call")
    name: str | None = Field(description="A representative's own full name where Civitas has a page for them (the "
                                         "Clerk prints only a surname); null for a senator, whose record prints "
                                         "both names, and for a former representative")
    party: str
    state: str
    district: int | None = Field(description="A representative's district (0 at-large) where Civitas knows it; "
                                             "null for a senator or a former member")
    position: str = Field(description="As the chamber recorded it: Yea, Nay, Aye, No, Present, Not Voting, Guilty, "
                                      "a candidate's name in an election of the Speaker")
    bucket: Literal["yea", "nay", "present", "notVoting", "candidate"] = Field(
        description="How the position is counted in the tally (Aye and Guilty count as yea; a candidate's name is "
                    "counted as not voting in the stored tally and listed in candidates)")
    chamber_member_id: str | None = Field(description="The id the chamber's record gives the member: a bioguide id "
                                                      "in the House, the Senate's LIS id in the Senate")
    member_id: str | None = Field(description="The member's Civitas id (as in /senators/{id} or "
                                              "/representatives/{id}); null for a member Civitas has no page for")
    site_url: str | None = Field(description="The member's page on Civitas; null when there is none")


class PublicRollCallDetailSchema(PublicRollCallSchema):
    bill_id: str | None = Field(description="The measure voted on (e.g. HR.1234), as Civitas names it; null for a "
                                            "roll call on no bill")
    title: str = Field(description="The chamber's own title of the vote; empty when it records none")
    yeas: int
    nays: int
    present: int
    not_voting: int
    positions: list[PublicRollCallPositionSchema] = Field(description="Every member's recorded position, by surname")


class PublicRollCallPageSchema(_PublicModel):
    chamber: Literal["senate", "house"]
    congress: int
    covered: bool = Field(description="Whether Civitas holds this Congress's roll calls; false means the list is "
                                      "empty because they aren't held here, not because none were taken")
    held_congresses: list[int] = Field(description="The Congresses whose roll calls Civitas holds, oldest first")
    total: int
    page: int = Field(description="This page; a page past the last is answered as the last")
    per_page: int
    total_pages: int = Field(description="At least 1, even when there are none")
    entries: list[PublicBillRollCallSchema] = Field(description="Newest first")


_PRESIDENT_RANK = Field(json_schema_extra=_DEPRECATED_FIELD, description=_DEPRECATED + "Place among the former presidents by Presidential Score as the site shows it "
                                    "(compared as displayed, a whole number; equal scores share a rank). Null for a "
                                    "sitting president, whose term is not complete and is not ranked")


class PublicPresidentSchema(PresidentSchema):
    model_config = _PublicModel.model_config
    rank: int | None = _PRESIDENT_RANK
    site_url: str = _SITE_URL


class PublicPresidentListSchema(_PublicModel):
    total: int
    entries: list[PublicPresidentSchema] = Field(description="Ranked presidents best score first, then any sitting "
                                                             "president")


class PublicJusticeRowSchema(JusticeLeaderboardEntry):
    model_config = _PublicModel.model_config
    site_url: str = _SITE_URL


class PublicJusticeListSchema(_PublicModel):
    total: int
    entries: list[PublicJusticeRowSchema] = Field(description="The sitting justices by seniority. Not ranked or "
                                                              "scored: score is always null")


class PublicJusticeSchema(JusticeSchema):
    model_config = _PublicModel.model_config
    site_url: str = _SITE_URL


class PublicCaseLinksSchema(_PublicModel):
    oyez: str | None = Field(description="The case on Oyez")
    opinion: str | None = Field(description="The Court's slip opinion, once posted")
    explore: str | None = Field(description="The case's document on Civitas")


class PublicJusticeCaseSchema(_PublicModel):
    case_id: str = Field(description="The case's id in the record (the Supreme Court Database's, or Oyez's for a "
                                     "term the Database doesn't cover yet)")
    name: str
    term: str = Field(description="The Court's term (October Term), YYYY")
    decided: str | None = Field(description="YYYY-MM-DD")
    vote: str = Field(description="The justice's side: majority or minority")
    opinion: str | None = Field(description="The opinion the justice wrote or joined, as the record codes it")
    split: str = Field(description="The Court's vote, majority-minority (e.g. 6-3)")
    links: PublicCaseLinksSchema


class PublicJusticeCasesSchema(_PublicModel):
    id: str = Field(description="The justice's id")
    total: int
    page: int
    per_page: int
    total_pages: int = Field(description="At least 1, even when there are none")
    terms: list[int] = Field(description="The terms the record covers")
    entries: list[PublicJusticeCaseSchema] = Field(description="Newest first")


class PublicLatestActionSchema(_PublicModel):
    date: str | None
    text: str | None


class PublicNominationSchema(_PublicModel):
    congress: int
    citation: str = Field(description="The Senate's citation, e.g. PN12 or PN12-3 for one part of a split "
                                      "nomination")
    description: str = Field(description="As the Senate's record words it (the nominee, their state and the office)")
    organization: str | None = Field(description="The organization, as Congress.gov names it")
    agency_slug: str | None = Field(description="The organization's agency in /agencies, where a name matches "
                                                "exactly; null otherwise")
    position_title: str | None
    kind: Literal["civilian", "judicial", "military"]
    nominees: list[str] = Field(description="The nominees' names as published (\"Jane Q. Doe (TX)\"): in a list, "
                                            "the first few; one nomination gives every one")
    nominee_count: int
    received_date: str | None = Field(description="When the Senate received it, YYYY-MM-DD")
    status: str = Field(description="pending, confirmed, returned or withdrawn")
    status_date: str | None
    latest_action: PublicLatestActionSchema
    days_to_confirm: int | None = Field(description="Days from receipt to confirmation; null unless confirmed")
    congress_gov_url: str
    site_url: str = _SITE_URL


class PublicNominationBackfillSchema(_PublicModel):
    complete: bool = Field(description="Whether the daily read has reached every nomination of the Congress. "
                                       "False: total and every count are of the nominations read so far (the "
                                       "oldest updates first), not the Congress's whole number")
    read: int = Field(description="Nominations read so far")
    expected: int | None = Field(description="About how many the Congress has, as Congress.gov counts them; null "
                                             "before the first read")
    nights_left: int | None = Field(description="Daily reads expected before the rest are in; null when complete "
                                                "or unknown")


class PublicNominationPageSchema(_PublicModel):
    congress: int
    backfill: PublicNominationBackfillSchema = Field(description="Whether the list is the Congress's whole record "
                                                                 "yet")
    total: int
    page: int
    per_page: int
    total_pages: int = Field(description="At least 1, even when there are none")
    entries: list[PublicNominationSchema] = Field(description="Newest action first")


class PublicNominationRollCallSchema(PublicRollCallSchema):
    yeas: int
    nays: int
    present: int
    not_voting: int


class PublicNominationActionSchema(_PublicModel):
    date: str = Field(description="YYYY-MM-DD")
    code: str = Field(description="Congress.gov's action code")
    type: str
    text: str = Field(description="As the Senate's record words it")


class PublicCommitteeRefSchema(_PublicModel):
    code: str = Field(description="The committee's code (e.g. SSJU00)")
    name: str


class PublicNominationDetailSchema(PublicNominationSchema):
    actions: list[PublicNominationActionSchema] = Field(description="The Senate's actions on it, newest first")
    hearing_held: bool = Field(description="Whether an action records a hearing")
    committee: PublicCommitteeRefSchema | None = Field(description="The committee it was referred to")
    roll_calls: list[PublicNominationRollCallSchema] = Field(description="The Senate roll calls whose question names "
                                                                         "this citation, oldest first")


class PublicAgencySchema(_PublicModel):
    slug: str = Field(description="The agency's id, the Federal Register's")
    name: str
    short_name: str | None
    parent_slug: str | None = Field(description="The department or agency it sits under")
    documents: int = Field(description="Federal Register documents Civitas holds from it, its sub-agencies' included")
    site_url: str = _SITE_URL


class PublicAgencyListSchema(_PublicModel):
    total: int
    entries: list[PublicAgencySchema] = Field(description="Every agency the Federal Register lists, by name")
    as_of: str | None = Field(description="When the list was last read, YYYY-MM-DD")


class PublicAgencyRefSchema(_PublicModel):
    slug: str
    name: str


class PublicAgencyDetailSchema(PublicAgencySchema):
    description: str | None = Field(description="The Federal Register's description of the agency")
    website: str | None
    fr_url: str | None = Field(description="The agency's page on the Federal Register")
    parent: PublicAgencyRefSchema | None
    children: list[PublicAgencyRefSchema] = Field(description="Its sub-agencies, by name")
    documents_by_type: dict[str, int] = Field(description="Documents held, by Federal Register type")
    open_comment_periods: int = Field(description="Its documents open for comment today (see /comment-periods)")


class PublicCommentPeriodSchema(_PublicModel):
    key: str = Field(description="The document's permanent key on Civitas (its Federal Register document number)")
    title: str
    doc_type: str = Field(description="The Federal Register's type: Proposed Rule, Rule, Notice ...")
    published: str = Field(description="YYYY-MM-DD")
    agency_name: str
    agency_slug: str
    comments_close_on: str = Field(description="The last day to comment, YYYY-MM-DD, on the deadline's own clock "
                                               "(Eastern)")
    days_left: int = Field(description="Days from today (Eastern) to the deadline; 0 on its last day")
    comment_url: str = Field(description="Where to comment (Regulations.gov)")
    url: str = Field(description="The document on the Federal Register")
    site_url: str = _SITE_URL


class PublicCommentPeriodPageSchema(_PublicModel):
    today: str = Field(description="Today on the deadline's clock (Eastern), YYYY-MM-DD")
    total: int
    page: int
    per_page: int
    total_pages: int = Field(description="At least 1, even when there are none")
    entries: list[PublicCommentPeriodSchema] = Field(description="Soonest deadline first")
