"""Generic confirmed-general-election-candidate sync: fetch, match, and
flag existing FEC-derived Candidate rows for ANY state with a registered
source (state_candidate_sources.json) — no API key beyond what each
state's own strategy needs, no guessing.

Deliberately NOT one parser that guesses an arbitrary state's candidate-
data shape, and equally deliberately NOT one fetcher per state — 50 of
those is the maintenance trap this design exists to avoid. States don't
each build their own system; they buy one from a handful of vendors. So a
strategy here is a VENDOR (Civix, Clarity, or a bulk tabular export
including the Enhanced Voting portal), and every state it serves is an
entry in state_candidate_sources.json — which URLs, which columns, which
nomination rule — never new code.

The invariant that keeps it that way: no state's name or state-specific
literal appears in any fetch module. A state that needs something nobody
has needed yet (Virginia's district-type column, Utah's never-flipped
certification flag) gets that capability added to its adapter FOR EVERY
STATE, plus a config key, never a branch on its name. Shared parsing
lives in state_candidates_common.py; the config contract is indexed in
state_candidate_sources.json's own _contract field.

The matching/flagging code around all of it (this module) is shared, same
STRATEGIES-dispatch shape as ballot_measures_pdf.py.

Matching a state's reported (office, district, party, last_name) against
Civitas's own FEC-derived Candidate rows compares surname to surname
directly — NOT state_candidates_common.last_name_matches, which matches a surname
against the TRAILING tokens of a "First Last"-formatted name (that's the
right shape for _incumbent_link's target, Representative/Senator.name, but
Candidate.name is FEC's own "LAST, FIRST MIDDLE" format, so the surname is
the LEADING part before the comma — the same extraction _incumbent_link
itself does to `cand.name` before calling last_name_matches on someone
else's name). Exact string equality on the extracted, lowercased surname
(not substring) for the same "lee" != "leeman" reason. A record that
matches zero or more than one candidate (after a party-based tiebreak
attempt) is logged and skipped, never guessed — the existing FEC candidate
list is left exactly as it was for that race, which is always at least as
accurate as before this sync ran, never worse.
"""

import hashlib
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timedelta
import unicodedata

import httpx
from sqlalchemy.orm import Session

from app.models import (
    BALLOT_ONLY_ID_PREFIX,
    Candidate,
    JudicialNominee,
    Race,
    StateLegNominee,
    StatewideNominee,
)
from app.atomic_write import NotSaved
from app.time_utils import utcnow
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch import state_candidate_sources
from app.pipeline.fetch.state_candidate_sources import (
    _load as _sources_file,
    configured_states,
    discovered_states,
    filings_for_state,
    forget_results_source,
    save_discovered,
    source_for_state,
    states_with_filings,
)
from app.pipeline.fetch import state_election_dates as election_dates
from app.pipeline.fetch.state_candidate_filings import fetch_ballot_candidates
from app.pipeline.fetch.state_source_crawler import (
    ELECTION_DOMAINS,
    discover_filings,
    discover_source,
)
from app.pipeline.candidate_dedup import normalized_surname
from app.pipeline.fetch.state_candidates_common import (
    BALLOT_BASIS_TIER,
    LABELLED_PARTIES,
    NOMINATION_RULE_KEYS,
    PARTY_CODE_MAP,
    fec_party,
    ballot_basis_key,
    is_not_a_person,
    clean_display_name,
    JUDICIAL_COURT_LABELS,
    JUDICIAL_MARKER_TIER,
    JUDICIAL_MARKER_TTL_HOURS,
    judicial_marker_key,
    STATE_LEG_CHAMBER_LABELS,
    STATEWIDE_MARKER_TIER,
    STATEWIDE_MARKER_TTL_HOURS,
    join_governor_tickets,
    STATEWIDE_OFFICE_LABELS,
    statewide_marker_key,
)
from app.pipeline.fetch.state_candidates_al import fetch_confirmed_candidates as _fetch_al
from app.pipeline.fetch.state_candidates_canvass_summary_pdf import fetch_confirmed_candidates as _fetch_canvass_summary_pdf
from app.pipeline.fetch.state_candidates_canvass_xml import fetch_confirmed_candidates as _fetch_canvass_xml
from app.pipeline.fetch.state_candidates_certified_pdf import fetch_confirmed_candidates as _fetch_certified_pdf
from app.pipeline.fetch.state_candidates_certified_table import fetch_confirmed_candidates as _fetch_certified_table
from app.pipeline.fetch.state_candidates_civic import fetch_confirmed_candidates as _fetch_civic
from app.pipeline.fetch.state_candidates_ct import fetch_confirmed_candidates as _fetch_ct
from app.pipeline.fetch.state_candidates_grouped_list_pdf import fetch_confirmed_candidates as _fetch_grouped_list_pdf
from app.pipeline.fetch.state_candidates_clarity import fetch_confirmed_candidates as _fetch_clarity
from app.pipeline.fetch.state_candidates_dos_canlist import fetch_confirmed_candidates as _fetch_dos_canlist
from app.pipeline.fetch.state_candidates_enhanced_voting import (
    fetch_confirmed_candidates as _fetch_enhanced_voting,
)
from app.pipeline.fetch.state_candidates_in import fetch_confirmed_candidates as _fetch_in
from app.pipeline.fetch.state_candidates_ks import fetch_confirmed_candidates as _fetch_ks
from app.pipeline.fetch.state_candidates_ky import fetch_confirmed_candidates as _fetch_ky
from app.pipeline.fetch.state_candidates_ma import fetch_confirmed_candidates as _fetch_ma
from app.pipeline.fetch.state_candidates_me import fetch_confirmed_candidates as _fetch_me
from app.pipeline.fetch.state_candidates_ms import fetch_confirmed_candidates as _fetch_ms
from app.pipeline.fetch.state_candidates_nh import fetch_confirmed_candidates as _fetch_nh
from app.pipeline.fetch.state_candidates_nj import fetch_confirmed_candidates as _fetch_nj
from app.pipeline.fetch.state_candidates_oh import fetch_confirmed_candidates as _fetch_oh
from app.pipeline.fetch.state_candidates_or import fetch_confirmed_candidates as _fetch_or
from app.pipeline.fetch.state_candidates_sd_vip import fetch_confirmed_candidates as _fetch_sd_vip
from app.pipeline.fetch.state_candidates_tabular import fetch_confirmed_candidates as _fetch_tabular
from app.pipeline.fetch.state_candidates_pa import fetch_confirmed_candidates as _fetch_pa
from app.pipeline.fetch.state_candidates_tally_enr import fetch_confirmed_candidates as _fetch_tally_enr
from app.pipeline.fetch.state_candidates_tn import fetch_confirmed_candidates as _fetch_tn
from app.pipeline.fetch.state_candidates_totalvote import fetch_confirmed_candidates as _fetch_totalvote
from app.pipeline.fetch.state_candidates_tx import fetch_confirmed_candidates as _fetch_tx
from app.pipeline.fetch.state_candidates_voterportal import fetch_confirmed_candidates as _fetch_voterportal
from app.pipeline.fetch.state_candidates_vrems import fetch_confirmed_candidates as _fetch_vrems
from app.pipeline.fetch.state_candidates_vt import fetch_confirmed_candidates as _fetch_vt
from app.pipeline.fetch.state_candidates_wy import fetch_confirmed_candidates as _fetch_wy

logger = logging.getLogger(__name__)

# Every strategy takes the SAME (client, cycle, state, source) arguments so
# one adapter can serve many states — a state whose vendor is already here
# is a state_candidate_sources.json entry, never new code. Only a genuinely
# different vendor earns a new module.
STRATEGIES = {
    "al_special_primary": _fetch_al,
    "tally_enr": _fetch_tally_enr,
    "ks_official_totals": _fetch_ks,
    "ct_enr": _fetch_ct,
    "tx_civix": _fetch_tx,
    "pa_returns": _fetch_pa,
    "clarity": _fetch_clarity,
    "tabular": _fetch_tabular,
    "canvass_xml": _fetch_canvass_xml,
    "nj_certification": _fetch_nj,
    "ky_certification": _fetch_ky,
    "ms_recap": _fetch_ms,
    "totalvote_enr": _fetch_totalvote,
    "in_enr": _fetch_in,
    "tn_precinct": _fetch_tn,
    "wy_canvass_xlsx": _fetch_wy,
    "or_abstract_pdf": _fetch_or,
    "vt_enr": _fetch_vt,
    "ma_pd43": _fetch_ma,
    "me_results": _fetch_me,
    "sd_vip": _fetch_sd_vip,
    "voterportal": _fetch_voterportal,
    "vrems": _fetch_vrems,
    "certified_pdf": _fetch_certified_pdf,
    "certified_table": _fetch_certified_table,
    "grouped_list_pdf": _fetch_grouped_list_pdf,
    "canvass_summary_pdf": _fetch_canvass_summary_pdf,
    "dos_canlist": _fetch_dos_canlist,
    "google_civic": _fetch_civic,
    "nh_results": _fetch_nh,
    "enhanced_voting": _fetch_enhanced_voting,
    "oh_canvass_xlsx": _fetch_oh,
}

# The strategies whose rows are a state's list of who is on the November
# ballot -- every qualified candidate, independents included -- rather
# than primary results, which name only each party's contested winners.
# Decides whether the page may call a state-office section the whole
# ballot (the markers' ballotList). A property of the DOCUMENT read, never
# of general_ballot_complete: that flag describes a state's federal
# ballot, and North Carolina sets it for its federal FILING list while its
# state offices come from primary results. A strategy that can read either
# kind says which per run (SourceRecords.ballot_list -- Vermont's reads
# the general report once final, primary winners before). Not listed:
# nj_certification (party nominees only) and every results reader.
BALLOT_LIST_STRATEGIES = frozenset({
    "certified_table",    # the certified general lists (AK CO DE HI IA MD ME ND NE NM OK TN WY)
    "grouped_list_pdf",   # Illinois's website candidate list
    "dos_canlist",        # Florida's general candidate list
    "certified_pdf",      # Missouri's certification of candidates
    "vrems",              # South Carolina's candidate tracking, general election
    "sd_vip",             # South Dakota's general candidate list
    "tx_civix",           # Texas's general-election candidate portal
    "voterportal",        # Louisiana's staged November ballot
})



