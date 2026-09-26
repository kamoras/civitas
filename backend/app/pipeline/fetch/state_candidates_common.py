"""Parsing shared by every confirmed-nominee adapter (see
state_candidates.py). Each state vendor publishes a different envelope —
Clarity's JSON, a bulk delimited results export, Civix's REST API — but
once a contest is reduced to (label, choices, votes) the questions are
identical: which federal office is this, which party's primary, who won,
and is it safe to say so.

Keeping these here is what stops "one adapter per vendor" from decaying
into "one parser per state": a new state on a known vendor is a config
entry, and a new VENDOR only has to supply the envelope, not re-derive how
a district number or a runoff threshold works.

Every label pattern below is taken from a real, live-fetched feed, never
invented:
  Clarity/CO  "Representative to the 120th United States Congress - District 1 - Democratic Party"
  Clarity/CO  "United States Senator - Democratic Party"
  NCSBE/NC    "US HOUSE OF REPRESENTATIVES DISTRICT 01 (REP)"
  NCSBE/NC    "US SENATE (DEM)"
"""

import logging
import re
from collections.abc import Callable
from urllib.parse import urljoin

from app.pipeline.fetch.http_utils import fetch_text_with_retry

logger = logging.getLogger(__name__)

# Chamber wording varies ("United States Congress", "US HOUSE OF
# REPRESENTATIVES", "U.S. Representative", Tennessee's certified list's
# "United States House of Representatives District 1", Arkansas's real "U.S. Congress
# District 02", Vermont's real bare "REPRESENTATIVE TO CONGRESS" — no
# "U.S."/"United States" prefix, "to" not "in"); the ordinal in Colorado's
# label advances every Congress, so nothing cycle-specific is matched.
# The word "Congress" is itself decisive — no state legislature is called
# one, so "Representative in/to Congress" (Florida/Vermont), "1ST
# CONGRESS" (Illinois) and the bare abbreviated "U.S. Congress" (Arkansas)
# are safe to recognise, while a bare "House of Representatives" is NOT
# and must keep being refused (it is a state chamber's name in most
# states; see office_from_columns for how a state that publishes only
# that label is handled). Every "Congress" alternative carries its own
# \b: without it, "Congress" matches as a substring of "Congressional"/
# "Congressman" too (a common county-export convention groups a LOCAL
# race under its containing congressional district, e.g. "U.S.
# Congressional District 3 County Commissioner"), which would misread
# that county race as a real federal contest.
_CHAMBER_HOUSE = (
    r"(?:United\s+States\s+(?:Congress\b|Representative|House\b)"
    r"|U\.?\s*S\.?\s*(?:House|Representative|Congress\b)"
    r"|(?:Representative\s+(?:in|to)\s+|\d+(?:st|nd|rd|th)\s+)Congress\b)"
)
# Both orders occur live: "District 5" (most states) and "5th District"
# / "1ST CONGRESS" (Florida's and Illinois' own labels). Arizona's own
# canvass export inserts "No." ("District No.  1", double space and all).
_HOUSE_DISTRICT_RE = re.compile(
    rf"{_CHAMBER_HOUSE}.*?District\s+(?:No\.?\s*)?0*(\d+)", re.IGNORECASE | re.DOTALL,
)
_HOUSE_ORDINAL_RE = re.compile(
    rf"(?:0*(\d+)(?:st|nd|rd|th)\s+(?:District|Congress)|{_CHAMBER_HOUSE}[^\d]*0*(\d+)(?:st|nd|rd|th))",
    re.IGNORECASE | re.DOTALL,
)
_HOUSE_RE = re.compile(_CHAMBER_HOUSE, re.IGNORECASE)
# Hawaii numbers its congressional districts in Roman numerals ("U.S.
# Representative, Dist I"). Bounded to I-XX: a state has no more than 52
# districts and nothing beyond that is worth guessing at.
_ROMAN = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
    "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
    "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20,
}
_HOUSE_ROMAN_RE = re.compile(
    r"Dist(?:rict)?\.?\s+([IVX]{1,5})\b", re.IGNORECASE,
)
# "Senator" (Colorado) and "Senate" (North Carolina) both appear live.
# "Senator in Congress" (Rhode Island's own label) carries no "U.S."
# prefix at all, so it needs its own arm -- the mirror of the
# "Representative in/to Congress" arm _CHAMBER_HOUSE already has, and
# safe for the same reason: no STATE chamber is named "Congress", so
# "in Congress" is only ever federal. This must stay narrow: a bare
# "Senator, District 5" is a state-senate seat in most states and must
# keep parsing as None (Rhode Island's own ballot proves the risk --
# its legislature is literally the "General Assembly", whose seats are
# labelled "Senator in General Assembly District 5").
_SENATE_RE = re.compile(
    r"(?:(?:United\s+States|U\.?\s*S\.?)\s*Senat(?:e|or)"
    r"|Senator\s+(?:in|to)\s+Congress\b)", re.IGNORECASE,
)

# Spelled-out names and the states' own abbreviations both occur; the
# shared matcher in state_candidates.py speaks single-letter codes.
_PARTY_PATTERNS = [
    # Two states don't call their Democrats Democrats on the ballot:
    # Minnesota's party is the Democratic-Farmer-Labor (DFL) and North
    # Dakota's the Democratic-NPL. Both are the state Democratic party and
    # FEC files their candidates as DEM.
    (re.compile(r"\b(?:democratic|democrat|dem|dfl|d-npl|dnl|npl)\b", re.IGNORECASE), "D"),
    (re.compile(r"\b(?:republican|rep|gop)\b", re.IGNORECASE), "R"),
    # Arizona's own 3-letter codes ("LBT", "GRN") don't share a root with
    # "lib"/"gre" — verified live off its canvass export.
    (re.compile(r"\b(?:libertarian|lib|lbt)\b", re.IGNORECASE), "L"),
    (re.compile(r"\b(?:green|gre|grn)\b", re.IGNORECASE), "G"),
    (re.compile(r"\b(?:constitution|con|cst)\b", re.IGNORECASE), "C"),
]

_SINGLE_LETTER_PARTIES = {"D", "R", "L", "G", "C"}

# Generational suffixes must not be mistaken for a surname.
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Ballot annotations that are never part of a legal name: a parenthetical
# (Georgia's "(I)" incumbency marker) or a bare asterisk (Rhode Island's
# party-endorsement marker). See surname().
_ANNOTATION_RE = re.compile(r"\([^)]*\)|\*")


