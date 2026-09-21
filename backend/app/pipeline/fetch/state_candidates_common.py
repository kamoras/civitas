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

import re
from collections.abc import Callable

# Chamber wording varies ("United States Congress", "US HOUSE OF
# REPRESENTATIVES", "U.S. Representative", Arkansas's real "U.S. Congress
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
    r"(?:United\s+States\s+(?:Congress\b|Representative)"
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
    ("auditor", re.compile(r"\b(?:state|general)\s+auditor\b", re.IGNORECASE)),
]

_LT_GOVERNOR_RE = re.compile(r"\b(?:lieutenant|lt\.?)\s+governor\b", re.IGNORECASE)

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


STATEWIDE_OFFICE_LABELS = {
    "governor": "Governor",
    "lt_governor": "Lieutenant Governor",
    "attorney_general": "Attorney General",
    "secretary_of_state": "Secretary of State",
    "treasurer": "State Treasurer",
    "auditor": "State Auditor",
}


def parse_statewide_office(contest_name: str) -> str | None:
    """A statewide executive office code, or None for anything else.

    None for every federal contest too: this is the complement of
    parse_office, not a superset of it, and a caller asks whichever
    question it means.
    """
    name = contest_name or ""
    if _LOCAL_QUALIFIER_RE.search(name):
        return None
    # A JOINT TICKET ("Governor and Lieutenant Governor", how several
    # states print it) is the GOVERNOR's contest, not the deputy's. Strip
    # the lieutenant phrase: if a bare "Governor" survives, both offices
    # are named and this is the top of the ticket.
    if _LT_GOVERNOR_RE.search(name):
        without_lt = _LT_GOVERNOR_RE.sub(" ", name)
        if not re.search(r"\bgovernor\b", without_lt, re.IGNORECASE):
            return "lt_governor"
    for code, pattern in _STATEWIDE_OFFICES:
        if pattern.search(name):
            return code
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
]

# Districts are identified per chamber, and the identifier is the whole
# point — a seat without one is not publishable, because it cannot be
# told apart from the other 74.
#
# The trailing letter is not optional decoration: Minnesota names its
# house districts "10A" and "10B" (two per senate district), so dropping
# it would merge two different seats into one. Leading zeros are stripped
# so "District 05" and "District 5" are the same seat, but the letter is
# kept and upper-cased.
_STATE_LEG_DISTRICT_RE = re.compile(
    r"\bDistrict\s+(?:No\.?\s*)?0*(\d+)([A-Za-z]?)\b", re.IGNORECASE,
)

STATE_LEG_CHAMBER_LABELS = {"upper": "State Senate", "lower": "State House"}


def district_sort_key(district: str) -> tuple[int, str]:
    """Natural order for a district identifier: 9 before 10, and 10A
    before 10B. Lexical order would put "10" before "9" and scatter
    Minnesota's A/B pairs across the list."""
    match = re.match(r"(\d+)([A-Za-z]*)$", str(district))
    return (int(match.group(1)), match.group(2)) if match else (10**9, str(district))


def parse_state_leg_office(contest_name: str) -> tuple[str, str] | None:
    """("upper", "5") / ("lower", "10A") for a state legislative seat, or
    None.

    None for federal and statewide-executive contests too: like its two
    siblings this answers one question, and a caller asks whichever it
    means. In particular no pattern here can match a federal label —
    Rhode Island's own "Senator in Congress" and "Senator in General
    Assembly" sit on the same ballot, and only the second is ours.
    """
    name = contest_name or ""
    if _NON_LEGISLATIVE_RE.search(name):
        return None
    for chamber, pattern in _STATE_LEG_CHAMBERS:
        if pattern.search(name):
            district = _STATE_LEG_DISTRICT_RE.search(name)
            if not district:
                return None
            return chamber, district.group(1) + district.group(2).upper()
    return None


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


def normalize_party(text: str) -> str | None:
    """Single-letter party code for `text` (a contest label or a party
    column), or None when no party is positively recognised. Never defaults
    to a major party: an unattributable contest is skipped instead.

    "Unaffiliated"/"Independent" deliberately yield None — those candidates
    don't run in a party primary, so a party-primary contest that appears
    to be theirs is a label this doesn't understand, not a race to confirm.
    """
    value = (text or "").strip()
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


def pick_nominees(
    choices: list[tuple[str, int]],
    runoff_threshold_pct: float | None = None,
    advance_count: int = 1,
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
    """
    ranked = sorted(
        [(n, v) for n, v in choices if isinstance(v, int) and v > 0],
        key=lambda t: t[1], reverse=True,
    )
    if not ranked:
        return []

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
    for (office, district, party), choices in by_seat.items():
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if not won:
            continue
        name = name_transform(won[0]) if name_transform else won[0]
        if name:
            results.append({"office": office, "district": district, "party": party, "last_name": name})
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