def is_configured(state: str) -> bool:
    """Whether `state` has both a registered source AND a strategy
    function for it — an entry with a typo'd/unregistered strategy key is
    a config bug, not a signal to guess."""
    source = source_for_state(state)
    return source is not None and source.get("strategy") in STRATEGIES


def _race_id_for(db: Session, cycle: int, state: str, office: str, district: int | None) -> str:
    """The race a state's record belongs to, by election_pipeline._race_id's
    convention. A Senate record goes to the state's one Senate race this
    cycle, regular or special: Florida and Ohio elect a senator in 2026
    only to fill a vacancy, so their race is "2026-SEN-FL-SPECIAL", and a
    record keyed to the regular id matched nothing — both pages showed
    every FEC filer instead of the certified ballot. With both a regular
    and a special race in one state (Georgia, 2020) a record carries
    nothing to choose between them, so it stays with the regular race."""
    if office == "S":
        senate = [rid for (rid,) in db.query(Race.id).filter(
            Race.cycle_year == cycle, Race.state == state, Race.office == "S",
        )]
        if len(senate) == 1:
            return senate[0]
        return f"{cycle}-SEN-{state}"
    return f"{cycle}-HOUSE-{state}-{district if district is not None else 0}"


def _fold(text: str) -> str:
    """Lowercased with diacritics removed. FEC files names in plain ASCII
    capitals and a state prints them as the candidate spells them, so
    without this "Sánchez" never equals "SANCHEZ" — which left Linda
    Sánchez (CA-41) unmatched and her race showing all eleven filers."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _candidate_surname(name: str) -> str:
    """FEC's Candidate.name is "LAST, FIRST MIDDLE ..." — the surname is
    everything before the comma, minus a generational suffix. FEC puts
    the suffix on either half ("CLEAVER II, EMANUEL"), and a state never
    does, so without stripping it a sitting member of Congress goes
    unconfirmed: "cleaver ii" is not "cleaver", and the last-token
    fallback below then compares "ii". Applied to a state's own surname
    too, which can carry one ("HAYNES III" from Texas)."""
    return _fold(normalized_surname(name))


# Words FEC appends to given names that are not names: honorifics, ranks,
# suffixes. Skipped when reading the given-name half.
_NOT_A_NAME = frozenset({
    "mr", "mrs", "ms", "miss", "dr", "hon", "the", "honorable", "captain",
    "jr", "sr", "ii", "iii", "iv", "v",
})


def _without_trailing_suffix(name: str) -> str:
    """"John A. Olszewski, Jr." without its ", Jr.": a comma before a
    generational suffix is not the "Last, First" comma, and read as one it
    left the name with no given name at all. Only after more than one word:
    "LEE, JR." is a surname and its suffix, and stripped it would read its
    surname as a given name."""
    name = name.strip()
    suffix = _SUFFIX_AFTER_COMMA_RE.search(name)
    if suffix and len(name[: suffix.start()].split()) > 1:
        return name[: suffix.start()]
    return name


def _given_names(name: str) -> list[str]:
    """The given-name tokens, folded, with honorifics and initials dropped.

    Both sides are normalised the same way: FEC files "SULLIVAN, DANIEL
    J" (surname, then given names) and a state prints "Sullivan, Daniel
    J. Jr." or "Daniel J. Sullivan Jr.". Taking the tokens AFTER any comma
    handles the first two; for the third the leading token already is the
    given name."""
    name = _without_trailing_suffix(name)
    tail = name.split(",", 1)[1] if "," in name else name
    words = ("".join(ch for ch in token if ch.isalpha()) for token in _fold(tail).replace(".", " ").split())
    return [w for w in words if len(w) > 1 and w not in _NOT_A_NAME]


def _first_name_key(name: str) -> str:
    """The leading given name, for telling apart two people who share a
    surname. Punctuation and single initials are dropped so "Dan S." and
    "DAN" agree."""
    given = _given_names(name)
    return given[0] if given else ""


def _one_transposition_or_typo(a: str, b: str) -> bool:
    """True when a and b are one slip apart: one edit (a swap of two
    adjacent letters counts as one), or the same letters with one moved
    and the first three unchanged — "DAUGHTERY" for "Daugherty" is the
    latter, two edits by any distance but plainly one mistake."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if sorted(a) == sorted(b) and a[:3] == b[:3]:
        return True
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[len(b)] == 1


def _surname_fallbacks(
    candidates: list[Candidate], target: str, display_name: str | None,
    keep=lambda found: found,
) -> list[Candidate]:
    """Candidates a state's surname reaches only indirectly. Each rule runs
    only when the one before found nobody — `keep` decides what "found"
    means for a rule's result (the caller's plausibility test) — and each
    still has to come out UNIQUE in the race (the caller refuses anything
    ambiguous)."""
    # A MULTI-WORD surname survives on the FEC side ("WASSERMAN SCHULTZ,
    # DEBBIE") but not on the state's, because a state publishes a display
    # name and the trailing token is all that can be taken from "Debbie
    # Wasserman Schultz" without guessing where the surname begins.
    found = keep([c for c in candidates if _candidate_surname(c.name).split()[-1:] == [target]])
    if found:
        return found
    # The mirror: the state prints the whole surname and FEC files only its
    # last word — Maryland's "McClain Delaney" is FEC's "DELANEY, APRIL
    # MCCLAIN" (MD-6, 2026).
    if len(target.split()) > 1:
        found = keep([c for c in candidates if _candidate_surname(c.name) == target.split()[-1]])
        if found:
            return found
    # A married or former surname filed as a given name: the ballot says
    # "Ashley Hinson" and FEC has "ARENHOLZ, ASHLEY HINSON" (IA Senate,
    # 2026 — the Republican nominee, unmatched without this).
    found = keep([c for c in candidates if _given_names(c.name)[-1:] == [target]])
    if found:
        return found
    # A one-letter slip on either side, only with the given name agreeing
    # too: the ballot's "Brandon Coulter Daugherty" is FEC's "DAUGHTERY,
    # BRANDON" (MO-2, 2026). Short surnames are excluded — one edit away
    # from "Lee" is too many real names.
    wanted = _first_name_key(display_name or "")
    if wanted and len(target) >= 5:
        return keep([
            c for c in candidates
            if _first_name_key(c.name) == wanted
            and _one_transposition_or_typo(_candidate_surname(c.name), target)
        ])
    return []


_KNOWN_PARTIES = frozenset(PARTY_CODE_MAP.values())


def _record_given(display_name: str | None, last_name: str) -> tuple[list[str], str]:
    """(every full given name, leading initial if it leads with one) a
    record's display name states — its surname's words, honorifics and
    suffixes set aside, so "J. Smith" states only the initial "j", "Mary
    Anne Smith" both "mary" and "anne", and "Smith", "Smith Jr." and "Dr.
    Smith" state nothing."""
    display = _without_trailing_suffix(display_name or "")
    if "," in display:
        words = _fold(display.split(",", 1)[1]).replace(".", " ").split()
    else:
        surname = set(_fold(last_name or "").replace(".", " ").split())
        words = [w for w in _fold(display).replace(".", " ").split() if w not in surname]
    words = ["".join(ch for ch in w if ch.isalpha()) for w in words]
    words = [w for w in words if w and w not in _NOT_A_NAME]
    full = [w for w in words if len(w) > 1]
    # The leading initial, only when the name really leads with one
    # ("J. Robert Smith"): "John Smith" states "john", not just "j".
    return full, (words[0] if words and len(words[0]) == 1 else "")


def _contradicts(
    cand: Candidate, party_code: str, display_name: str | None, last_name: str = "",
) -> bool:
    """Whether a same-surname candidate is plainly someone else: a
    different party AND a given name that fits none of theirs. Either alone
    is not enough — a party can be coded differently between sources, and
    a nickname ("Jim" for JAMES) fits no FEC token — but both together is a
    different person (Mary Smith, Libertarian, is not John Smith, DEM)."""
    expected = PARTY_CODE_MAP.get(party_code)
    theirs = fec_party(cand.party)
    # Only a party both sides name (fec_party): a code the map doesn't know
    # says nothing either way.
    if not expected or theirs not in _KNOWN_PARTIES or theirs == expected:
        return False
    return given_name_contradicts(cand, display_name, last_name)


def given_name_contradicts(cand: Candidate, display_name: str | None, last_name: str = "") -> bool:
    """_contradicts' given-name half on its own: the record states a given
    name that fits none of the candidate's. Asked alone where the record
    states no party to compare (a live-results feed's independent)."""
    wanted, initial = _record_given(display_name, last_name)
    theirs_initial = _given_initial(cand.name or "")
    if initial and initial == theirs_initial:
        # Their first initial is the record's own ("J. Robert Smith" and
        # JAMES, or J): that fits, whatever the middle name.
        return False
    if wanted:
        if theirs_initial and _leads_with_initial(cand.name or "") and theirs_initial == wanted[0][0]:
            # The mirror: FEC's own leading initial ("SMITH, J ROBERT") is
            # the record's first given name's ("John Smith").
            return False
        tokens = _given_names(cand.name or "")
        if tokens:
            # The record's first given name against any of theirs (a short
            # form either way), or FEC's first given name EXACTLY among the
            # record's later ones: "Maria Elvira Salazar" is FEC's
            # "SALAZAR, ELVIRA" and "Mary Anne Smith" is "SMITH, ANNE". Not
            # any name against any: a shared middle name ("John Lee" and
            # "MARY LEE") or a later prefix ("Mary Jo" and "JOHN") is not
            # the same person.
            first = wanted[0]
            return not (
                any(t == first or t.startswith(first) or first.startswith(t) for t in tokens)
                or tokens[0] in wanted[1:]
            )
        return bool(theirs_initial) and theirs_initial != wanted[0][0]
    if initial:  # an initial alone ("J. Smith") against theirs
        return bool(theirs_initial) and theirs_initial != initial
    return False  # no given name stated: nothing to contradict with


def _match_candidate(
    candidates: list[Candidate], last_name: str, party_code: str,
    display_name: str | None = None,
) -> Candidate | None:
    """The one candidate `last_name` (with party and display name to break
    ties) names in `candidates`, or None. Whatever path chose it, a match
    that is plainly someone else — another party AND another given name
    (_contradicts) — is refused."""
    match = _match_by_surname(candidates, last_name, party_code, display_name)
    return None if match is not None and _contradicts(match, party_code, display_name, last_name) else match