def parse_office(contest_name: str) -> tuple[str, int | None] | None:
    """("S", None) / ("H", 3) / ("H", None) for an at-large seat, or None
    for anything not positively recognised as a federal contest — a state
    legislative or judicial race must never be guessed into a federal one.

    A House label carrying no district number is an at-large seat (AK, DE,
    MT, ND, SD, VT, WY); FEC models those as district 0, which
    state_candidates._race_id_for already falls back to.
    """
    name = contest_name or ""
    if _SENATE_RE.search(name):
        return "S", None
    if _HOUSE_RE.search(name):
        roman = _HOUSE_ROMAN_RE.search(name)
        if roman and roman.group(1).upper() in _ROMAN:
            return "H", _ROMAN[roman.group(1).upper()]
        # An ordinal district only counts once the label is already known
        # to be federal — "5th District" alone says nothing about which
        # chamber it belongs to.
        m = _HOUSE_ORDINAL_RE.search(name)
        if m:
            return "H", int(m.group(1) or m.group(2))
    m = _HOUSE_DISTRICT_RE.search(name)
    if m:
        return "H", int(m.group(1))
    if _HOUSE_RE.search(name):
        return "H", None
    return None


# ── Statewide executive offices ──────────────────────────────────────
#
# A deliberately SHORT list of offices whose names are unambiguous
# statewide once a locality qualifier is ruled out. These have no FEC
# counterpart (no federal filing, no campaign-finance rows, no
# Representation Score), so they are stored and rendered separately from
# Race/Candidate — see models.StatewideNominee.
#
# The risk here is the mirror of parse_office's: where that must not read
# a STATE legislative seat as federal, this must not read a COUNTY or
# MUNICIPAL office as statewide. "County Treasurer" and "District
# Attorney" both contain a statewide office's words, and the colon form
# is this vendor's own real convention for a one-town contest -- Rhode
# Island's live ballot carries "DEM Cranston: City Council Ward 1",
# "DEM Providence: Mayor" and "Woonsocket: Non-Partisan Mayor".
# _LOCAL_QUALIFIER_RE refuses all of them outright rather than trying to
# rank which reading is more likely.
_STATEWIDE_OFFICES = [
    ("governor", re.compile(r"\bgovernor\b", re.IGNORECASE)),
    ("attorney_general", re.compile(r"\battorney\s+general\b", re.IGNORECASE)),
    ("secretary_of_state", re.compile(r"\bsecretary\s+of\s+state\b", re.IGNORECASE)),
    # Bare "Treasurer" is a county/city office in most states; only the
    # qualified statewide forms count.
    ("treasurer", re.compile(r"\b(?:general|state)\s+treasurer\b", re.IGNORECASE)),
    # Same rule, same reason: Minnesota's own ballot carries both "State
    # Auditor" (statewide, elected) and "County Auditor/Treasurer" (not).
    # The locality gate already refuses the county one; requiring the
    # qualifier means a state that prints a bare "Auditor" for a county
    # office cannot slip through either.
    # Nebraska's own name for the office is "Auditor of Public
    # Accounts" -- specific enough to stand without a "state" qualifier,
    # since no county calls its auditor that.
    ("auditor", re.compile(
        r"\b(?:state|general)\s+auditor\b|\bAuditor\s+of\s+(?:Public\s+Accounts|State)\b",
        re.IGNORECASE)),
    # Idaho's own constitutional officer. Qualified like the two above,
    # because a county can have a controller too.
    ("controller", re.compile(r"\bstate\s+controller\b", re.IGNORECASE)),
    # The same role under the name most other states give it.
    ("comptroller", re.compile(r"\b(?:state\s+)?comptroller\b", re.IGNORECASE)),
    # LAST RESORT, and only ever reached by a label the locality gate
    # already cleared. California prints its statewide officers as a
    # bare "Treasurer" and "Controller" with nothing else in the label;
    # a county or municipal one carries its locality ("County
    # Treasurer", "Cranston: Treasurer") and was refused well before
    # this point. The qualified arms above still match first, so a state
    # that spells the office out is unaffected.
    ("treasurer", re.compile(r"\btreasurer\b", re.IGNORECASE)),
    ("controller", re.compile(r"\bcontroller\b", re.IGNORECASE)),
]

_LT_GOVERNOR_RE = re.compile(r"\b(?:lieutenant|lt\.?)\s+governor\b", re.IGNORECASE)

# Offices whose FULL PHRASE is statewide by construction, checked BEFORE
# the locality gate because that gate would otherwise refuse them on a
# word it is right to distrust in general.
#
# "Commissioner of Insurance" is refused by _LOCAL_QUALIFIER_RE on
# "commissioner", which exists to stop Minnesota's "County Commissioner
# District 1"; "State School Superintendent" is refused on "school",
# which exists to stop Rhode Island's "School Committee". Both of those
# gates are correct and both must stay — so these phrases get their own
# pass, and are still refused when the label carries a real locality
# marker, since a "County School Superintendent" is a county office.
#
# Every phrase here is a real label off Georgia's 2026 primary, where
# four genuine statewide constitutional offices were being missed.
_STATEWIDE_PHRASES = [
    ("insurance_commissioner", re.compile(
        r"\bCommissioner\s+of\s+Insurance\b|\bInsurance\s+Commissioner\b", re.IGNORECASE)),
    # Iowa calls its agriculture officer a Secretary, not a Commissioner.
    ("agriculture_commissioner", re.compile(
        r"\bCommissioner\s+of\s+Agriculture\b|\bAgriculture\s+Commissioner\b"
        r"|\bSecretary\s+of\s+Agriculture\b", re.IGNORECASE)),
    ("labor_commissioner", re.compile(
        r"\bCommissioner\s+of\s+Labor\b|\bLabor\s+Commissioner\b", re.IGNORECASE)),
    ("school_superintendent", re.compile(
        r"\bState\s+School\s+Superintendent\b"
        r"|\bSuperintendent\s+of\s+Public\s+Instruction\b", re.IGNORECASE)),
    # Georgia prints the bare initialism; spelled out elsewhere.
    ("public_service_commission", re.compile(
        r"\bPSC\b|\bPublic\s+Service\s+Commission(?:er)?\b", re.IGNORECASE)),
    # Florida's fourth cabinet officer.
    ("chief_financial_officer", re.compile(
        r"\bChief\s+Financial\s+Officer\b", re.IGNORECASE)),
    # Both of these carry a district and would otherwise be refused by
    # the general locality gate, same as the PSC.
    ("board_of_equalization", re.compile(
        r"\bBoard\s+of\s+Equalization\b", re.IGNORECASE)),
    # "State" is required: a COUNTY board of education is a local office,
    # and North Carolina's export is full of them.
    ("state_board_of_education", re.compile(
        r"\bState\s+Board\s+of\s+Education\b", re.IGNORECASE)),
    ("university_regent", re.compile(
        r"\bRegent\s+of\s+the\s+University\b|\bUniversity\s+Regent\b", re.IGNORECASE)),
    # North Dakota's own constitutional officer.
    ("tax_commissioner", re.compile(
        r"\bTax\s+Commissioner\b|\bCommissioner\s+of\s+Taxation\b", re.IGNORECASE)),
    # Arkansas's, which it abbreviates as "Comm. of State Lands".
    ("state_lands_commissioner", re.compile(
        r"\bComm(?:issioner)?\.?\s+of\s+State\s+Lands\b"
        r"|\bState\s+Lands\s+Commissioner\b", re.IGNORECASE)),
]

