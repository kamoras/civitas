import re
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.config_definitions import BILL_STAGES


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


class RepresentationScoreSchema(CamelModel):
    funding_independence: float = Field(
        description="0-100: how free the member is from PAC and large-donor dependence. Weighted into overall")
    promise_persistence: float | None = Field(
        None,
        description="Always null. It measured campaign promises kept, and campaign-promise tracking was removed in 2026-07 (its LLM evaluations were unreliable); the stored value since then was a constant, not a measurement. Kept so existing clients still find the field")
    constituent_alignment: float = Field(
        description="0-100: how the member's votes compare with what the seat's partisan lean predicts. Weighted into overall")
    funding_diversity: float = Field(
        description="0-100, informational: how widely funding spreads across industries and donor kinds")
    legislative_effectiveness: float = Field(
        0.0, description="0-100: how far the member's bills advance, and the coalitions they draw. Weighted into overall")
    # Backend-computed overall (score_calculator.compute_overall_score) — the
    # frontend must never recompute this from the sub-scores itself (see
    # lib/representation.ts's removed weightedScore).
    overall: float = Field(0.0, description="The weighted overall score, 0-100 (weights: the API index's scoreWeights)")
    confidence: dict[str, str] | None = Field(
        None, description='How much data backs each sub-score, by its name: "high", "medium" or "low". Also '
                          'constituentAlignmentVotePart, how that score\'s vote part was scored: "full"; '
                          '"shrunk:<share kept>" or "shrunk-neutral:<share kept>" (few votes, pulled toward the '
                          'party\'s typical score or toward 50); "typical:few-votes"; or "neutral:<reason>" (scored 50)')


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
    total_votes: int = Field(description="Roll-call votes on the member's record")
    voted_with_party_count: int = Field(0, description="Votes cast with the member's party majority")
    voted_against_party_count: int = Field(0, description="Votes cast against the member's party majority")
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


class LobbyingMatchSchema(CamelModel):
    lobbyist_org: str
    industry: str
    lobbying_spend: float
    donation_to_senator: float
    bills_influenced: list[str]
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
    overall_lean: float = Field(description="Negative leans Democratic, positive Republican")
    overall_party: Literal["R", "D", "centrist"]
    depth: Literal["deep", "moderate", "centrist", "cross-cutting"] = Field(
        description="The member's tercile of lean within their own party; cross-cutting when many areas "
                    "lean to the other party")
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


class BillDetailSchema(BillInFlightSchema):
    policy_areas: list[PolicyAreaDetail] = []
    party_leaning: Literal["R", "D", "bipartisan"] | None = None
    related_issues: list[RelatedIssueSchema] = []


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
        None, description="How strongly the member's votes lean to one party, by policy area")
    sponsored_bills: list[SponsoredBillSchema] = Field([], description="Bills the member sponsored this Congress")
    leadership_score: float | None = Field(None, description=_LEADERSHIP)
    bipartisanship_score: float | None = Field(
        None, description="Cross-party cosponsorship, given and received (after the Lugar Center's Bipartisan "
                          "Index), 0-1: 0.5 is the chamber median, 1 twice it or more; null with too little data")
    ideology_score: float | None = Field(None, description=_IDEOLOGY)
    sponsorship_description: str = Field("", description="Ideology and leadership in words")
    constituent_approval: ConstituentApprovalSchema | None = Field(
        None, description="How the member's constituents rated them in the Cooperative Election Study, by party")
    website_url: str = ""
    contact_form_url: str = ""
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
    ideology_label: str | None = Field(None, description="ideologyScore in words, relative to the member's own party")
    # PageRank cosponsorship centrality (sponsorship_analysis.
    # compute_leadership_scores), log-rescaled to [0, 1] to counter its
    # power-law distribution — most members cluster low, a few attract
    # disproportionate cosponsor weight. None when too little
    # cosponsorship data exists to compute it.
    leadership_score: float | None = Field(None, description=_LEADERSHIP)


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
_RANK = Field(description="Place in the whole chamber by overall score, whatever the filters (ties share one: 1, 2, 2, 4)")


class PublicApiIndexSchema(_PublicModel):
    name: str
    version: str
    rate_limit: str = Field(description="The rate limit, in words")
    score_weights: dict[str, float] = Field(description="How the sub-scores weigh into representationScore.overall")
    endpoints: dict[str, str] = Field(description='"METHOD path" -> what it returns')
    docs: str = Field(description="The documentation page")
    openapi: str = Field(description="This API's OpenAPI 3 description")
    mcp: str = Field(description="MCP server (streamable HTTP) exposing these endpoints as tools")
    source: str = Field(description="Civitas's source code")


class PublicStateSchema(_PublicModel):
    code: str = Field(description="Two-letter state code")
    name: str
    senator_count: int = Field(description="Serving senators")
    representative_count: int = Field(description="Serving representatives (delegates aren't scored)")