def _match_by_surname(
    candidates: list[Candidate], last_name: str, party_code: str,
    display_name: str | None = None,
) -> Candidate | None:
    target = _candidate_surname(last_name)

    def plausible(tier: list[Candidate]) -> list[Candidate]:
        # A tier whose every candidate is plainly someone else (_contradicts)
        # says nothing, and the next rule is tried: John Hinson, Libertarian,
        # on the exact surname must not hide Ashley Hinson filed under her
        # married name. A tier with anyone plausible is judged whole, as
        # before — dropping only the implausible could turn an ambiguous
        # pair into a false unique match.
        return [] if tier and all(_contradicts(c, party_code, display_name, last_name) for c in tier) else tier

    exact = [c for c in candidates if _candidate_surname(c.name) == target]
    matches = plausible(exact)
    if not matches:
        # Each fallback rule is judged the same way, over everyone but the
        # exact-surname people just refused (the rule that reads a
        # surname's last word would otherwise find John Hinson again).
        refused = {id(c) for c in exact}
        matches = _surname_fallbacks(
            [c for c in candidates if id(c) not in refused], target, display_name, keep=plausible,
        )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None

    expected_party = PARTY_CODE_MAP.get(party_code)
    pool = [c for c in matches if fec_party(c.party) == expected_party] or matches
    if len(pool) == 1:
        return pool[0]
    # Two candidates sharing a surname AND a party. A given name separates
    # them where party cannot: Alaska's 2026 top-four advances two
    # Sullivans, TX-34 has Eric and Mayra Flores, AZ-7 Raúl and Adelita
    # Grijalva. It can only narrow; a given name nobody matches (a
    # nickname, say) leaves the pool as it was.
    wanted = _first_name_key(display_name or "")
    if wanted:
        pool = [c for c in pool if _first_name_key(c.name) == wanted] or pool
        if len(pool) == 1:
            return pool[0]
    # One person under two FEC ids: FEC assigns a new candidate_id on a
    # refiling, so a race can hold "BERRY, PAUL" twice (MO-1) or "ELLESON,
    # JOHN D." beside "ELLESON, JOHN" (IL-9). candidate_dedup will not
    # merge them unless their financials are identical, which is right for
    # DISPLAY — but a ballot names one person, and refusing both left the
    # nominee unconfirmed. Same surname, given name and party inside one
    # race is the same person; confirm the record that raised money, and
    # the other drops off the page with every other unconfirmed filer.
    if len({(_first_name_key(c.name), fec_party(c.party)) for c in pool}) == 1 and _first_name_key(pool[0].name):
        return max(pool, key=lambda c: (bool(c.has_raised_funds), c.contributions or 0, c.id))
    return None


def _fec_candidates(race: Race) -> list[Candidate]:
    """The race's FEC rows. A ballot-only row from an earlier run is the
    state's own record echoed back, never something to match against: if
    it were, a candidate who files with the FEC later could lose the match
    to their own placeholder."""
    return [c for c in race.candidates if c.fec_filed]




# A ballot-only row claims a PERSON printed on the November ballot. A
# name the source itself marks as a write-in or withdrawn is not that
# claim ("Redkey, David (Write-In)", "Write-In - David Fey"), even though a
# live count keeps their votes -- is_not_a_person only refuses aggregate
# rows, so this is the narrower question asked on top of it.
_NOT_ON_THE_BALLOT_RE = re.compile(
    r"\bwrite[\s-]*ins?\b|\bwithdrawn\b|\bscattering\b|\buncommitted\b|\b(?:over|under)[\s/-]*votes?\b"
    # Summary rows the old substring filter caught and is_not_a_person's
    # whole-label match doesn't: ES&S's "Times Blank Voted", "Blank/Void",
    # "Over Votes (Not Counted)". A person surnamed Blank is still a
    # person: "blank" counts only beside a ballot word.
    r"|\btimes\s+blank\b|\bblank\s*(?:/|votes?\b|ballots?\b)|\bvoid(?:ed)?\s+(?:ballots?|votes?)\b",
    re.IGNORECASE,
)


def _fec_style_name(display_name: str, last_name: str) -> str:
    """The printed name in FEC's "LAST, GIVEN" shape, which is how every
    other candidate on the page is named: 'Walter "Rocky" Beach' ->
    'BEACH, WALTER "ROCKY"'. A name already printed last-first
    ("Shah, Amish") is kept in that order."""
    head, _, rest = display_name.partition(",")
    if rest and _fold(head).strip() == _fold(last_name).strip():
        return display_name.upper()
    tokens = display_name.split()
    want = [_fold(t) for t in last_name.split()]
    n = len(want)
    for i in range(len(tokens) - n, -1, -1):
        if [_fold(t).strip(".,") for t in tokens[i:i + n]] == want:
            given = " ".join(tokens[:i] + tokens[i + n:]).strip(" ,")
            surname_text = " ".join(tokens[i:i + n]).strip(" ,")
            return f"{surname_text}, {given}".upper() if given else surname_text.upper()
    return display_name.upper()


def _ballot_party(record: dict) -> str:
    """The party a ballot-only row for `record` is stored under. A party
    the matcher has no code for (South Carolina's Workers, say) keeps the
    state's own label rather than reading as "unknown"."""
    return (
        PARTY_CODE_MAP.get(record.get("party") or "")
        or (record.get("party_label") or "").strip().upper()
        or "UNK"
    )


def _keep_ballot_only(
    db: Session, race: Race, record: dict, claimed: set[str] = frozenset(),
) -> str | None:
    """Record a state-listed candidate who has no FEC row, and return the
    row's id — or None when the record is not safe to show as a person.

    A surname alone is not enough to show anyone (Oregon's results PDF
    prints only surnames, and "Smith (R)" is not a ballot entry), and a
    results file's non-candidate rows must never become one."""
    display = (record.get("display_name") or "").strip()
    words = [w for w in re.split(r"[\s,]+", display) if any(ch.isalpha() for ch in w)]
    if len(words) < 2 or is_not_a_person(display) or _NOT_ON_THE_BALLOT_RE.search(display):
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(display)).strip("-")
    cid = f"{BALLOT_ONLY_ID_PREFIX}{race.id}:{slug}"
    cand = db.query(Candidate).filter(Candidate.id == cid).first()
    if cand is None:
        # The same person another source spelled differently ("Jane Q. Doe"
        # and "Jane Doe") keeps one row; minting a second showed them twice
        # wherever nothing prunes (_may_prune).
        cand = _placeholder_for(db, race, record, claimed=claimed)
        if cand is not None:
            cid = cand.id
    if cand is None:
        cand = Candidate(id=cid, race_id=race.id)
        db.add(cand)
    cand.name = _fec_style_name(display, record["last_name"])
    cand.party = _ballot_party(record)
    cand.has_raised_funds = False
    cand.incumbent_challenge = None
    cand.candidate_status = None
    cand.confirmed_general = True
    db.commit()
    return cid


def _has_general_filings(state: str) -> bool:
    """Whether this state's filing list speaks for its November ballot
    (North Carolina's does) — its general rows, not the results pass,
    deciding who is listed. For a hand-verified state only a list in its
    own entry does: one the crawler found is unverified, and must not take
    that authority from a certified ballot (TX, LA, ...) — its general rows
    aren't applied at all (_filings_speak_for_november)."""
    return bool(filings_for_state(state)) and _filings_speak_for_november(state)


def _filings_speak_for_november(state: str) -> bool:
    """Whether `state`'s filing list's general rows are applied. A
    hand-verified state's are only if the list is its own; a crawler-found
    list there contributes its primary rows alone. (Before, such a list was
    not read at all.)"""
    hand = (_sources_file().get("states") or {}).get(state.upper())
    return not hand or bool(hand.get("filings"))


def _apply_ballot(
    db: Session, cycle: int, state: str, records: list[dict],
    *, keep_unlisted: bool, authoritative: bool, scope: set[str] | None = None,
    prune: bool = True,
) -> dict:
    """Confirm a state's federal records against its races.

    `keep_unlisted`: a record that matches no FEC candidate is still a
    person on the ballot — show them (ballot-only row) rather than drop
    them. `authoritative`: these records ARE the certified November
    ballot, so anyone confirmed in a race they cover but not on them is
    unconfirmed (_unconfirm_off_ballot). `prune`: whether ballot-only rows
    these records don't list are dropped — not when a weaker source is
    answering for a state whose certified ballot listed them
    (_may_prune)."""
    confirmed = unmatched = 0
    ballot_only: set[str] = set()
    listed: dict[str, set[str]] = {}
    for record in records:
        race_id = _race_id_for(db, cycle, state, record["office"], record["district"])
        race = db.query(Race).filter(Race.id == race_id).first()
        if race is None:
            unmatched += 1
            continue
        listed.setdefault(race.id, set())
        match = _match_candidate(
            _fec_candidates(race), record["last_name"], record["party"], record.get("display_name"),
        )
        if match is None:
            kept = _keep_ballot_only(db, race, record, ballot_only) if keep_unlisted else None
            if kept:
                ballot_only.add(kept)
                continue
            unmatched += 1
            logger.info(
                "No FEC match for confirmed %s candidate %s (%s) in %s",
                state, record["last_name"], record["party"], race_id,
            )
            continue
        if not match.confirmed_general:
            match.confirmed_general = True
            db.commit()
        _note_ballot_name(db, match, record)
        _drop_replaced_placeholder(db, race, record, match, ballot_only)
        listed[race.id].add(match.id)
        confirmed += 1
    if keep_unlisted and prune:
        _prune_ballot_only(db, cycle, state, ballot_only, scope)
    withdrawn = _unconfirm_off_ballot(db, listed) if authoritative else 0
    return {
        "confirmed": confirmed, "unmatched": unmatched,
        "ballotOnly": len(ballot_only), "unconfirmed": withdrawn,
    }


def _record_ballot_basis(
    db: Session, cycle: int, state: str, source: dict, races: set[str] | None = None,
) -> None:
    """Say which source answered for this state tonight — see
    BALLOT_BASIS_TIER. Read by the API to decide "confirmed" (the whole
    ballot) versus "nominees" (primary results), per race: `complete`
    means every federal race here, `races` names the ones a certified
    list covered when it did not cover them all."""
    api_cache_set(
        db, BALLOT_BASIS_TIER, ballot_basis_key(state, cycle),
        {
            "complete": bool(source.get("general_ballot_complete")),
            "races": sorted(races or ()),
            "sourceName": str(source.get("source_name") or ""),
            "checkedAt": utcnow().isoformat() + "Z",
        },
        normal_ttl_hours=STATEWIDE_MARKER_TTL_HOURS,
    )
    db.commit()