# The locality markers that stay decisive even beside one of the phrases
# above — deliberately much narrower than _LOCAL_QUALIFIER_RE, which
# also distrusts office words like "commissioner" and "school".
# A handful of statewide bodies seat their members BY DISTRICT while
# electing them statewide — Georgia's Public Service Commission is
# elected by the whole state, but a commissioner must reside in the
# district whose seat they hold. They are statewide offices with a seat
# number, which is why StatewideNominee carries an optional district:
# without it, "PSC - District 3" and "PSC - District 5" are one office
# and the second overwrites the first.
_STATEWIDE_DISTRICT_SEATS = {
    "public_service_commission",
    # California seats its Board of Equalization by district, and
    # Utah its State Board of Education, exactly as Georgia does
    # the PSC: elected statewide, held for a district.
    "board_of_equalization",
    "state_board_of_education",
    # Colorado elects its university regents statewide, one per
    # CONGRESSIONAL district — a seat number that has nothing to do with
    # any legislative map.
    "university_regent",
}

_STATEWIDE_SEAT_RE = re.compile(r"\bDistrict\s+(?:No\.?\s*)?0*(\d+)\b", re.IGNORECASE)

_STRICT_LOCAL_RE = re.compile(
    r"\b(?:county|city|town|township|ward|borough|parish|village|precinct|municipal)\b|:",
    re.IGNORECASE,
)

_LOCAL_QUALIFIER_RE = re.compile(
    r"\b(?:county|city|town|township|ward|borough|parish|village|precinct|district|"
    r"municipal|school|council|mayor|alderman|commissioner|judge|justice|court|"
    r"assembly|senate|house|representative|senator|committee|delegate)\b"
    r"|:",  # "Cranston: ..." -- a real vendor prefix marking one town's race
    re.IGNORECASE,
)

# A state's own party lettering (mostly single-letter) doesn't match FEC's
# 3-letter codes. Used two ways, and it has to be the same map for both:
# as a tiebreaker among same-surname candidates in one federal race, and
# to render a statewide nominee's party through the very same
# majorPartyOf() the frontend already applies to every federal candidate.
PARTY_CODE_MAP = {
    "R": "REP", "D": "DEM", "L": "LIB", "G": "GRE", "I": "IND", "C": "CON",
}

# Where the pipeline records that it checked a state's statewide-executive
# contests, and the API reads that back. Shared here rather than in
# state_candidates.py so the API doesn't import the whole strategy
# registry (and its two dozen adapter modules) to read one cache key.
STATEWIDE_MARKER_TIER = "statewide"

# Deliberately long: this marker is a coverage RECORD, not an HTTP cache
# entry. Its staleness is shown to the reader as a "last checked" date the
# way MeasureCoverage.checked_at is, and a pipeline that stops running is
# an operator concern ops_alerts' staleness watchdog already owns. At the
# default 72h TTL every state page would instead revert to "not yet
# covered" after three quiet days.
STATEWIDE_MARKER_TTL_HOURS = 24 * 400


def statewide_marker_key(state: str, cycle: int) -> str:
    return f"synced-{state}-{cycle}"


# Which source last answered for a state's federal ballot, and whether that
# source was its complete certified ballot. A state's config says what its
# PRIMARY source is; a `fallback` can answer instead (a certified list not
# posted yet), and then the page must not claim a complete ballot.
BALLOT_BASIS_TIER = "ballot-basis"


def ballot_basis_key(state: str, cycle: int) -> str:
    return f"{state}-{cycle}"


# Judicial gets its OWN marker rather than sharing the statewide one.
# The statewide marker covers the executive offices and the legislature
# together because they are one claim — same ballot, same response,
# labels checked. Judicial is a separate and STRONGER claim: that this
# state's own statute on what a judicial majority means has been read
# (see JUDICIAL_MAJORITY_* — Washington and Idaho mean opposite things
# by it). A state can be checked for one and not the other, and sharing
# a marker would let it claim coverage it does not have.
JUDICIAL_MARKER_TIER = "judicial"
JUDICIAL_MARKER_TTL_HOURS = STATEWIDE_MARKER_TTL_HOURS


def judicial_marker_key(state: str, cycle: int) -> str:
    return f"judicial-synced-{state}-{cycle}"


STATEWIDE_OFFICE_LABELS = {
    "governor": "Governor",
    "lt_governor": "Lieutenant Governor",
    "attorney_general": "Attorney General",
    "secretary_of_state": "Secretary of State",
    "treasurer": "State Treasurer",
    "auditor": "State Auditor",
    "controller": "State Controller",
    "comptroller": "State Comptroller",
    "insurance_commissioner": "Insurance Commissioner",
    "agriculture_commissioner": "Agriculture Commissioner",
    "labor_commissioner": "Labor Commissioner",
    "school_superintendent": "State School Superintendent",
    "public_service_commission": "Public Service Commission",
    "chief_financial_officer": "Chief Financial Officer",
    "board_of_equalization": "Board of Equalization",
    "state_board_of_education": "State Board of Education",
    "university_regent": "University Regent",
    "tax_commissioner": "Tax Commissioner",
    "state_lands_commissioner": "Commissioner of State Lands",
}


def _statewide_seat(code: str, name: str) -> tuple[str, str | None]:
    """Attach a seat number to the offices that have one. A district on
    any other statewide office would be a misread — "Secretary of State"
    beside a district number is not a thing — so it is dropped rather
    than stored."""
    if code not in _STATEWIDE_DISTRICT_SEATS:
        return code, None
    match = _STATEWIDE_SEAT_RE.search(name)
    return code, (match.group(1) if match else None)


def parse_statewide_office(contest_name: str) -> tuple[str, str | None] | None:
    """(office code, seat) for a statewide executive contest, or None.

    The seat is None for all but the handful of statewide bodies that
    seat members by district (see _STATEWIDE_DISTRICT_SEATS). None for
    every federal contest too: this is the complement of parse_office,
    not a superset of it, and a caller asks whichever question it means.
    """
    name = contest_name or ""
    # Checked first — see _STATEWIDE_PHRASES for why these cannot go
    # through the general locality gate below.
    for code, pattern in _STATEWIDE_PHRASES:
        if pattern.search(name) and not _STRICT_LOCAL_RE.search(name):
            return _statewide_seat(code, name)
    if _LOCAL_QUALIFIER_RE.search(name):
        return None
    # A JOINT TICKET ("Governor and Lieutenant Governor", how several
    # states print it) is the GOVERNOR's contest, not the deputy's. Strip
    # the lieutenant phrase: if a bare "Governor" survives, both offices
    # are named and this is the top of the ticket.
    if _LT_GOVERNOR_RE.search(name):
        without_lt = _LT_GOVERNOR_RE.sub(" ", name)
        if not re.search(r"\bgovernor\b", without_lt, re.IGNORECASE):
            return _statewide_seat("lt_governor", name)
    for code, pattern in _STATEWIDE_OFFICES:
        if pattern.search(name):
            return _statewide_seat(code, name)
    return None