class PublicSenatorRowSchema(LeaderboardEntrySchema):
    model_config = _PublicModel.model_config
    rank: int = _RANK
    site_url: str = _SITE_URL


class PublicRepresentativeRowSchema(LeaderboardEntrySchema):
    model_config = _PublicModel.model_config
    rank: int = _RANK
    district: int = Field(description="Congressional district; 0 for an at-large seat")
    site_url: str = _SITE_URL


class PublicSenatorProfileSchema(SenatorSchema):
    model_config = _PublicModel.model_config
    site_url: str = _SITE_URL


class PublicRepresentativeProfileSchema(RepresentativeSchema):
    model_config = _PublicModel.model_config
    site_url: str = _SITE_URL


class _PublicPage(_PublicModel):
    total: int = Field(description="Matching members across every page")
    page: int = Field(description="This page; a page past the last is answered as the last")
    per_page: int
    total_pages: int = Field(description="At least 1, even when nothing matches")


class PublicSenatorPageSchema(_PublicPage):
    entries: list[PublicSenatorRowSchema]


class PublicRepresentativePageSchema(_PublicPage):
    entries: list[PublicRepresentativeRowSchema]


class PublicScoreSnapshotSchema(_PublicModel):
    date: str = Field(description="When the scores were computed, YYYY-MM-DD")
    overall: float = Field(description="0-100, like the sub-scores here; see representationScore for each")
    funding_independence: float
    promise_persistence: float | None = Field(None, description="Always null: see representationScore")
    constituent_alignment: float
    funding_diversity: float
    legislative_effectiveness: float


class PublicHistorySchema(_PublicModel):
    id: str = Field(description="The member's id")
    snapshots: list[PublicScoreSnapshotSchema] = Field(description="Oldest first")


class PublicSearchResultSchema(_PublicModel):
    id: int = Field(description="The document's id on Civitas (siteUrl)")
    title: str
    date: str = Field(description="YYYY-MM-DD")
    doc_type: str = Field(description="One of the doc_type filter's values")
    source: str
    politician_name: str = Field(description="The member who delivered or signed it; empty when none")
    politician_id: str = Field(description="Their Civitas id; empty when none")
    chamber: str = Field(description="Senate, House, Executive, Judicial or Regulatory (the chamber filter's "
                                     "values, capitalized)")
    agency_name: str = Field(description="The issuing agency, for a Federal Register document; empty otherwise")
    url: str = Field(description="The document at its official source")
    site_url: str = _SITE_URL
    summary: str
    comment_url: str = Field(description="Where to comment, for a rule whose comment period is open")
    comments_close_on: str = Field(description="When that comment period closes, YYYY-MM-DD; empty when none")
    cited_by_count: int = Field(description="How many indexed documents cite this one")
    matched_by: list[Literal["semantic", "keyword"]] = Field(description="Which search found it: by meaning, by exact words, or both")
    distance: float | None = Field(description="Distance in meaning from the query; null when only the exact words matched")
    snippet: str = Field(description="Text around the matched words, plain")
    duplicate_count: int = Field(description="Near-identical copies folded into this result")


class PublicSearchResponseSchema(_PublicModel):
    query: str
    results: list[PublicSearchResultSchema] = Field(description="Best match first")
    count: int = Field(description="Results returned (at most the request's limit), not a total")
    partial: bool = Field(description="True when only the exact-words search could answer, so the ranking is incomplete")
    index_building: bool = Field(description="True while the search index is being built; results are empty until it is")


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
    # Loyalty to the appointing president, 0-100 (justice_loyalty.score);
    # None until the Supreme Court Database covers the justice.
    loyalty: float | None = None
    # Backend-computed overall (justice_service._build_score).
    overall: float | None = None


class JusticeLoyaltySchema(CamelModel):
    """The loyalty estimate behind the score: points more often for the
    government while the appointing president is in office (a share), its
    standard error, the votes under the appointing president and under
    others with the share of each for the government, and the Supreme Court
    Database term the record runs through."""
    estimate: float
    se: float
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
    loyalty: JusticeLoyaltySchema | None = None


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
    # The action-center blob already stores these (action_center._make_entry);
    # without declaring them here, RelatedSenator(**s) silently strips them and
    # the issue card's per-member "CONTACT" button always fell back to a generic
    # senate.gov link — wrong for House members in the list.
    contact_form_url: str | None = None
    website_url: str | None = None


class ActionItemSchema(CamelModel):
    text: str
    type: str = "general"
    url: str | None = None


class RelatedBillSchema(CamelModel):
    name: str
    id: str
    url: str
    # Path to our own bill detail page ("/congress/bills/HR.22") when we host this
    # bill; None means the congress.gov `url` is the only link available.
    internal_url: str | None = None


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
    related_monitor_slugs: list[str] = []
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