def _unconfirm_off_ballot(db: Session, listed: dict[str, set[str]]) -> int:
    """For a state whose source IS its certified November ballot: anyone
    confirmed in a race that list covers, but not on it, is not running.

    `confirmed_general` is otherwise never cleared, which is right for
    primary results (a nominee does not stop being one because a later
    fetch hiccupped) and wrong once the state has certified its ballot.
    Maine confirmed Graham Platner from the June primary he won; he
    withdrew in July and the party nominated Troy Jackson. Adding Jackson
    alone would have shown both. Scoped to the races the list actually
    covers, so a race the list is missing (a parse slip) keeps what it
    had rather than losing everyone."""
    changed = 0
    for race_id, keep in listed.items():
        for cand in db.query(Candidate).filter(
            Candidate.race_id == race_id, Candidate.confirmed_general.is_(True),
        ):
            if cand.fec_filed and cand.id not in keep:
                cand.confirmed_general = False
                changed += 1
                logger.info("%s is no longer on the certified ballot for %s", cand.name, race_id)
    if changed:
        db.commit()
    return changed


def _given_initial(name: str) -> str:
    """The first letter of an FEC-style name's given half, initials
    included ("SMITH, T.J." gives "t"), honorifics skipped; "" for a name
    with no given half."""
    if "," not in name:
        return ""
    for token in _fold(name.split(",", 1)[1]).replace(".", " ").split():
        word = "".join(ch for ch in token if ch.isalpha())
        if word and word not in _NOT_A_NAME:
            return word[0]
    return ""


def _leads_with_initial(name: str) -> bool:
    """Whether an FEC-style name's given half leads with a bare initial
    ("SMITH, J ROBERT", "SMITH, T.J."), honorifics skipped."""
    if "," not in name:
        return False
    for token in _fold(name.split(",", 1)[1]).replace(".", " ").split():
        word = "".join(ch for ch in token if ch.isalpha())
        if word and word not in _NOT_A_NAME:
            return len(word) == 1
    return False


def _same_given_name(a: str, b: str) -> bool:
    """Whether two FEC-style names' given names can be the same person's:
    equal, one a short form of the other ("DAN" / "DANIEL"), or — where
    either side prints only initials ("T.J.") — the same first letter.
    Mary and John never are; a name with no given half matches nothing."""
    if "," not in a or "," not in b:
        return False
    ka, kb = _first_name_key(a), _first_name_key(b)
    if ka and kb:
        return ka == kb or ka.startswith(kb) or kb.startswith(ka)
    ia, ib = _given_initial(a), _given_initial(b)
    return bool(ia) and ia == ib


def _surnames_agree(a: str, b: str) -> bool:
    """Whether two folded surnames can be one person's: equal, or one the
    last word of the other — the multi-word equivalence the matcher's first
    two fallbacks accept ("LEGER FERNANDEZ, TERESA" printed by one source,
    "Teresa Leger Fernandez", surname "Fernandez", by another)."""
    return bool(a) and (a == b or a.split()[-1:] == [b] or b.split()[-1:] == [a])


def _placeholder_for(
    db: Session, race: Race, record: dict, *,
    reference: str | None = None, claimed: set[str] = frozenset(),
) -> Candidate | None:
    """This race's ballot-only row for the same PERSON as `record`: same
    party (_ballot_party), same surname (_surnames_agree), and a given name
    compatible (_same_given_name) with `reference` — the FEC row the record matched, when dropping — or
    with the record's own. Only a UNIQUE such row, the record's exact given
    name breaking a tie, and never one this pass already `claimed` for
    someone (Chris and Christine Smith both on one list). Nothing looser: a
    surname and party alone would take Mary Smith's row for John's."""
    display = (record.get("display_name") or "").strip()
    if not display:
        return None
    # The party the row was stored under (_ballot_party), so an unaffiliated
    # or third-party candidate's row is found as well as a Democrat's.
    party = _ballot_party(record)
    wanted = _fec_style_name(display, record["last_name"])
    surname = _candidate_surname(wanted)
    rows = [
        c for c in db.query(Candidate)
        .filter(Candidate.race_id == race.id, Candidate.id.startswith(BALLOT_ONLY_ID_PREFIX))
        .all()
        if c.id not in claimed and c.party == party
        and _surnames_agree(_candidate_surname(c.name or ""), surname)
        and (
            _same_given_name(c.name or "", wanted)
            # The FEC row's name too — another source may have spelled the
            # placeholder "Daniel" for tonight's "Dan" — but only a full
            # given name: "SMITH, J" would fit Jane's row as well as John's.
            or (reference and _first_name_key(reference) and _same_given_name(c.name or "", reference))
        )
    ]
    if len(rows) > 1:
        key = _first_name_key(wanted)
        rows = [c for c in rows if key and _first_name_key(c.name or "") == key]
    return rows[0] if len(rows) == 1 else None


def _drop_replaced_placeholder(
    db: Session, race: Race, record: dict, match: Candidate, keep: set[str] = frozenset(),
) -> None:
    """A record that now matches an FEC candidate replaces this race's
    ballot-only row for the same person (they filed since). Part of
    _prune_ballot_only's job, but not a judgement about who is on the
    ballot, so it runs whatever source is answering — else the person is
    shown twice while a weaker source answers. A row this pass itself kept
    (`keep`) is someone else's, by construction."""
    same = _placeholder_for(db, race, record, reference=match.name, claimed=keep)
    if same is not None:
        db.delete(same)
        db.commit()
        logger.info("%s: dropped ballot-only %s, now an FEC candidate", race.id, same.id)


def _may_prune(configured: dict, answering: dict) -> bool:
    """Whether tonight's answering source may drop ballot-only rows. Only a
    source as authoritative as the state's configured one: a state with a
    certified ballot (general_ballot_complete, or a general_list) had its
    third-party and independent candidates listed by it, and a primary-
    results file answering while it is down — a fallback, the crawler's
    spare, the results beside a general_list — cannot list them, so its
    silence says nothing about them."""
    certified = bool(configured.get("general_ballot_complete") or configured.get("general_list"))
    return not certified or bool(answering.get("general_ballot_complete"))


def _prune_ballot_only(
    db: Session, cycle: int, state: str, kept: set[str], scope: set[str] | None = None,
) -> None:
    """Drop ballot-only rows this state's source no longer lists: the
    candidate withdrew, or filed with the FEC and now matches a real row.
    Only reached after a successful fetch — a source that failed says
    nothing about who is on the ballot. `scope` limits it to the races
    that source speaks for, when two sources split a state's races."""
    query = (
        db.query(Candidate)
        .join(Race, Candidate.race_id == Race.id)
        .filter(
            Race.state == state,
            Race.cycle_year == cycle,
            Candidate.id.startswith(BALLOT_ONLY_ID_PREFIX),
        )
    )
    if scope is not None:
        query = query.filter(Race.id.in_(scope))
    stale = query.all()
    removed = [c for c in stale if c.id not in kept]
    for cand in removed:
        db.delete(cand)
    if removed:
        db.commit()
        logger.info("%s: removed %d ballot-only candidate(s) no longer listed", state, len(removed))


# Each state's crawl record ("{cycle}-{ST}" in api_cache): when its last
# crawl completed, and since when its discovered results source has been
# failing. A state is crawled once it is _CRAWL_EVERY past its last
# completed crawl, so one that errored is retried the next night rather
# than waiting a week — and one that errors every time can't hold up the
# states after it, which a single weekly sweep in a fixed order did.
CRAWL_TIER = "source-crawl"
_CRAWL_EVERY = timedelta(days=7)
# The election run starts at a different time each night (it is last in the
# nightly chain), so a week to the hour would often slip a state to the
# eighth night.
_CRAWL_SLACK = timedelta(hours=12)
# A discovered source that fails on consecutive crawls this long apart is
# gone; one failed fetch is as likely an outage as a move, and forgetting
# on it left the state dark until a later crawl could re-prove it.
_FORGET_AFTER = timedelta(days=14)
_CRAWL_RECORD_TTL_HOURS = 24 * 400
# How an adopted results source's description begins — what tells the
# crawler's own find from anything else in the discovered file.
_FOUND_AUTOMATICALLY = "Found automatically"


def _crawl_record(db: Session, cycle: int, state: str) -> dict:
    return api_cache_get(
        db, CRAWL_TIER, f"{cycle}-{state}", max_age_hours=_CRAWL_RECORD_TTL_HOURS,
    ) or {}


def _crawl_due(record: dict, now: datetime) -> bool:
    last = record.get("lastOk")
    return not last or now - datetime.fromisoformat(last) >= _CRAWL_EVERY - _CRAWL_SLACK


async def crawl_for_new_sources(
    db: Session, client: httpx.AsyncClient, cycle: int,
) -> dict:
    """Look for a usable results source in every state that doesn't have a
    hand-verified one, and keep the ones that prove out. Runs nightly, over
    the states due (_CRAWL_EVERY); returns per-state outcomes for the run
    report. A state whose crawl raised or couldn't save ("error", "save
    failed") is retried the next night, and the failures are alerted.

    Adoption needs POSITIVE proof, because this is the one path that adds
    a state with nobody reading it first: a discovered source is kept only
    if the nominees it names can be matched to Civitas's own FEC-derived
    candidates for those same races. Parsing cleanly is not enough, and
    neither is producing nothing — a first version of this accepted
    Nebraska's candidate FILING list (its numeric column is the number of
    seats to elect, so every candidate "tied") and a Hawaii file with no
    real headers, purely because neither claimed a nominee anybody could
    contradict.

    So a state whose results aren't certified yet is simply not adopted
    yet: it claims nothing, nothing can be proved, and the next weekly
    crawl picks it up once its nominees are real and checkable. Nothing is
    lost by waiting — a source adopted the week after certification is
    still months before the general.
    """
    # What another process wrote since this one last read either file.
    state_candidate_sources.invalidate_cache()
    election_dates.invalidate_cache()
    hand_verified = (_sources_file().get("states") or {})
    outcomes: dict[str, str] = {}
    problems: list[str] = []
    raised_token = _RAISED.set([])
    try:
        await _crawl_due_states(db, client, cycle, hand_verified, outcomes, problems)
    finally:
        # Reported even if the loop itself raised: what the states crawled
        # before it found is not lost with it.
        report_file_problems(
            "Election source crawl failed for some states",
            "These states' crawl raised or couldn't save (an \"error\" or \"save failed\" is "
            "retried the next night; a raise inside a step was treated as not fetching).",
            problems + (_RAISED.get() or []), "election-source-crawl",
        )
        _RAISED.reset(raised_token)
    return outcomes