# ── State legislative seats ──────────────────────────────────────────
#
# The third gate, and the hardest of the three. parse_office must not
# read a state chamber as federal; parse_statewide_office must not read a
# county office as statewide; this one must not read a PARTY COMMITTEE or
# a municipal seat as a legislative seat — and on a real ballot those
# collide word-for-word with the thing being matched.
#
# Every rejection below is a real label off Rhode Island's live 2026
# primary, not a hypothetical:
#
#   Senatorial District Committee District 13   <- "Senatorial", "District"
#   Representative District Committee District 5 <- "Representative", "District"
#   State Committeewoman District 1             <- "State", "District"
#   Ward Committee Cranston Ward 5
#   Pawtucket: City Council Pawtucket District 1 <- "District"
#
# The first three are the dangerous ones: each contains the exact words a
# loose chamber pattern keys on, so a party-committee race would be
# published as a legislative nominee. "Committee" is what actually
# separates them, matched WITHOUT a trailing \b so that
# "Committeewoman"/"Committeeman" are caught by the same rule.
_NON_LEGISLATIVE_RE = re.compile(
    r"\bcommittee"                      # ... and Committeeman/Committeewoman
    r"|\b(?:council|mayor|alderman|school|ward|precinct|municipal"
    r"|county|city|town|township|borough|parish|village"
    r"|judge|justice|court|sheriff|clerk|register)\b"
    r"|:",                              # "Pawtucket: ..." -- one town's race
    re.IGNORECASE,
)

# Chamber patterns, each taken from a real fetched feed. A state whose
# wording is not here yet simply yields None (no legislative nominee
# published) rather than being guessed at — the same refuse-by-default
# stance parse_office takes. Adding a verified state is one entry plus
# the provenance note saying where its wording was read.
#
#   Rhode Island: its legislature is the "General Assembly", so its seats
#   read "Senator in General Assembly District 5" and "Representative in
#   General Assembly District 13" (verified live, 2026 primary: 43 upper
#   and 90 lower contests).
#   Minnesota: the plain "State Senator District 10" / "State
#   Representative District 10A" forms, read off its real 2026 primary
#   export (57 office names, of which these two shapes are the seats).
#   The "State" qualifier is what makes them safe — a bare "Senator,
#   District 5" stays refused, because in a state that prints its federal
#   seats that way it would be a congressional race.
_STATE_LEG_CHAMBERS = [
    ("upper", re.compile(r"\bSenator\s+in\s+General\s+Assembly\b", re.IGNORECASE)),
    ("lower", re.compile(r"\bRepresentative\s+in\s+General\s+Assembly\b", re.IGNORECASE)),
    ("upper", re.compile(r"\bState\s+Senat(?:e|or)\b", re.IGNORECASE)),
    ("lower", re.compile(r"\bState\s+(?:House|Representative)\b", re.IGNORECASE)),
    #   North Carolina: "NC HOUSE OF REPRESENTATIVES DISTRICT 1", with no
    #   "State" in it. A BARE "House of Representatives" is exactly what
    #   parse_office must always refuse — it is a state chamber's name in
    #   most of the country — which is what makes it safe to claim here:
    #   this function refuses anything parse_office recognises before it
    #   looks at a single pattern, so a label reaching this arm is one
    #   that carries no federal marker at all.
    ("lower", re.compile(r"\bHouse\s+of\s+Representatives\b", re.IGNORECASE)),
    #   California: "State Assembly Member District 1" — its lower
    #   chamber. "State" is required so Rhode Island's "General
    #   Assembly", which names the WHOLE legislature and is matched by
    #   the arms above, cannot reach this one.
    ("lower", re.compile(r"\bState\s+Assembly\b", re.IGNORECASE)),
    #   Maryland and West Virginia call their lower chamber the House of
    #   Delegates; Virginia does too. No federal chamber is called that,
    #   so the bare form is unambiguous.
    ("lower", re.compile(r"\bHouse\s+of\s+Delegates\b", re.IGNORECASE)),
    #   Alaska: "Senate District A" / "House District 1" — the chamber
    #   named with no qualifier at all, the shortest form any state uses.
    #   Safe for the same reason the bare "House of Representatives" arm
    #   above is: nothing federal reaches these patterns, because this
    #   gate refuses whatever parse_office claims before testing any of
    #   them. And a districted SENATE seat cannot be federal in any case
    #   — U.S. Senate seats are elected statewide, never by district.
    ("upper", re.compile(r"\bSenate\s+District\b", re.IGNORECASE)),
    ("lower", re.compile(r"\bHouse\s+District\b", re.IGNORECASE)),
]

# Some districts elect SEVERAL members from a single contest, with no
# seat designator to tell the winners apart — Maryland prints "House of
# Delegates District 10 ... Vote for up to 3", and all three of that
# party's top finishers are its nominees.
#
# This is the third multi-member shape after Idaho's "Seat A"/"Seat B"
# and Washington's "Pos. 1"/"Pos. 2", and the only one where the number
# is a property of the CONTEST rather than of the state, so it cannot
# come from config the way advance_count does.
_VOTE_FOR_RE = re.compile(r"\bVote\s+for\s+(?:up\s+to\s+)?(\d+)\b", re.IGNORECASE)


def vote_for_count(contest_name: str) -> int | None:
    """How many seats this contest fills, when its own label says so.

    None when the label is silent, which is most states — the caller
    then keeps whatever its own configuration says.
    """
    match = _VOTE_FOR_RE.search(contest_name or "")
    if not match:
        return None
    count = int(match.group(1))
    # A zero or an absurd count is a label this does not understand, and
    # guessing from it would either drop every candidate or advance the
    # whole field.
    return count if 1 <= count <= 10 else None

# Districts are identified per chamber, and the identifier is the whole
# point — a seat without one is not publishable, because it cannot be
# told apart from the other 74.
#
# The trailing letter is not optional decoration: Minnesota names its
# house districts "10A" and "10B" (two per senate district), so dropping
# it would merge two different seats into one. Leading zeros are stripped
# so "District 05" and "District 5" are the same seat, but the letter is
# kept and upper-cased.
# Arkansas abbreviates it on 15 of its own House contests ("REP State
# Representative Dist. 42") while spelling it out on the rest, so both
# spellings have to read the same seat.
_STATE_LEG_DISTRICT_RE = re.compile(
    r"\bDist(?:rict)?\.?\s+(?:No\.?\s*)?0*(\d+)([A-Za-z]?)\b", re.IGNORECASE,
)

# West Virginia puts the number FIRST: "HOUSE OF DELEGATES, 1st District"
# and "STATE SENATOR, 3rd Senatorial District". One optional word is
# allowed between the ordinal and "District" to carry that "Senatorial".
_STATE_LEG_ORDINAL_DISTRICT_RE = re.compile(
    r"\b0*(\d+)(?:st|nd|rd|th)\s+(?:\w+\s+)?District\b", re.IGNORECASE,
)

