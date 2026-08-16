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
    r"(?:United\s+States\s+(?:Congress|Representative)"
    r"|U\.?\s*S\.?\s*(?:House|Representative))"
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
    the comma. "Robert Cruz Jr." -> "Cruz".

    Parenthetical annotations are stripped first: Georgia's ballot names
    carry an incumbency marker, and "Earl L. Carter (I)" would otherwise
    yield a surname of "(I)" for every sitting member in the state.
    """
    cleaned = re.sub(r"\([^)]*\)", " ", display_name or "")
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
    sub-threshold leader to a second primary (NC at 30%, and the 50%
    runoff states TX/GA/MS/AL/AR/OK/SC) would otherwise have a
    runoff-bound candidate confirmed as the winner of a race still being
    decided.
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