async def _crawl_due_states(
    db: Session, client: httpx.AsyncClient, cycle: int, hand_verified: dict,
    outcomes: dict[str, str], problems: list[str],
) -> None:
    for state in sorted(ELECTION_DOMAINS):
        now = utcnow()
        record = _crawl_record(db, cycle, state)
        if not _crawl_due(record, now):
            continue
        try:
            outcome = await _crawl_state(db, client, cycle, state, hand_verified.get(state), record, now)
        except NotSaved as error:
            db.rollback()
            logger.error("Source crawl for %s found something it couldn't save: %s", state, error)
            outcome = "save failed"
            problems.append(f"{state}: {error}")
        except Exception as error:
            db.rollback()
            logger.exception("Source crawl raised for %s — retried next night", state)
            outcome = "error"
            problems.append(f"{state}: {type(error).__name__}: {error}")
        else:
            record["lastOk"] = now.isoformat()
        outcomes[state] = outcome
        api_cache_set(db, CRAWL_TIER, f"{cycle}-{state}", record,
                      normal_ttl_hours=_CRAWL_RECORD_TTL_HOURS)
        db.commit()


async def _crawl_state(
    db: Session, client: httpx.AsyncClient, cycle: int, state: str,
    hand: dict | None, record: dict, now: datetime,
) -> str:
    """One state's crawl; its outcome. Raises what its steps raise
    (NotSaved included) — the caller contains it to this state."""
    # A hand-verified state is left alone while its source works. When
    # it STOPS working — a state moves hosts between cycles, which is
    # the whole reason locations aren't trusted to stay put — it gets
    # crawled like any other, so a replacement can be found without
    # anyone editing a URL. Its LAW still comes from the hand-written
    # entry; only the location is rediscovered.
    outcome = "none"
    looked_for_filings = False
    if hand:
        # Logged, not reported: the nightly sync fetches this same source
        # and reports its raise already.
        still_works = await _fetch(client, cycle, state, hand, "Hand-verified source", report=False)
        if still_works is not None:
            # A primary date moves once a cycle, so it is read on the
            # weekly pass rather than nightly — off the same feed the
            # state's results already come from, never a stored
            # calendar anybody has to maintain.
            await _refresh_dates(client, cycle, state, hand)
            if not hand.get("filings"):
                outcome = await _adopt_filings(db, client, cycle, state, hand)
                looked_for_filings = True
            # google_civic is a national fallback for a state with no
            # real per-district vendor at all — unlike every other
            # hand-verified strategy, it must never shadow discovery
            # the way a working per-state source rightly does, or
            # this state's only path to a REAL vendor being found
            # (Clarity, Enhanced Voting, ...) is permanently blocked
            # for the rest of the cycle. Falls through to the same
            # discover_source() probe an unregistered state gets — a
            # find still can't auto-override the hand-verified civic
            # entry (see save_discovered/source_for_state
            # precedence), it just lands in the discovered-sources
            # file, visible for a human to hand-promote.
            if hand.get("strategy") != "google_civic":
                return outcome
        else:
            logger.warning(
                "Hand-verified source for %s is not fetching — looking for a "
                "replacement location", state,
            )
    rules = {
        k: v for k, v in (hand or {}).items()
        if k in NOMINATION_RULE_KEYS
    }
    earlier = outcome
    outcome = await _crawl_results_source(db, client, cycle, state, rules, record, now)
    if earlier != "none":  # what this state's filing-list pass already found
        outcome = f"{outcome}; {earlier}" if outcome not in ("none", "kept") else earlier
    # Every state is looked for a filing list, whatever became of its
    # results source: the list's general rows are the only way to see a
    # third-party or independent candidate, and a stored list is re-found
    # each week, so one that moves is followed.
    if not looked_for_filings:
        filings = await _adopt_filings(db, client, cycle, state, hand or {})
        if filings != "none":
            outcome = filings if outcome in ("none", "kept", "filings only") else f"{outcome}; {filings}"
    return outcome


async def _crawl_results_source(
    db: Session, client: httpx.AsyncClient, cycle: int, state: str,
    rules: dict, record: dict, now: datetime,
) -> str:
    """Find, prove and keep (or retire) a state's RESULTS source; the
    outcome."""
    try:
        found = await discover_source(client, state, cycle, rules)
    except Exception:
        # Portals' JSON has shapes nobody checked; a raise here must not
        # keep the state from its forget check and filing-list search.
        _note_raise(state, "Source discovery")
        found = None
    if not found:
        return await _forget_if_broken(client, cycle, state, record, now)

    records = await _fetch(client, cycle, state, found, "Discovered candidate source")
    if records is None:
        return "unusable"
    matched = sum(
        1 for record_ in records
        if _confirmed_match(db, cycle, state, record_) is not None
    )
    if not matched:
        logger.info(
            "Not adopting a source for %s: it names %d nominee(s), %d of whom are "
            "candidates on file for those races — %s",
            state, len(records), matched, found.get("_evidence"),
        )
        return "unproven" if not records else "rejected"
    # The state's filing list, if one was proved, is its own finding and
    # stays with it.
    kept = {k: v for k, v in (_discovered_source(state) or {}).items() if k == "filings"}
    save_discovered(state, kept | {k: v for k, v in found.items() if not k.startswith("_")}
                    | {"source_name": found.get("_evidence", "discovered"),
                       "description": f"{_FOUND_AUTOMATICALLY} on {now.date().isoformat()}: "
                                      f"{found.get('_evidence')}. Nomination rules are NOT "
                                      f"inferred — a state needing a runoff threshold, a "
                                      f"convention rule or top-two counting still needs a "
                                      f"hand-verified entry, which overrides this one."})
    record.pop("failingSince", None)
    logger.info("Adopted a discovered source for %s: %s", state, found.get("_evidence"))
    return f"adopted ({matched}/{len(records)} matched)"


def report_file_problems(subject: str, lead: str, problems: list[str], key: str) -> None:
    """One ops alert (once a day per `key`) for failures a pass contained
    rather than raised — so a state that went dark, or a change that was
    never written, reaches someone instead of only the log. A pass with
    none resolves the open alert for `key`."""
    from app.ops_alerts import resolve_ops_alert, send_ops_alert

    if not problems:
        resolve_ops_alert(key)
        return
    try:
        # Once a day per set of failing states, not per day: the first
        # alert of a day must not silence a different failure later in it.
        failing = sorted({p.split(":", 1)[0] for p in problems})
        digest = hashlib.sha1("|".join(failing).encode()).hexdigest()[:12]
        send_ops_alert(
            subject, lead + "\n" + "\n".join(problems),
            dedupe_key=f"{key}-{utcnow().date().isoformat()}-{digest}",
            condition=key,
        )
    except Exception:
        logger.exception("Could not send the %s ops alert", key)


async def _adopt_filings(
    db: Session, client: httpx.AsyncClient, cycle: int, state: str, base: dict,
) -> str:
    """Find and keep a state's candidate filing list, under the same bar
    the results sources face: the people it says are on the ballot have to
    be candidates on file for those races."""
    try:
        filings = await discover_filings(client, state, cycle)
    except Exception:
        _note_raise(state, "Filing-list discovery")
        return "none"
    if not filings:
        return "none"

    candidate_source = {**base, "filings": {
        k: v for k, v in filings.items() if not k.startswith("_")
    }}
    try:
        found = await fetch_ballot_candidates(client, cycle, state, candidate_source)
    except Exception:
        # The list discovery validated can differ from the file this reads
        # (a generalised link pattern), and a parse can raise on it. The
        # list already on file is read (and reported) by the nightly sync.
        if candidate_source["filings"] == filings_for_state(state):
            logger.exception("Filing-list read raised for %s", state)
        else:
            _note_raise(state, "Filing-list read")
        return "none"
    if not found:
        return "none"
    records = found["primary"] + found["general"]
    held = found["primary_date"]
    matched = sum(1 for r in records if _confirmed_match(db, cycle, state, r) is not None)
    if not matched:
        logger.info(
            "Not adopting a filing list for %s: it lists %d ballot candidates, none "
            "of whom are on file for those races — %s",
            state, len(records), filings.get("_evidence"),
        )
        return "filings rejected"

    # Only what was found here goes in the discovered file: for a
    # hand-verified state, a copy of its entry would shadow later edits to
    # it and serve as a stale "spare" source (sync_confirmed_candidates).
    # An earlier version stored exactly such a copy; one is dropped as it
    # is rewritten. A results source the crawler itself found and adopted
    # (a replacement for a broken hand-verified one, which carries the
    # hand entry's rules) says so in its description, and is kept whole.
    existing = _discovered_source(state) or {}
    if str(existing.get("description") or "").startswith(_FOUND_AUTOMATICALLY):
        stored = dict(existing)
    else:
        stored = {}
    stored["filings"] = candidate_source["filings"]
    stored.setdefault("source_name", filings["_evidence"])
    save_discovered(state, stored)
    if held:
        election_dates.save(state, cycle, {"primary": held})
    logger.info(
        "Adopted a candidate filing list for %s (%d/%d matched, primary %s): %s",
        state, matched, len(records), held, filings.get("_evidence"),
    )
    return f"filings adopted ({matched}/{len(records)} matched)"


async def _refresh_dates(
    client: httpx.AsyncClient, cycle: int, state: str, source: dict,
) -> None:
    try:
        dates = await election_dates.discover_dates(client, cycle, state, source)
    except Exception:
        _note_raise(state, "Election-date read")
        return
    if dates:
        election_dates.save(state, cycle, dates)


# The raises a pass contained, collected for its alert (_note_raise). A
# contained raise is treated like a source that returned nothing — but a
# programming error in an adapter looks exactly like an outage in the data,
# so each one is still reported, not only logged.
_RAISED: ContextVar[list[str] | None] = ContextVar("election_source_raised", default=None)


def _note_raise(state: str, what: str) -> None:
    """Log the exception being handled, and add it to the running pass's
    report. Call from an except block."""
    logger.exception("%s raised for %s", what, state)
    raised = _RAISED.get()
    if raised is not None:
        error = sys.exc_info()[1]
        raised.append(f"{state}: {what} raised {type(error).__name__}: {error}")