# Alaska identifies its senate districts by a LETTER, not a number —
# "Senate District A" through "Senate District T" — pairing each with the
# two numbered house districts it contains. Every other state's district
# identifier is numeric, which is why this is a separate pattern tried
# only after the numeric and ordinal ones fail rather than a relaxation
# of them: "District 10A" must keep parsing as district "10A", not as
# the letter "A".
#
# The single-letter group is anchored on both sides by \b, so it matches
# a letter standing alone as its own word and nothing else. That is what
# keeps "District Attorney" out — the "A" there is followed by "ttorney",
# so there is no word boundary after it (and _NON_LEGISLATIVE_RE already
# refuses that label anyway; this pattern does not rely on it).
_STATE_LEG_LETTER_DISTRICT_RE = re.compile(
    r"\bDist(?:rict)?\.?\s+([A-Za-z])\b", re.IGNORECASE,
)

# SOME STATES ELECT SEVERAL MEMBERS FROM ONE DISTRICT, and the seat is
# what tells their contests apart. Idaho runs "District 1 Seat A" and
# "Seat B"; Washington runs "Pos. 1" and "Pos. 2" of a Legislative
# District. Both are TWO seats in ONE geography.
#
# That is a different thing from Minnesota's "10A"/"10B", which are two
# separate districts with their own boundaries — Census confirms it:
# Minnesota has 134 lower-chamber polygons named 10A, 10B, ..., while
# Idaho has 35 and Washington 49, numbered plainly. So the seat is
# stored beside the district rather than folded into it, and the
# district keeps pointing at the one real polygon both seats share.
_STATE_LEG_SEAT_RE = re.compile(
    r"\bSeat\s+([A-Za-z])\b|\bPos(?:ition|\.)?\s*(\d+)\b", re.IGNORECASE,
)

STATE_LEG_CHAMBER_LABELS = {"upper": "State Senate", "lower": "State House"}

# ── judicial ─────────────────────────────────────────────────────
#
# The fourth gate, and the same refuse-by-default stance as the other
# three: a court whose wording is not here yields None rather than being
# guessed at.
#
# Surveyed live 2026-09-21 across every tabular state's own real export:
# 191 judgeships in five states (GA 92, ID 49, NC 23, FL 23, WA 4), and
# EVERY ONE names a specific seat — "SEAT 2", "Group 4", "Position #1",
# "Seat Bonner A", or the sitting judge's own surname in parentheses.
# Judgeships are single-seat by construction, which is why they escape
# the per-contest seat-count problem that still blocks county and
# municipal offices (a town council electing 5 of 17).
#
# Only NORTH CAROLINA's are partisan, and only those are read today —
# see parse_judicial_office's docstring for why the other 168 need a
# piece of per-state statute this codebase does not have.
JUDICIAL_COURT_LABELS = {
    "supreme": "Supreme Court",
    "appeals": "Court of Appeals",
    "superior": "Superior Court",
    "district": "District Court",
    "circuit": "Circuit Court",
}

_JUDICIAL_COURTS = [
    # Longest/most specific first: "Court of Appeals" must win before a
    # bare "Court", and "Supreme Court" before "Superior Court" — they
    # share no prefix but the ordering rule is what keeps it that way.
    ("supreme", re.compile(r"\bSupreme\s+Court\b", re.IGNORECASE)),
    ("appeals", re.compile(r"\bCourt\s+of\s+Appeals\b", re.IGNORECASE)),
    ("superior", re.compile(r"\bSuperior\s+Court\b", re.IGNORECASE)),
    ("district", re.compile(r"\bDistrict\s+Court\b", re.IGNORECASE)),
    # LAST on purpose. Georgia writes "Judge - Superior Court - Alcovy
    # Judicial Circuit", where the circuit is the GEOGRAPHY of a superior
    # court seat, not the court itself — the superior arm above has to
    # win there. Florida's "Circuit Judge, 9th Judicial Circuit, Group 1"
    # names the court itself and reaches this arm only because nothing
    # above claimed it.
    ("circuit", re.compile(r"\bCircuit\s+(?:Court|Judge)\b", re.IGNORECASE)),
]

# The office must be the JUDGESHIP itself.
_JUDICIAL_TITLE_RE = re.compile(r"\b(?:judge|justice)\b", re.IGNORECASE)

# Every other office that names a court. A clerk of superior court and a
# district attorney both sit squarely inside a court's name and are not
# judgeships — North Carolina's own export carries 26 clerk contests and
# Georgia's 10 district-attorney ones, all of which must be refused.
_NON_JUDGESHIP_RE = re.compile(
    r"\b(?:clerk|attorney|solicitor|sheriff|magistrate|register|"
    r"reporter|marshal|constable)\b",
    re.IGNORECASE,
)

# A trial court's geography is a numbered CIRCUIT in some states rather
# than a numbered district — Florida's "9th Judicial Circuit". Written
# as an ordinal, which is why neither the plain district pattern ("Dist.
# 3") nor the legislative ordinal one (which requires the word
# "District") sees it.
_JUDICIAL_CIRCUIT_RE = re.compile(
    r"\b0*(\d+)(?:st|nd|rd|th)\s+Judicial\s+Circuit\b", re.IGNORECASE,
)

# NC writes "SEAT 2"; the state-legislative seat pattern only accepts a
# single letter (Idaho's "Seat A"), so judicial needs its own.
_JUDICIAL_SEAT_RE = re.compile(
    r"\bSeat\s+(\w+)\b|\bGroup\s+(\w+)\b|\bPos(?:ition|\.)?\s*#?\s*(\w+)\b",
    re.IGNORECASE,
)


def parse_judicial_office(
    contest_name: str,
) -> tuple[str, str | None, str | None] | None:
    """(court, district, seat) for an elected judgeship, or None.

    ("district", "3", "2") for North Carolina's "NC DISTRICT COURT JUDGE
    DISTRICT 3 SEAT 2 (REP)", and ("appeals", None, "4") for a statewide
    appellate seat, which has no district.

    None for everything the other three gates answer, and None for every
    other office that merely names a court — a clerk of superior court
    is not a judge, and neither is a district attorney.

    WHY ONLY PARTISAN JUDICIAL CONTESTS ARE READ. In a party primary the
    winner becomes that party's nominee and appears on the November
    ballot, which is what this whole pipeline already models. A
    NON-partisan judicial election inverts it: in most states a
    candidate who clears a majority is ELECTED OUTRIGHT rather than
    nominated, and only a field that fails to produce one sends two
    candidates on to November. Reading those the same way would publish
    a judge who has already won as though they were still standing —
    the confidently-wrong failure this system refuses everywhere else.
    Which of the two a state does is its own statute and appears in no
    feed, so the 168 non-partisan judgeships surveyed stay omitted until
    that rule is hand-verified per state, exactly as
    `runoff_threshold_pct` already is.
    """
    name = contest_name or ""
    if not _JUDICIAL_TITLE_RE.search(name):
        return None
    if _NON_JUDGESHIP_RE.search(name):
        return None
    # Never claim what an earlier gate answers. Federal judges are
    # appointed, so nothing federal is a judgeship, but the check keeps
    # this gate self-contained rather than order-dependent — the same
    # reason parse_state_leg_office refuses federal labels itself.
    if parse_office(name) is not None:
        return None

    for court, pattern in _JUDICIAL_COURTS:
        if not pattern.search(name):
            continue
        district = None
        match = _STATE_LEG_DISTRICT_RE.search(name)
        if match:
            district = match.group(1) + match.group(2).upper()
        else:
            # A numbered CIRCUIT is the same thing by another name, and
            # is what Florida's trial seats are keyed by.
            circuit = _JUDICIAL_CIRCUIT_RE.search(name)
            if circuit:
                district = circuit.group(1)
        seat_match = _JUDICIAL_SEAT_RE.search(name)
        seat = None
        if seat_match:
            seat = next((g for g in seat_match.groups() if g), None)
            if seat:
                # "SEAT 01" is seat 1. Stripped for the same reason the
                # district pattern strips its own zeros: the state pads
                # inconsistently within one file, and an unpadded "SEAT 3"
                # and a padded "SEAT 03" are the same bench.
                seat = seat.lstrip("0") or "0" if seat.isdigit() else seat.upper()
        return court, district, seat
    return None


