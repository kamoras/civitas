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

# Chamber wording varies ("United States Congress", "US HOUSE OF
# REPRESENTATIVES", "U.S. Representative"); the ordinal in Colorado's label
# advances every Congress, so nothing cycle-specific is matched.
_CHAMBER_HOUSE = (
    r"(?:United\s+States\s+Congress|U\.?\s*S\.?\s*(?:House|Representative))"
)
_HOUSE_DISTRICT_RE = re.compile(
    rf"{_CHAMBER_HOUSE}.*?District\s+0*(\d+)", re.IGNORECASE | re.DOTALL,
)
_HOUSE_RE = re.compile(_CHAMBER_HOUSE, re.IGNORECASE)
# "Senator" (Colorado) and "Senate" (North Carolina) both appear live.
_SENATE_RE = re.compile(
    r"(?:United\s+States|U\.?\s*S\.?)\s*Senat(?:e|or)", re.IGNORECASE,
)

# Spelled-out names and the states' own abbreviations both occur; the
# shared matcher in state_candidates.py speaks single-letter codes.
_PARTY_PATTERNS = [
    (re.compile(r"\b(?:democratic|democrat|dem)\b", re.IGNORECASE), "D"),
    (re.compile(r"\b(?:republican|rep|gop)\b", re.IGNORECASE), "R"),
    (re.compile(r"\b(?:libertarian|lib)\b", re.IGNORECASE), "L"),
    (re.compile(r"\b(?:green|gre)\b", re.IGNORECASE), "G"),
    (re.compile(r"\b(?:constitution|con|cst)\b", re.IGNORECASE), "C"),
]

# Generational suffixes must not be mistaken for a surname.
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


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
    m = _HOUSE_DISTRICT_RE.search(name)
    if m:
        return "H", int(m.group(1))
    if _HOUSE_RE.search(name):
        return "H", None
    return None


def normalize_party(text: str) -> str | None:
    """Single-letter party code for `text` (a contest label or a party
    column), or None when no party is positively recognised. Never defaults
    to a major party: an unattributable contest is skipped instead.

    "Unaffiliated"/"Independent" deliberately yield None — those candidates
    don't run in a party primary, so a party-primary contest that appears
    to be theirs is a label this doesn't understand, not a race to confirm.
    """
    for pattern, code in _PARTY_PATTERNS:
        if pattern.search(text or ""):
            return code
    return None


def surname(display_name: str) -> str | None:
    """Trailing token of a "First [Middle] Last" display name, which is
    what the shared matcher compares against the surname FEC stores before
    the comma. "Robert Cruz Jr." -> "Cruz"."""
    tokens = [t for t in re.split(r"\s+", (display_name or "").strip()) if t]
    while tokens and tokens[-1].strip(".,").lower() in _NAME_SUFFIXES:
        tokens.pop()
    if not tokens:
        return None
    return tokens[-1].strip(".,")


def pick_nominee(
    choices: list[tuple[str, int]], runoff_threshold_pct: float | None,
) -> tuple[str, float] | None:
    """Winner of one contest as (name, pct) from [(name, votes), ...], or
    None when no nominee can be named safely.

    None on: an empty field, no votes cast yet, an exact tie for the lead
    (the state resolves those by recount/runoff/draw — guessing either way
    would be fabrication), or a leader who failed to clear a runoff state's
    threshold.

    The threshold is the load-bearing safety rule. Where a plurality wins
    outright the top vote-getter IS the nominee, but states that send a
    sub-threshold leader to a second primary (NC at 30%, and the 50% runoff
    states TX/GA/MS/AL/AR/OK/SC) would otherwise have a runoff-bound
    candidate confirmed as the winner of a race still being decided.
    """
    ranked = sorted(
        [(n, v) for n, v in choices if isinstance(v, int)],
        key=lambda t: t[1], reverse=True,
    )
    if not ranked or ranked[0][1] <= 0:
        return None
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None

    total = sum(v for _, v in ranked)
    pct = 100.0 * ranked[0][1] / total if total else 0.0
    if runoff_threshold_pct is not None and pct < runoff_threshold_pct:
        # ponytail: withholds the contest until the leader clears the bar;
        # the upgrade is fetching that state's second-primary/runoff feed
        # and merging it in.
        return None
    return ranked[0][0], pct