def _report_raises(subject: str, key: str, extra: list[str] | None = None) -> None:
    report_file_problems(
        subject,
        "Contained and treated as not fetching; each is also in the log with its traceback.",
        (_RAISED.get() or []) + (extra or []), key,
    )


async def _fetch(
    client: httpx.AsyncClient, cycle: int, state: str, source: dict, what: str,
    *, report: bool = True,
) -> list[dict] | None:
    """Run `source`'s strategy, a raise counted as not fetching (None) and
    reported (_note_raise): one source that breaks by raising (a host
    serving HTML where a spreadsheet was) must not end a pass over every
    state, nor keep its own state out of the "not fetching" handling —
    replacement, retirement — a source that returns nothing gets."""
    strategy = STRATEGIES.get(source.get("strategy"))
    if strategy is None:
        return None
    try:
        return await strategy(client, cycle, state, source)
    except Exception:
        if report:
            _note_raise(state, f"{what} fetch")
        else:
            logger.exception("%s fetch raised for %s", what, state)
        return None


def _discovered_source(state: str) -> dict | None:
    """What the crawler last proved for `state`, ignoring the
    hand-verified entry that normally shadows it."""
    from app.pipeline.fetch.state_candidate_sources import _load_discovered

    return _load_discovered().get(state.upper())


async def _forget_if_broken(
    client: httpx.AsyncClient, cycle: int, state: str, record: dict, now: datetime,
) -> str:
    """Drop a previously discovered results source that has stopped working.

    The other half of self-healing: finding a state's new location is only
    useful if the dead one goes away. But a failed fetch is as likely an
    outage as a move, so a source is forgotten only once it has failed on
    crawls _FORGET_AFTER apart ("failing since ..." until then) — and only
    its results source: a filing list the state also has was proved on its
    own. The state then falls back to showing every FEC filer, which is
    where it was before anything was discovered.
    """
    if state not in discovered_states():
        return "none"
    source = _discovered_source(state) or {}
    if STRATEGIES.get(source.get("strategy")) is None:
        # A filing list alone — no results source to test. The caller looks
        # for the filing list again, which keeps it current if it moves.
        return "filings only"
    # Logged, not reported: the nightly sync fetches this same source and
    # reports its raise already.
    records = await _fetch(client, cycle, state, source, "Discovered source", report=False)
    if records is not None:
        record.pop("failingSince", None)
        return "kept"
    since = record.get("failingSince")
    if since is None or now - datetime.fromisoformat(since) < _FORGET_AFTER:
        record.setdefault("failingSince", now.isoformat())
        logger.warning(
            "The discovered source for %s is not fetching (since %s) — kept until "
            "it has failed for %s: %s",
            state, record["failingSince"][:10], _FORGET_AFTER, source.get("source_name"),
        )
        return f"failing since {record['failingSince'][:10]}"
    logger.warning(
        "Forgetting the discovered source for %s — it has not fetched since %s: %s",
        state, since[:10], source.get("source_name"),
    )
    forget_results_source(state)
    record.pop("failingSince", None)
    return "forgotten"


# A comma after these is part of the name ("Olszewski, Jr."), not a
# "Last, First" printing.
_SUFFIX_AFTER_COMMA_RE = re.compile(r",\s*(?:Jr|Sr|II|III|IV|V)\.?$", re.IGNORECASE)


def _note_ballot_name(db: Session, cand: Candidate, record: dict) -> None:
    """Keep the name the state prints for a candidate it matched. A
    "Last, First" printing is left out rather than reordered: a comma does
    not reliably mark where the surname ends, and the FEC name already
    reads that way."""
    printed = clean_display_name(record.get("display_name") or "")
    if len(printed.split()) < 2:
        return
    if "," in _without_trailing_suffix(printed):
        return
    if cand.ballot_name != printed:
        cand.ballot_name = printed
        db.commit()


def _confirmed_match(db: Session, cycle: int, state: str, record: dict):
    race = db.query(Race).filter(
        Race.id == _race_id_for(db, cycle, state, record["office"], record["district"]),
    ).first()
    if race is None:
        return None
    return _match_candidate(
        _fec_candidates(race), record["last_name"], record["party"], record.get("display_name"),
    )


def _printed_party(record: dict) -> str | None:
    """The party label a state-office row keeps: the list's own printing,
    and only under OTHER_PARTY or NONPARTISAN -- a recognised party is its code, and a
    label beside it would be a second vocabulary for the same fact."""
    if record.get("party") not in LABELLED_PARTIES:
        return None
    label = " ".join(str(record.get("party_label") or "").split())
    return label[:80] or None


def _sync_statewide_nominees(
    db: Session, cycle: int, state: str, source: dict, records: list[dict],
    *, ballot_list: bool = False,
) -> int:
    """Persist this state's statewide-executive nominees and record that
    we looked. Returns how many were stored.

    Only runs for a state whose source entry opts in with
    `statewide_offices: true`. That flag is not a feature toggle — it is
    the difference between a real claim and a false one. Every adapter
    returns only what its state's feed contains, so a state nobody has
    parsed executive contests for returns zero of them, which is
    indistinguishable from a state that genuinely elects none this cycle.
    Writing a marker for both would tell a Georgian that Georgia has no
    governor's race. The flag says "this state's feed was checked and its
    executive contests are understood", so a zero under it is a real
    `confirmed none`.

    Rows absent from this run are DELETED rather than left behind: a
    nominee who withdraws, or one resolved from an amended re-post,
    must not linger as a confirmed name. The feed is the whole truth for
    (state, cycle) every time it is read.
    """
    if not source.get("statewide_offices"):
        return 0

    keep: set[tuple[str, str | None, str, str]] = set()
    for record in records:
        office, party = record["office"], record["party"]
        district = record["district"]
        name = record["last_name"]
        if is_not_a_person(name or "") or _NOT_ON_THE_BALLOT_RE.search(name or ""):
            # A results file's bucket won the contest ("Write-in" took
            # Illinois's 2026 Republican primary for Treasurer, where no
            # Republican filed): the seat has no nominee to name.
            continue
        # See the identical guard in _sync_state_leg_nominees: with
        # autoflush=False a duplicate key in one run queues two rows and
        # fails the unique constraint at commit.
        if (office, district, party, name) in keep:
            continue
        row = (
            db.query(StatewideNominee)
            .filter(
                StatewideNominee.state == state,
                StatewideNominee.cycle_year == cycle,
                StatewideNominee.office == office,
                StatewideNominee.district == district,
                StatewideNominee.party == party,
                StatewideNominee.display_name == name,
            )
            .first()
        )
        if row is None:
            row = StatewideNominee(
                state=state, cycle_year=cycle, office=office,
                district=district, party=party, display_name=name,
            )
            db.add(row)
        # `last_name` is the shared resolver's field name for "the name
        # this seat resolved to". For a statewide contest the adapter
        # reduced it with clean_display_name rather than surname (see
        # state_candidates_enhanced_voting), so it holds the whole
        # printed name, which is what gets rendered.
        row.party_label = _printed_party(record)
        row.source_name = str(source.get("source_name") or source.get("strategy") or "")
        row.updated_at = utcnow()
        keep.add((office, district, party, name))

    for row in (
        db.query(StatewideNominee)
        .filter(StatewideNominee.state == state, StatewideNominee.cycle_year == cycle)
        .all()
    ):
        if (row.office, row.district, row.party, row.display_name) not in keep:
            db.delete(row)

    api_cache_set(
        db, STATEWIDE_MARKER_TIER, statewide_marker_key(state, cycle),
        {
            # Explicit Z: utcnow() is naive UTC, and JS Date parses an
            # offset-less ISO string as LOCAL time — the same bug
            # elections._iso_utc exists to prevent for every other
            # timestamp this API returns.
            "checkedAt": utcnow().isoformat() + "Z",
            "count": len(keep),
            "sourceName": str(source.get("source_name") or ""),
            # Set only for a state whose adapter reads no executive
            # contests at all, because the state elects none this cycle
            # (Virginia and New Jersey choose governors in odd years).
            # Its "none" rests on the state's constitutional calendar,
            # not on a feed we parsed, and the page has to say which —
            # "as published by the Department of Elections" would claim
            # a reading that never happened.
            "basis": str(source.get("statewide_offices_basis") or "") or None,
            # Whether these names come from the state's list of who is on
            # the November ballot (every qualified candidate) or from
            # primary results. Primary results name only the nominees a
            # results file itemised: a nominee who ran unopposed is often
            # absent (Alabama prints no uncontested contest at all), and
            # no independent or minor-party candidate ever appears. The
            # page must say an office's names may be incomplete then.
            "ballotList": ballot_list,
        },
        normal_ttl_hours=STATEWIDE_MARKER_TTL_HOURS,
    )
    db.commit()
    return len(keep)


def _sync_state_leg_nominees(
    db: Session, cycle: int, state: str, source: dict, records: list[dict],
) -> int:
    """Persist this state's legislative nominees and record that we
    looked. Returns how many were stored.

    Gated on the same `statewide_offices` opt-in, and for the same
    reason: a state nobody has checked returns zero legislative contests,
    which is indistinguishable from a state that elects none. One flag
    covers both because it means the same thing either way — this state's
    real contest labels were read against the parsers. A state whose feed
    carries executive contests carries its legislative ones too; they are
    the same ballot, in the same response.

    Rows absent from this run are DELETED, as with the statewide table:
    the feed is the whole truth for (state, cycle) every time it is read.
    """
    if not source.get("statewide_offices"):
        return 0

    keep: set[tuple[str, str, str | None, str, str]] = set()
    for record in records:
        chamber, district, party = record["office"], record["district"], record["party"]
        seat = record.get("seat")
        name = record["last_name"]
        if is_not_a_person(name or "") or _NOT_ON_THE_BALLOT_RE.search(name or ""):
            # A results file's bucket won the contest ("Write-in" took
            # Illinois's 2026 Republican primary for Treasurer, where no
            # Republican filed): the seat has no nominee to name.
            continue
        # A key already handled in THIS run. The query below cannot see a
        # row added moments ago because SessionLocal sets autoflush=False,
        # so a feed that lists one nominee twice would queue two identical
        # rows and fail the unique constraint at commit — the exact shape
        # that took the coverage refresh down (see election_coverage
        # ._store_if_new). Nothing observed emits a duplicate today; this
        # is the guard, not a fix for a live symptom.
        if (chamber, district, seat, party, name) in keep:
            continue
        row = (
            db.query(StateLegNominee)
            .filter(
                StateLegNominee.state == state,
                StateLegNominee.cycle_year == cycle,
                StateLegNominee.chamber == chamber,
                StateLegNominee.district == district,
                StateLegNominee.seat == seat,
                StateLegNominee.party == party,
                StateLegNominee.display_name == name,
            )
            .first()
        )
        if row is None:
            row = StateLegNominee(
                state=state, cycle_year=cycle, chamber=chamber,
                district=district, seat=seat, party=party, display_name=name,
            )
            db.add(row)
        row.party_label = _printed_party(record)
        row.source_name = str(source.get("source_name") or source.get("strategy") or "")
        row.updated_at = utcnow()
        keep.add((chamber, district, seat, party, name))

    for row in (
        db.query(StateLegNominee)
        .filter(StateLegNominee.state == state, StateLegNominee.cycle_year == cycle)
        .all()
    ):
        if (row.chamber, row.district, row.seat, row.party,
                row.display_name) not in keep:
            db.delete(row)

    db.commit()
    return len(keep)