def district_sort_key(district: str) -> tuple[int, str]:
    """Natural order for a district identifier: 9 before 10, and 10A
    before 10B. Lexical order would put "10" before "9" and scatter
    Minnesota's A/B pairs across the list."""
    match = re.match(r"(\d+)([A-Za-z]*)$", str(district))
    return (int(match.group(1)), match.group(2)) if match else (10**9, str(district))


def parse_state_leg_office(contest_name: str) -> tuple[str, str, str | None] | None:
    """(chamber, district, seat) for a state legislative seat, or None.

    ("upper", "5", None) for Rhode Island's Senate District 5,
    ("lower", "10A", None) for one of Minnesota's two real districts,
    ("lower", "1", "A") for Idaho's Seat A of District 1, and
    ("lower", "5", "2") for Washington's Position 2 of District 5.

    The seat is None wherever a district elects a single member, which
    is most of the country. Where it is set, the district still names
    the one geography both seats share — see _STATE_LEG_SEAT_RE.

    None for federal and statewide-executive contests too: like its two
    siblings this answers one question, and a caller asks whichever it
    means. In particular no pattern here can match a federal label —
    Rhode Island's own "Senator in Congress" and "Senator in General
    Assembly" sit on the same ballot, and only the second is ours.
    """
    name = contest_name or ""
    if _NON_LEGISLATIVE_RE.search(name):
        return None
    # Refused here rather than relying on the caller having asked
    # parse_office first. The bare "House of Representatives" arm below
    # is only safe because nothing federal reaches it, and a contract
    # that depends on call order is one that breaks the first time
    # somebody calls this function on its own.
    if parse_office(name) is not None:
        return None
    for chamber, pattern in _STATE_LEG_CHAMBERS:
        if pattern.search(name):
            district = _STATE_LEG_DISTRICT_RE.search(name)
            if district:
                number = district.group(1) + district.group(2).upper()
            else:
                ordinal = _STATE_LEG_ORDINAL_DISTRICT_RE.search(name)
                if ordinal:
                    number = ordinal.group(1)
                else:
                    # Last, so a numeric district is never read as a letter.
                    letter = _STATE_LEG_LETTER_DISTRICT_RE.search(name)
                    if not letter:
                        return None
                    number = letter.group(1).upper()
            seat_match = _STATE_LEG_SEAT_RE.search(name)
            seat = None
            if seat_match:
                seat = (seat_match.group(1) or seat_match.group(2) or "").upper() or None
            return chamber, number, seat
    return None


def district_label(district: str, seat: str | None) -> str:
    """How a seat is written on the page. "1A" for a lettered seat, the
    way Idaho itself writes it; "5-2" for a numbered position, which no
    state prints exactly but which stays compact, unambiguous and
    obviously two seats of one district when both rows sit together."""
    if not seat:
        return district
    return f"{district}{seat}" if seat.isalpha() else f"{district}-{seat}"


def office_from_columns(row: dict, spec: dict | None) -> tuple[str, int | None] | None:
    """The same ("H", 3) answer as parse_office, taken from a results row's
    OWN columns instead of its label, or None when `spec` is unset or the
    row isn't the federal office it describes.

    Some states name the office in a way that is only unambiguous next to
    another column — Virginia's is "Member, House of Representatives (2nd
    District)", with no "U.S." prefix (which parse_office must keep
    refusing, since that is a STATE chamber's name in many states) and an
    ordinal it doesn't read. Those states carry their own district-type
    column, and it says plainly what the label can't.

    Every value here comes from the state's entry in
    state_candidate_sources.json, never from code: the discriminating
    column, the value that marks a congressional seat, and the column
    holding the district number. A state that publishes a column with some
    other name and some other marker is still a config entry.
    """
    if not spec:
        return None
    marker = (row.get(spec.get("type_column")) or "").strip().casefold()
    if not marker or marker != str(spec.get("type_value") or "").strip().casefold():
        return None
    # Zero-padded ("05") in Virginia's export; an at-large seat has no
    # number at all, which parse_office already models as district None.
    digits = re.search(r"\d+", row.get(spec.get("district_column")) or "")
    return "H", int(digits.group()) if digits else None


# "Independent"/"Unaffiliated", spelled the ways a ballot list writes
# them. Read only when a caller says it is looking at a BALLOT, never at
# primary results — see normalize_party.
# Abbreviations are matched only as the WHOLE value (a party column),
# never inside a longer label, for the same reason a stray "R" in a
# contest name must not become a Republican.
# NOPTY is Louisiana's "No Party"; PETITION is South Carolina's label for
# a candidate who reached the ballot by petition rather than a party, and
# Nebraska writes the same thing out as "By Petition".
_INDEPENDENT_ABBR = frozenset({"IND", "INDEPENDENT", "UNA", "NPA", "NOP", "NP", "NOPTY", "PETITION"})
_INDEPENDENT_RE = re.compile(
    r"\b(independent|unaffiliated|no\s+party(\s+affiliation)?|non[\s-]?partisan|by\s+petition)\b",
    re.IGNORECASE,
)


