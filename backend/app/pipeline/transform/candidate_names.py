"""Deterministic detection of a candidate's own money in donor records.

The embedding-based donor-type classifier misses many "Lastname, Firstname"
self-loan records (the 2026-07 adversarial audit found 19 senators with
their own money typed as Org/Employees inside their top-10 external donors
— e.g. "Scott, Rick — $19,000,000"). Those records corrupted the Funding
Independence concentration signal (self-funders scored as "captured by a
top donor") and surfaced senators as their own biggest donor-interest
match in the UI.

This module is a conservative, rule-based guard: it only matches when the
candidate's last name appears in the donor name AND every other donor-name
token is compatible with the candidate's name (same token, a known
nickname of the first name, or an initial). Committees ("Kim For
Congress") don't match because "for"/"congress" are incompatible tokens.
"""

import re
import unicodedata

# Common given-name/nickname equivalences among current members.
# Bidirectional: lookup normalizes both sides.
_NICKNAMES: dict[str, set[str]] = {
    "david": {"dave"},
    "michael": {"mike"},
    "james": {"jim", "jimmy"},
    "thomas": {"tom", "tommy", "thom"},
    "bernard": {"bernie"},
    "peter": {"pete"},
    "jeffrey": {"jeff"},
    "jeffery": {"jeff"},
    "andrew": {"andy", "drew"},
    "charles": {"chuck", "charlie"},
    "richard": {"rick", "dick", "rich"},
    "robert": {"bob", "rob", "bobby"},
    "william": {"bill", "will", "billy"},
    "timothy": {"tim"},
    "daniel": {"dan", "danny"},
    "christopher": {"chris"},
    "katherine": {"katie", "kate", "kathy"},
    "margaret": {"maggie", "meg"},
    "benjamin": {"ben"},
    "edward": {"ed", "ted", "eddie"},
    "theodore": {"ted"},
    "joseph": {"joe", "joey"},
    "john": {"jack", "johnny"},
    "steven": {"steve"},
    "stephen": {"steve"},
    "gregory": {"greg"},
    "raphael": {"rafael"},
    "cynthia": {"cindy"},
    "deborah": {"deb", "debbie"},
    "elizabeth": {"liz", "beth", "betty"},
    "patricia": {"patty", "pat"},
    "nicholas": {"nick"},
    "anthony": {"tony"},
    "lawrence": {"larry"},
    "ronald": {"ron", "ronny"},
    "donald": {"don"},
    "kenneth": {"ken"},
    "samuel": {"sam"},
    "joshua": {"josh"},
    "matthew": {"matt"},
    "jonathan": {"jon"},
    "angus": set(),
}

_HONORIFICS = {
    "mr", "mrs", "ms", "dr", "hon", "sen", "senator", "rep",
    "representative", "gov", "governor", "jr", "sr", "ii", "iii", "iv",
    "esq", "md", "phd",
}


def _norm_tokens(name: str) -> list[str]:
    """Lowercase, strip accents/punctuation, drop honorifics and suffixes."""
    nfkd = unicodedata.normalize("NFKD", name or "")
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    tokens = re.split(r"[^a-z]+", ascii_name.lower())
    return [t for t in tokens if t and t not in _HONORIFICS]


def _names_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    return b in _NICKNAMES.get(a, set()) or a in _NICKNAMES.get(b, set())


def is_candidate_self_donor(donor_name: str, candidate_name: str) -> bool:
    """True when a donor record is (conservatively) the candidate themselves.

    Requires the candidate's last name to appear in the donor name, and
    every other donor token to be a candidate-name token, a nickname of
    one, or an initial. Anything unexplained ("bank", "congress", another
    first name) means no match.
    """
    d_tokens = _norm_tokens(donor_name)
    c_tokens = _norm_tokens(candidate_name)
    if not d_tokens or not c_tokens:
        return False

    c_last = c_tokens[-1]
    if c_last not in d_tokens:
        return False

    matched_given = False
    for t in d_tokens:
        if t == c_last:
            continue
        if len(t) == 1:
            continue  # initials (middle names often absent from display name)
        if any(_names_equivalent(t, c) for c in c_tokens):
            matched_given = True
            continue
        if any(len(c) == 1 and t.startswith(c) for c in c_tokens):
            matched_given = True
            continue  # donor spells out an initial in the candidate name
        return False

    # Last name alone ("Scott") is not enough — require a given-name signal.
    return matched_given