def _sync_judicial_nominees(
    db: Session, cycle: int, state: str, source: dict, records: list[dict],
    *, ballot_list: bool = False,
) -> int:
    """Persist this state's judicial nominees. Returns how many were stored.

    Gated on its OWN `judicial_offices` opt-in rather than riding
    `statewide_offices`, which the executive and legislative tables
    share. Those two really are one claim — a feed carrying a state's
    executive contests carries its legislative ones, same ballot, same
    response. Judicial is a genuinely separate claim: the flag asserts
    not only that this state's labels were read, but that its judicial
    contests are PARTISAN, so that a primary winner is a nominee for
    November rather than a judge already elected outright. Georgia's 92
    judgeships parse perfectly well through the same gate and must not
    be published, because Georgia's are non-partisan — see
    parse_judicial_office's docstring.

    Rows absent from this run are DELETED, as in both sibling tables:
    the feed is the whole truth for (state, cycle) every time it is read.
    """
    if not source.get("judicial_offices"):
        return 0

    keep: set[tuple[str, str | None, str | None, str, str]] = set()
    for record in records:
        court, party = record["office"], record["party"]
        district, seat = record["district"], record.get("seat")
        name = record["last_name"]
        if is_not_a_person(name or "") or _NOT_ON_THE_BALLOT_RE.search(name or ""):
            # A results file's bucket won the contest ("Write-in" took
            # Illinois's 2026 Republican primary for Treasurer, where no
            # Republican filed): the seat has no nominee to name.
            continue
        # Same autoflush=False guard as both siblings above.
        if (court, district, seat, party, name) in keep:
            continue
        row = (
            db.query(JudicialNominee)
            .filter(
                JudicialNominee.state == state,
                JudicialNominee.cycle_year == cycle,
                JudicialNominee.court == court,
                JudicialNominee.district == district,
                JudicialNominee.seat == seat,
                JudicialNominee.party == party,
                JudicialNominee.display_name == name,
            )
            .first()
        )
        if row is None:
            row = JudicialNominee(
                state=state, cycle_year=cycle, court=court, district=district,
                seat=seat, party=party, display_name=name,
            )
            db.add(row)
        row.party_label = _printed_party(record)
        row.source_name = str(source.get("source_name") or source.get("strategy") or "")
        row.updated_at = utcnow()
        keep.add((court, district, seat, party, name))

    for row in (
        db.query(JudicialNominee)
        .filter(JudicialNominee.state == state, JudicialNominee.cycle_year == cycle)
        .all()
    ):
        if (row.court, row.district, row.seat, row.party,
                row.display_name) not in keep:
            db.delete(row)

    # Written even when `keep` is empty, which is the whole point: a
    # state whose judicial seats were all decided in its primary has
    # ZERO November contests, and that is a checked answer, not a gap.
    # Idaho is exactly that case — all three of its matched contests
    # were unopposed and therefore already elected under Idaho Code
    # 34-1217. Without this marker an empty section is indistinguishable
    # from a state nobody has looked at.
    api_cache_set(
        db, JUDICIAL_MARKER_TIER, judicial_marker_key(state, cycle),
        {
            "checkedAt": utcnow().isoformat() + "Z",
            "count": len(keep),
            "sourceName": str(source.get("source_name") or ""),
            # See the statewide marker's field of the same name.
            "ballotList": ballot_list,
        },
        normal_ttl_hours=JUDICIAL_MARKER_TTL_HOURS,
    )
    db.commit()
    return len(keep)


def _is_ballot_list(source: dict, records: list[dict]) -> bool:
    """Whether the state-office rows `records` (read from `source`) are
    the state's list of who is on the November ballot rather than primary
    results: the adapter's own answer for this run when it gave one, else
    its strategy's (BALLOT_LIST_STRATEGIES)."""
    said = getattr(records, "ballot_list", None)
    if said is not None:
        return bool(said)
    return source.get("strategy") in BALLOT_LIST_STRATEGIES


def _state_office_source(
    db: Session, cycle: int, state: str,
    source: dict, records: list[dict], main_answered: bool,
    general: dict | None, general_records: list[dict] | None,
) -> tuple[dict, list[dict], bool]:
    """(source, records, answered) for this state's statewide and
    legislative offices this run.

    Without a general_list that opts in with its own statewide_offices,
    that is the main source, as it always was.

    With one, the certified list is the source once it answers with
    state-office rows: it is the ballot itself. Until then -- it has not
    been published (None), or it answered with federal rows only -- the
    main source's primary results stand in, exactly as they did before
    the list was configured, when the main source answered and itself
    opts in (_sync_statewide_nominees gates on the source's own flag).
    Otherwise every one of these states would drop from the nominees it
    had to "not yet covered" (or keep a stale marker) for the weeks
    between its primary and its list's certification.

    Once the list HAS answered with state rows for this cycle (the stored
    marker names it as the source), it stays authoritative: a later run
    where it is down says nothing, rather than swapping the certified
    ballot back to primary winners and deleting its independents.
    """
    if not (general and general.get("statewide_offices")):
        return source, records, main_answered
    if any(r["office"] not in ("S", "H") for r in general_records or []):
        return general, general_records or [], True
    marker = api_cache_get(
        db, STATEWIDE_MARKER_TIER, statewide_marker_key(state, cycle),
        max_age_hours=STATEWIDE_MARKER_TTL_HOURS,
    ) or {}
    # The lock is the marker's sourceName, so renaming the list's
    # source_name in the sources file mid-cycle unlocks it until the list
    # next answers: primary results would stand in again for those nights.
    list_name = str(general.get("source_name") or "")
    if list_name and marker.get("sourceName") == list_name:
        return general, [], False
    return source, records, main_answered