def normalize_party(text: str, ballot_list: bool = False) -> str | None:
    """Single-letter party code for `text` (a contest label or a party
    column), or None when no party is positively recognised. Never defaults
    to a major party: an unattributable contest is skipped instead.

    "Unaffiliated"/"Independent" yield None by default — those candidates
    don't run in a party primary, so a party-PRIMARY contest that appears
    to be theirs is a label this doesn't understand, not a race to confirm.

    `ballot_list` says the caller is reading a certified GENERAL-election
    ballot rather than primary results, where that reasoning inverts: an
    independent is a perfectly ordinary entry, and dropping them loses
    exactly the candidate a ballot list exists to show. South Dakota's
    Senate race is the live case — Brian Bengs (IND) is on the November
    ballot and no primary-results source can ever see him. PARTY_CODE_MAP
    already carries "I" -> "IND", so nothing downstream needed changing;
    only this refused to produce it.
    """
    value = (text or "").strip()
    if ballot_list and (
        value.upper() in _INDEPENDENT_ABBR or _INDEPENDENT_RE.search(value)
    ):
        return "I"
    # A dedicated party column sometimes holds nothing but the letter
    # ("R", "D" — Minnesota's results file). That is only safe to read
    # when the WHOLE value is the code: a contest label containing a
    # stray "R" must never become a Republican.
    if value.upper() in _SINGLE_LETTER_PARTIES:
        return value.upper()
    for pattern, code in _PARTY_PATTERNS:
        if pattern.search(value):
            return code
    return None


async def discover_certification_link(
    client, rate_limiter, page_url: str, link_regex: str, year: int, label: str,
) -> str | None:
    """The one link on `page_url` matching `link_regex` (with `{year}`
    filled in), resolved against the page, or None.

    For the certified-ballot strategies: a state posts the certification
    on a landing page that stays put while the file name changes every
    cycle, so the link is found, never pinned. None for both "not posted
    yet" and "more than one match" — guessing which of two
    certifications is current would be the one wrong answer."""
    page_url = page_url.replace("{year}", str(year))
    page = await fetch_text_with_retry(client, rate_limiter, page_url, f"{label} certification page")
    if page is None:
        return None
    links = {m.group(1) for m in re.finditer(link_regex.replace("{year}", str(year)), page)}
    if len(links) != 1:
        logger.info("%s certification page links %d %d certifications", label, len(links), year)
        return None
    return urljoin(page_url, links.pop())


def federal_record(
    office: str, district: int | None, party: str | None, name: str,
    *, last_first: bool = False,
) -> dict | None:
    """The record every strategy returns for a federal candidate, built
    from the name as the state printed it, or None when that name yields
    no surname.

    `last_name` is what the matcher compares with FEC's surname.
    `display_name` is the printed name, kept for the two things a surname
    cannot do: tell apart two same-party candidates who share a surname
    (TX-34's Eric and Mayra Flores), and show a candidate who is on the
    ballot but never filed with the FEC, who has no FEC row to show
    instead. A strategy that reduces names to surnames before picking a
    winner loses the second one silently, which is why this takes the
    full name and does the reduction itself.
    """
    last = surname(name, last_first=last_first)
    if not last:
        return None
    return {
        "office": office, "district": district, "party": party,
        "last_name": last, "display_name": clean_display_name(name),
    }


def clean_display_name(display_name: str) -> str:
    """A ballot name with its annotations removed, kept otherwise
    verbatim — "Aaron C. Guckian*" -> "Aaron C. Guckian".

    The same annotations surname() strips, stripped for the same reason:
    they are markers the state prints beside a name, not part of it.
    Rhode Island's bare asterisk means the party ENDORSED that candidate
    (and the endorsee does not always win), Georgia's "(I)" means
    incumbent. Rendered as-is, both read as a typo in a person's name,
    or worse as a footnote marker pointing at a footnote that does not
    exist on the page.
    """
    return re.sub(r"\s+", " ", re.sub(_ANNOTATION_RE, " ", display_name or "")).strip()


def surname(display_name: str, last_first: bool = False) -> str | None:
    """Trailing token of a "First [Middle] Last" display name, which is
    what the shared matcher compares against the surname FEC stores before
    the comma. "Robert Cruz Jr." -> "Cruz".

    Ballot annotations are stripped first, since no legal name carries
    one: Georgia's names carry a parenthetical incumbency marker, and
    "Earl L. Carter (I)" would otherwise yield a surname of "(I)" for
    every sitting member in the state; Rhode Island appends a bare
    asterisk to its party-ENDORSED candidates (verified live against its
    2026 primary -- "John F. Reed*", and note the endorsee does not
    always win: endorsed "Stephen T. Skoly*" lost RI-2's Republican
    primary to unendorsed "Victor Mellor"), which would otherwise yield
    "Reed*" and match no FEC row at all.
    """
    # Some states print the ballot name the way FEC files it — "CASE, Ed",
    # "Darden, Dustin Thomas House" — where the surname is everything
    # BEFORE the comma and the trailing token is a middle name. Taking the
    # last token there is not a near miss, it is a different person's name.
    if last_first and "," in (display_name or ""):
        head = (display_name or "").split(",")[0].strip()
        return re.sub(r"\s+", " ", re.sub(_ANNOTATION_RE, " ", head)).strip() or None
    cleaned = re.sub(_ANNOTATION_RE, " ", display_name or "")
    tokens = [t for t in re.split(r"\s+", cleaned.strip()) if t]
    while tokens and tokens[-1].strip(".,").lower() in _NAME_SUFFIXES:
        tokens.pop()
    if not tokens:
        return None
    return tokens[-1].strip(".,")


# How a non-partisan judicial contest RESOLVES relative to the November
# ballot. Three real states, three different answers — which is exactly
# why this cannot be one blanket rule inferred from advance_count, and
# why a state is not opted in until its own statute has been read.
#
#   "sole_candidate"  Washington. RCW 29A.36.170: "only the name of that
#                     candidate may be printed for that position on the
#                     ballot at the general election". The winner IS a
#                     November candidate, standing alone.
#   "elects"          Idaho. Idaho Code 34-1217 certifies the majority
#                     winner "as duly elected to such office" and says
#                     "he shall not be required to stand for election at
#                     the general election following". The seat is gone
#                     from the November ballot entirely, so publishing
#                     that judge as a candidate would be wrong.
JUDICIAL_RESOLUTION_SOLE_CANDIDATE = "sole_candidate"
JUDICIAL_RESOLUTION_ELECTS = "elects"
# Georgia. O.C.G.A. 21-2-138 puts EVERY judicial office -- superior
# court, Court of Appeals, Supreme Court, state court -- "on the ballot
# in a nonpartisan election to be held and conducted jointly with the
# general primary", and 21-2-285.1 declares "duly elected" either the
# candidate with a majority there OR the winner of the nonpartisan
# RUNOFF. Both branches finish months before November, so no Georgia
# judgeship is ever on the general ballot and none is published. This is
# why the field describes RESOLUTION and not only what a majority does:
# Georgia's no-majority branch goes to a June runoff, where Idaho's and
# Florida's go to November.
JUDICIAL_RESOLUTION_DECIDED_EARLY = "decided_before_general"