async def sync_confirmed_candidates(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """_sync_confirmed_candidates, with every source raise it contained
    reported in one alert."""
    token = _RAISED.set([])
    try:
        return await _sync_confirmed_candidates(db, client, cycle)
    finally:
        _report_raises("Election sources raised in the ballot sync", "election-sync-raised")
        _RAISED.reset(token)


async def _sync_confirmed_candidates(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """Confirm every registered state's general-election candidates
    against this cycle's Race/Candidate rows. Returns per-state counts —
    `confirmed` (candidates newly or already flagged), `unmatched`
    (records that couldn't be safely matched to one FEC candidate), and
    `status` (`ok` / `fetch_failed` / `not_configured`)."""
    # Refresh the national calendar FIRST and every run, not just on each
    # state's weekly crawl: a state whose results file is addressed by
    # election date (Minnesota) can't be fetched at all without it, so
    # leaving it to the weekly pass would leave that state dark for up to
    # a week — and dark on a fresh deploy. Three calls.
    #
    # One locked write for the whole calendar. Only a COMPLETE read may
    # retract a Senate election or mark the calendar read: the roster
    # takes "not listed" to mean no race, and deletes it.
    state_candidate_sources.invalidate_cache()
    election_dates.invalidate_cache()
    problems: list[str] = []
    try:
        calendar, complete = await election_dates.fetch_fec_calendar(client, cycle)
        if calendar:
            election_dates.save_calendar(
                cycle, calendar, complete=complete, read_on=utcnow().date().isoformat(),
            )
        if not complete:
            problems.append(
                "the FEC election-date calendar read was incomplete, so no Senate "
                "election was retracted and the calendar was not marked read"
            )
    except NotSaved as error:
        problems.append(f"the FEC election-date calendar was not saved: {error}")
    except Exception as error:
        logger.exception("FEC election-date calendar read failed")
        problems.append(f"the FEC election-date calendar read raised: {error}")
    report_file_problems(
        "Election-date calendar not recorded",
        "Senate races and primary dates rest on this calendar; the next sync retries.",
        problems, "election-date-calendar",
    )

    results: dict[str, dict] = {}
    for state in sorted(configured_states()):
        source = source_for_state(state)
        configured = source or {}
        strategy = STRATEGIES.get(source["strategy"]) if source else None
        if strategy is None:
            logger.error(
                "State candidate source for %s references unknown strategy %r",
                state, source.get("strategy") if source else None,
            )
            results[state] = {"confirmed": 0, "unmatched": 0, "status": "not_configured"}
            continue

        # A state's certified November list, when it has one, speaks for its
        # federal races outright (see general_list in the sources file). It
        # runs FIRST so a nominee the list has replaced is never confirmed
        # from primary results only to be unconfirmed moments later.
        general = source.get("general_list")
        general_records = None
        if general:
            general_records = await _fetch(client, cycle, state, general, "Certified general list")
            if general_records is not None and not general_records:
                # A list not published yet (its page does not name this
                # year's election, or it is not due until after the
                # primary) answers [] -- healthy, not a failed fetch. It
                # speaks for no race, so it is handled exactly like one
                # that did not answer.
                general_records = None

        records = await _fetch(client, cycle, state, source, "Confirmed-candidate")

        fallback = source.get("fallback")
        if records is None and fallback and STRATEGIES.get(fallback.get("strategy")):
            # A state's own second choice, named in its entry — Wisconsin's
            # canvass file name changes between cycles, and until the new
            # one is known its national fallback still says something.
            logger.info("Falling back to %s for %s", fallback["strategy"], state)
            records = await _fetch(client, cycle, state, fallback, "Fallback")
            if records is not None:
                source = fallback
        if records is None:
            # A hand-verified source that has broken falls back to whatever
            # the crawler last proved for this state, rather than the state
            # going dark until someone edits a URL.
            spare = _discovered_source(state)
            if spare and spare != source:
                logger.info("Falling back to the discovered source for %s", state)
                records = await _fetch(client, cycle, state, spare, "Discovered spare source")
                if records is not None:
                    # Its records are the spare's: never the broken entry's
                    # authority (general_ballot_complete) or attribution.
                    source = spare
        if records is None and general_records is None:
            results[state] = {"confirmed": 0, "unmatched": 0, "status": "fetch_failed"}
            continue
        # Whether the main source said anything this run. When it failed
        # and only the certified federal list answered, the main source has
        # nothing to say about the state offices: syncing an empty list
        # would record the state as checked and holding none, and the page
        # would call that a confirmed absence.
        main_answered = records is not None
        # An answer holding NO contest at all is a feed with nothing in it
        # yet -- a primary not settled, a results page not posted -- not a
        # read of a ballot: every state has a House seat on every even-year
        # ballot, so a real read always yields something. Syncing it would
        # record the state as checked and holding no statewide offices,
        # which is what every opted-in state said between the start of a
        # cycle and its primary settling (New Hampshire's 21-day settle
        # window ran to 2026-09-29). A state whose "none" rests on its
        # constitutional calendar rather than the feed
        # (statewide_offices_basis) is still recorded.
        if main_answered and not records and not source.get("statewide_offices_basis"):
            main_answered = False
        # The adapter's federal rows are settled but its state-office read
        # is not (a party's primary or a runoff still settling -- see
        # SourceRecords): the federal rows are applied as usual, and the
        # main source says nothing about the state offices this run.
        main_state_answered = main_answered and not getattr(records, "state_offices_incomplete", False)
        records = records or []

        # Neither a statewide executive office (Governor, AG, ...) nor a
        # seat in the state legislature has an FEC race to confirm
        # against, so each takes an entirely different path: the state's
        # own feed is the record, stored as-is. Split first rather than
        # branching inside the federal loop, which otherwise counts every
        # one of them as `unmatched` against a Race id that cannot exist.
        #
        # A certified November list that opts in with its OWN
        # statewide_offices is the source for these instead: it is the
        # ballot itself, independents included, where primary results can
        # only name each party's winner (and New Mexico's results portal
        # no longer serves its primary at all). See _state_office_source
        # for when the main source's primary results stand in for it.
        main_records = records
        state_source, state_records, state_answered = _state_office_source(
            db, cycle, state, source, records, main_state_answered, general, general_records,
        )
        statewide = [r for r in state_records if r["office"] in STATEWIDE_OFFICE_LABELS]
        if configured.get("joint_governor_ticket"):
            # One vote for governor and lieutenant governor together: the
            # ballot has one contest, the ticket (join_governor_tickets).
            statewide = join_governor_tickets(statewide)
        state_leg = [r for r in state_records if r["office"] in STATE_LEG_CHAMBER_LABELS]
        judicial = [r for r in records if r["office"] in JUDICIAL_COURT_LABELS]
        records = [
            r for r in records
            if r["office"] not in STATEWIDE_OFFICE_LABELS
            and r["office"] not in STATE_LEG_CHAMBER_LABELS
            and r["office"] not in JUDICIAL_COURT_LABELS
        ]
        statewide_count = state_leg_count = judicial_count = 0
        if state_answered:
            statewide_count = _sync_statewide_nominees(
                db, cycle, state, state_source, statewide,
                ballot_list=_is_ballot_list(state_source, state_records),
            )
            state_leg_count = _sync_state_leg_nominees(db, cycle, state, state_source, state_leg)
        if main_state_answered:
            judicial_count = _sync_judicial_nominees(
                db, cycle, state, source, judicial, ballot_list=_is_ballot_list(source, main_records),
            )

        # A state with its own general FILING list gets its November ballot
        # from that list (sync_ballot_filings), which is what may speak for
        # candidates this results file cannot see or has gone stale on.
        # Here, it only confirms who the results name.
        ballot_is_elsewhere = _has_general_filings(state)
        if general_records is not None:
            # The certified ballot answered: it alone decides every federal
            # race it covers. Races it does not cover — a national source
            # that only knows the districts it has a verified address for —
            # keep what primary results say, non-authoritatively, exactly
            # as if the list did not exist for them. (The state offices
            # were settled above, by _state_office_source.)
            general_federal = [r for r in general_records if r["office"] in ("S", "H")]
            covered = {_race_id_for(db, cycle, state, r["office"], r["district"]) for r in general_federal}
            races_here = {
                rid for (rid,) in db.query(Race.id).filter(Race.state == state, Race.cycle_year == cycle)
            }
            applied = _apply_ballot(
                db, cycle, state, general_federal, keep_unlisted=True, authoritative=True,
                scope=covered,
            )
            rest = [r for r in records if _race_id_for(db, cycle, state, r["office"], r["district"]) not in covered]
            if rest:
                # Primary results beside a certified list never prune: a
                # race the list didn't answer for tonight (an empty or
                # partial read) may hold its certified third-party rows,
                # which results can't list (_may_prune).
                more = _apply_ballot(
                    db, cycle, state, rest, keep_unlisted=not ballot_is_elsewhere, authoritative=False,
                    scope=races_here - covered, prune=_may_prune(configured, {}),
                )
                applied = {k: applied[k] + more[k] for k in applied}
            _record_ballot_basis(
                db, cycle, state,
                {**general, "general_ballot_complete": bool(races_here) and races_here <= covered},
                races=covered & races_here,
            )
        else:
            # An empty answer is a ballot not published yet: it names no
            # race, so it may neither unconfirm anyone nor record the state's
            # ballot as the complete certified one (general_ballot_complete
            # describes the document once it exists, not its absence).
            published = bool(records)
            applied = _apply_ballot(
                db, cycle, state, records,
                keep_unlisted=not ballot_is_elsewhere,
                authoritative=bool(source.get("general_ballot_complete")) and not ballot_is_elsewhere and published,
                prune=_may_prune(configured, source) and published,
            )
            if not ballot_is_elsewhere:
                _record_ballot_basis(
                    db, cycle, state, source if published else {**source, "general_ballot_complete": False},
                )
        confirmed, unmatched = applied["confirmed"], applied["unmatched"]

        results[state] = {
            "confirmed": confirmed, "unmatched": unmatched,
            "ballotOnly": applied["ballotOnly"], "unconfirmed": applied["unconfirmed"],
            "statewide": statewide_count, "stateLeg": state_leg_count,
            "judicial": judicial_count,
            "status": "ok",
        }

    return results


async def sync_ballot_filings(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """_sync_ballot_filings, with every filing-list raise it contained
    reported in one alert."""
    token = _RAISED.set([])
    try:
        return await _sync_ballot_filings(db, client, cycle)
    finally:
        _report_raises("Filing lists raised in the ballot sync", "election-filings-raised")
        _RAISED.reset(token)


async def _sync_ballot_filings(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """Flag what a state's own candidate filing list says about both its
    ballots, and record its primary date.

    Its PRIMARY list is what the ballot page has to work with for most of
    a cycle — before any primary, a race's only other answer is every
    active FEC filer, including people who never filed with the state at
    all. It is weaker than a confirmed nominee and never overrides one.

    Its GENERAL list is the state naming its November ballot outright, so
    it confirms candidates exactly as a results file does — and it is the
    ONLY way to see a Libertarian or Green candidate, who reaches November
    without appearing in any primary and is therefore invisible to
    confirmation derived from primary results.
    """
    results: dict[str, dict] = {}
    problems: list[str] = []
    for state in sorted(states_with_filings()):
        source = {**(source_for_state(state) or {}), "filings": filings_for_state(state)}
        try:
            found = await fetch_ballot_candidates(client, cycle, state, source)
        except Exception:
            _note_raise(state, "Ballot-filing fetch")
            found = None
        if found is None:
            if _filings_speak_for_november(state) and api_cache_get(
                db, BALLOT_BASIS_TIER, ballot_basis_key(state, cycle),
                max_age_hours=STATEWIDE_MARKER_TTL_HOURS,
            ) is None:
                # Nothing has said what this cycle's ballot rests on, and the
                # page would otherwise fall back to the entry's claim of a
                # complete one. A basis a good night recorded is left alone.
                _record_ballot_basis(db, cycle, state, {**source, "general_ballot_complete": False})
            results[state] = {"primary": 0, "general": 0, "unmatched": 0,
                              "status": "fetch_failed"}
            continue

        if found["primary_date"]:
            try:
                election_dates.save(state, cycle, {"primary": found["primary_date"]})
            except NotSaved as error:
                problems.append(f"{state}: {error}")
        counts = {"primary": 0, "general": 0}
        unmatched = 0
        for record in found["primary"]:
            match = _confirmed_match(db, cycle, state, record)
            if match is None:
                unmatched += 1
                continue
            if not match.on_primary_ballot:
                match.on_primary_ballot = True
                db.commit()
            _note_ballot_name(db, match, record)
            counts["primary"] += 1
        applied = {"ballotOnly": 0, "unconfirmed": 0}
        if not found["general"] and _filings_speak_for_november(state):
            # The list speaks for November but names nobody for it yet (a
            # primary-season list): until it does, the ballot is not known
            # whole, whatever the state's entry claims for later — and
            # nothing else records a basis for this state.
            _record_ballot_basis(db, cycle, state, {**source, "general_ballot_complete": False})
        elif found["general"] and not _filings_speak_for_november(state):
            logger.info(
                "%s: %d general row(s) on a crawler-found filing list not applied — "
                "the state's verified source speaks for November",
                state, len(found["general"]),
            )
        elif found["general"]:
            applied = _apply_ballot(
                db, cycle, state, found["general"], keep_unlisted=True,
                authoritative=bool(source.get("general_ballot_complete")),
            )
            _record_ballot_basis(db, cycle, state, source)
            counts["general"] = applied["confirmed"]
            unmatched += applied["unmatched"]
        results[state] = {
            **counts, "unmatched": unmatched,
            "ballotOnly": applied["ballotOnly"], "unconfirmed": applied["unconfirmed"],
            "primary_date": found["primary_date"], "status": "ok",
        }
    report_file_problems(
        "Primary dates from filing lists not saved",
        "These states' filing lists dated their primary, but the date was not written.",
        problems, "election-filing-dates",
    )
    return results