def pick_nominees(
    choices: list[tuple[str, int]],
    runoff_threshold_pct: float | None = None,
    advance_count: int = 1,
    judicial_resolution: str | None = None,
) -> list[tuple[str, float]]:
    """Who advances to the general from one contest, as [(name, pct), ...]
    from [(name, votes), ...]. Empty when nobody can be named safely.

    `advance_count` is the state's own rule, not a tuning knob. Most states
    run a party primary and send ONE nominee per party. The top-two states
    (CA, WA) and top-four (AK) run a single all-party contest and advance
    that many regardless of party, so a top-two contest legitimately sends
    two candidates of the SAME party to the general — treating it as a
    one-winner race would silently drop a real ballot option.

    Nobody advances on: an empty field, or no votes cast yet. A tie
    spanning the cutoff truncates to whoever is strictly above it — the
    state resolves ties by recount or draw, and guessing which tied
    candidate advanced would be fabrication.

    `runoff_threshold_pct` applies only to one-nominee party primaries,
    where it is the load-bearing safety rule: states that send a
    sub-threshold leader somewhere else to be decided would otherwise have
    that candidate confirmed as the winner of a race still being settled.
    "Somewhere else" is not always a runoff — it is a second primary in NC
    (30%) and the 50% runoff states TX/GA/MS/AL/AR/OK/SC, but a party
    CONVENTION in Iowa (35%, Iowa Code 43.52), where a sub-threshold
    leader may end up not being the nominee at all. The threshold is the
    state's own rule from config either way.

    `judicial_resolution` answers a different question from that
    threshold, and the two must not be confused. A threshold WITHHOLDS a
    leader who failed to clear it, because the contest is still being
    settled elsewhere. This says what a majority MEANS — and the two
    states that have one mean opposite things by it, which is why it is
    a value rather than a flag and why no state gets it by inference:

      "sole_candidate"  Washington (RCW 29A.36.170) leaves that one name
                        on the general ballot. Read as an ordinary
                        top-two contest, two of its four 2026 Supreme
                        Court seats would have published a runner-up who
                        appears on no ballot (Position 1, 53.0%;
                        Position 7, 58.1%).
      "elects"          Idaho (Idaho Code 34-1217) seats the winner
                        outright — "shall not be required to stand for
                        election at the general election following" — so
                        the seat is absent from November and NOBODY is
                        published for it.

    Deliberately NOT inferred from advance_count: in the same state and
    the same file, an ordinary top-two race still advances two whatever
    the leader's share, so this is a per-contest-kind rule a state's own
    entry has to state.
    """
    ranked = sorted(
        [(n, v) for n, v in choices if isinstance(v, int) and v > 0],
        key=lambda t: t[1], reverse=True,
    )
    if not ranked:
        return []

    # Applied BEFORE the cutoff, because it changes what the cutoff is.
    # "A majority of all the votes cast" is strictly more than half, so a
    # dead-even field has no majority and both names reach the general.
    if judicial_resolution == JUDICIAL_RESOLUTION_DECIDED_EARLY:
        # Settled before November either way — by a majority here or by a
        # runoff that is itself held before the general.
        return []
    if judicial_resolution and advance_count > 1:
        cast = sum(v for _, v in ranked)
        if cast and ranked[0][1] * 2 > cast:
            if judicial_resolution == JUDICIAL_RESOLUTION_ELECTS:
                # Seated already; the seat is not on the November ballot,
                # so there is no candidate to publish for it.
                return []
            advance_count = 1

    # Truncate at a tie that straddles the cutoff: everyone strictly above
    # the tied vote count advanced, and who broke the tie isn't ours to say.
    cutoff = min(advance_count, len(ranked))
    if cutoff < len(ranked) and ranked[cutoff - 1][1] == ranked[cutoff][1]:
        tied_at = ranked[cutoff][1]
        ranked = [r for r in ranked if r[1] > tied_at]
        if not ranked:
            return []
    else:
        ranked = ranked[:cutoff]

    total = sum(v for _, v in choices if isinstance(v, int) and v > 0)
    out = [(n, 100.0 * v / total if total else 0.0) for n, v in ranked]

    if advance_count == 1 and runoff_threshold_pct is not None:
        # ponytail: withholds the contest until the leader clears the bar;
        # the upgrade is fetching that state's second-primary/runoff feed
        # and merging it in.
        out = [(n, p) for n, p in out if p >= runoff_threshold_pct]
    return out


def pick_nominee(
    choices: list[tuple[str, int]], runoff_threshold_pct: float | None,
) -> tuple[str, float] | None:
    """Single-nominee convenience wrapper for the party-primary adapters."""
    won = pick_nominees(choices, runoff_threshold_pct, advance_count=1)
    return won[0] if won else None


def resolve_confirmed_nominees(
    by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]],
    runoff_threshold_pct: float | None,
    name_transform: Callable[[str], str | None] | None = None,
) -> list[dict]:
    """Every {"office", "district", "party", "last_name"} record this
    by-seat grouping resolves to via the shared tie-safe pick_nominee --
    the "group real vote choices by seat, pick a safe winner, shape the
    confirmed-candidate record" tail that was hand-copied, near-
    identically, in OR/VT/MA/KS/MS/TotalVote before being pulled in here
    (crossing this system's own "3 strikes" extraction bar well before
    the pull actually happened — flagged independently across four PR
    reviews and deliberately deferred each time to keep those PRs
    scoped). A seat with no safely-nameable winner (a tie, a sub-
    threshold leader, an empty field) contributes nothing, never a
    guess -- matching pick_nominee's own withholding behavior exactly.

    `name_transform`, when given, runs on the winning name AFTER
    pick_nominee has already resolved it from the real vote totals, and
    the seat is skipped if it returns a falsy result. This is for a
    state whose raw choices carry a full display name rather than an
    already-reduced surname (Vermont) -- reducing every choice to a
    surname BEFORE pick_nominee would risk quietly dropping a real
    candidate's votes from the denominator if their own reduction were
    ever empty, the same "never drop real votes from the total" rule
    this system's Oregon/Arkansas/North Dakota modules already apply.
    """
    results = []
    for key, choices in by_seat.items():
        # A fourth element is the SEAT, for the states that elect more
        # than one member from a district (see parse_state_leg_office).
        # Optional so every federal caller's three-part key still works
        # unchanged.
        office, district, party = key[0], key[1], key[2]
        seat = key[3] if len(key) > 3 else None
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if not won:
            continue
        name = name_transform(won[0]) if name_transform else won[0]
        if name:
            record = {
                "office": office, "district": district,
                "party": party, "last_name": name,
            }
            if name_transform and office in ("S", "H"):
                # The whole printed name, kept beside the surname the
                # matcher reads — see federal_record.
                record["display_name"] = clean_display_name(won[0])
            # Present only where a district really has more than one
            # seat. A federal or single-member record carries no seat
            # concept at all, so it carries no key either.
            if seat is not None:
                record["seat"] = seat
            results.append(record)
    return results


class DiscoveryFailed(Exception):
    """Raised by a strategy's own discovery step on a genuine fetch/parse
    failure (network error, malformed list/detail response, a matched
    record missing a field it should always have) -- never for a healthy
    "nothing published for this cycle yet" or "no match this year", both
    of which a strategy should signal by returning None (or []) from its
    own discovery function instead. Collapsing the two would silently
    report a broken feed as a healthy empty cycle -- originally a private
    class duplicated near-word-for-word in state_candidates_or.py and
    state_candidates_vt.py before being pulled in here."""
