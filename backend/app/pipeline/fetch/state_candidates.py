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
directly — NOT elections.py's _last_name_matches, which matches a surname
against the TRAILING tokens of a "First Last"-formatted name (that's the
right shape for _incumbent_link's target, Representative/Senator.name, but
Candidate.name is FEC's own "LAST, FIRST MIDDLE" format, so the surname is
the LEADING part before the comma — the same extraction _incumbent_link
itself does to `cand.name` before calling _last_name_matches on someone
else's name). Exact string equality on the extracted, lowercased surname
(not substring) for the same "lee" != "leeman" reason. A record that
matches zero or more than one candidate (after a party-based tiebreak
attempt) is logged and skipped, never guessed — the existing FEC candidate
list is left exactly as it was for that race, which is always at least as
accurate as before this sync ran, never worse.
"""

import logging
import re
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
from app.time_utils import utcnow
from app.pipeline.cache import api_cache_set
from app.pipeline.fetch.state_candidate_sources import (
    _load as _sources_file,
    configured_states,
    discovered_states,
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
    PARTY_CODE_MAP,
    ballot_basis_key,
    JUDICIAL_COURT_LABELS,
    JUDICIAL_MARKER_TIER,
    JUDICIAL_MARKER_TTL_HOURS,
    judicial_marker_key,
    STATE_LEG_CHAMBER_LABELS,
    STATEWIDE_MARKER_TIER,
    STATEWIDE_MARKER_TTL_HOURS,
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
    "canvass_summary_pdf": _fetch_canvass_summary_pdf,
    "dos_canlist": _fetch_dos_canlist,
    "google_civic": _fetch_civic,
    "nh_results": _fetch_nh,
    "enhanced_voting": _fetch_enhanced_voting,
}



def is_configured(state: str) -> bool:
    """Whether `state` has both a registered source AND a strategy
    function for it — an entry with a typo'd/unregistered strategy key is
    a config bug, not a signal to guess."""
    source = source_for_state(state)
    return source is not None and source.get("strategy") in STRATEGIES


def _race_id_for(cycle: int, state: str, office: str, district: int | None) -> str:
    """Same id convention election_pipeline._race_id uses for a REGULAR
    race. Nothing registered here reaches a special election: every
    adapter's discovery matches that state's PRIMARY by name, so a
    special's results are never fetched in the first place. A state whose
    special general shares this cycle's ballot would need both that
    discovery and this id taught the "-SPECIAL" suffix."""
    if office == "S":
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


def _given_names(name: str) -> list[str]:
    """The given-name tokens, folded, with honorifics and initials dropped.

    Both sides are normalised the same way: FEC files "SULLIVAN, DANIEL
    J" (surname, then given names) and a state prints "Sullivan, Daniel
    J. Jr." or "Daniel J. Sullivan Jr.". Taking the tokens AFTER any comma
    handles the first two; for the third the leading token already is the
    given name."""
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
) -> list[Candidate]:
    """Candidates a state's surname reaches only indirectly. Each rule runs
    only when the one before found nobody, and each still has to come out
    UNIQUE in the race (the caller refuses anything ambiguous)."""
    # A MULTI-WORD surname survives on the FEC side ("WASSERMAN SCHULTZ,
    # DEBBIE") but not on the state's, because a state publishes a display
    # name and the trailing token is all that can be taken from "Debbie
    # Wasserman Schultz" without guessing where the surname begins.
    found = [c for c in candidates if _candidate_surname(c.name).split()[-1:] == [target]]
    if found:
        return found
    # A married or former surname filed as a given name: the ballot says
    # "Ashley Hinson" and FEC has "ARENHOLZ, ASHLEY HINSON" (IA Senate,
    # 2026 — the Republican nominee, unmatched without this).
    found = [c for c in candidates if _given_names(c.name)[-1:] == [target]]
    if found:
        return found
    # A one-letter slip on either side, only with the given name agreeing
    # too: the ballot's "Brandon Coulter Daugherty" is FEC's "DAUGHTERY,
    # BRANDON" (MO-2, 2026). Short surnames are excluded — one edit away
    # from "Lee" is too many real names.
    wanted = _first_name_key(display_name or "")
    if wanted and len(target) >= 5:
        return [
            c for c in candidates
            if _first_name_key(c.name) == wanted
            and _one_transposition_or_typo(_candidate_surname(c.name), target)
        ]
    return []


def _match_candidate(
    candidates: list[Candidate], last_name: str, party_code: str,
    display_name: str | None = None,
) -> Candidate | None:
    target = _candidate_surname(last_name)
    matches = [c for c in candidates if _candidate_surname(c.name) == target]
    if not matches:
        matches = _surname_fallbacks(candidates, target, display_name)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None

    expected_party = PARTY_CODE_MAP.get(party_code)
    pool = [c for c in matches if c.party == expected_party] or matches
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
    if len({(_first_name_key(c.name), c.party) for c in pool}) == 1 and _first_name_key(pool[0].name):
        return max(pool, key=lambda c: (bool(c.has_raised_funds), c.contributions or 0, c.id))
    return None


def _fec_candidates(race: Race) -> list[Candidate]:
    """The race's FEC rows. A ballot-only row from an earlier run is the
    state's own record echoed back, never something to match against: if
    it were, a candidate who files with the FEC later could lose the match
    to their own placeholder."""
    return [c for c in race.candidates if c.fec_filed]


# What a results file can print where a candidate's name goes. None of it
# is a person, and a ballot-only row makes whatever it is visible.
_NOT_A_PERSON_RE = re.compile(
    r"write[\s-]*ins?\b|scattering|\b(over|under)\s*votes?\b|\bblank\b|"
    r"none of (these|the above)|uncommitted|withdrawn",
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


def _keep_ballot_only(db: Session, race: Race, record: dict) -> str | None:
    """Record a state-listed candidate who has no FEC row, and return the
    row's id — or None when the record is not safe to show as a person.

    A surname alone is not enough to show anyone (Oregon's results PDF
    prints only surnames, and "Smith (R)" is not a ballot entry), and a
    results file's non-candidate rows must never become one."""
    display = (record.get("display_name") or "").strip()
    words = [w for w in re.split(r"[\s,]+", display) if any(ch.isalpha() for ch in w)]
    if len(words) < 2 or _NOT_A_PERSON_RE.search(display):
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(display)).strip("-")
    cid = f"{BALLOT_ONLY_ID_PREFIX}{race.id}:{slug}"
    cand = db.query(Candidate).filter(Candidate.id == cid).first()
    if cand is None:
        cand = Candidate(id=cid, race_id=race.id)
        db.add(cand)
    cand.name = _fec_style_name(display, record["last_name"])
    # A party the matcher has no code for (South Carolina's Workers, say)
    # keeps the state's own label rather than reading as "unknown".
    cand.party = (
        PARTY_CODE_MAP.get(record.get("party") or "")
        or (record.get("party_label") or "").strip().upper()
        or "UNK"
    )
    cand.has_raised_funds = False
    cand.incumbent_challenge = None
    cand.candidate_status = None
    cand.confirmed_general = True
    db.commit()
    return cid


def _has_general_filings(source: dict) -> bool:
    """Whether this state has a filing list, whose general-election rows
    then speak for its November ballot (North Carolina's do)."""
    return bool(source.get("filings"))


def _apply_ballot(
    db: Session, cycle: int, state: str, records: list[dict],
    *, keep_unlisted: bool, authoritative: bool,
) -> dict:
    """Confirm a state's federal records against its races.

    `keep_unlisted`: a record that matches no FEC candidate is still a
    person on the ballot — show them (ballot-only row) rather than drop
    them. `authoritative`: these records ARE the certified November
    ballot, so anyone confirmed in a race they cover but not on them is
    unconfirmed (_unconfirm_off_ballot)."""
    confirmed = unmatched = 0
    ballot_only: set[str] = set()
    listed: dict[str, set[str]] = {}
    for record in records:
        race_id = _race_id_for(cycle, state, record["office"], record["district"])
        race = db.query(Race).filter(Race.id == race_id).first()
        if race is None:
            unmatched += 1
            continue
        listed.setdefault(race.id, set())
        match = _match_candidate(
            _fec_candidates(race), record["last_name"], record["party"], record.get("display_name"),
        )
        if match is None:
            kept = _keep_ballot_only(db, race, record) if keep_unlisted else None
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
        listed[race.id].add(match.id)
        confirmed += 1
    if keep_unlisted:
        _prune_ballot_only(db, cycle, state, ballot_only)
    withdrawn = _unconfirm_off_ballot(db, listed) if authoritative else 0
    return {
        "confirmed": confirmed, "unmatched": unmatched,
        "ballotOnly": len(ballot_only), "unconfirmed": withdrawn,
    }


def _record_ballot_basis(db: Session, cycle: int, state: str, source: dict) -> None:
    """Say which source answered for this state tonight — see
    BALLOT_BASIS_TIER. Read by the API to decide "confirmed" (the whole
    ballot) versus "nominees" (primary results) per state."""
    api_cache_set(
        db, BALLOT_BASIS_TIER, ballot_basis_key(state, cycle),
        {
            "complete": bool(source.get("general_ballot_complete")),
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


def _prune_ballot_only(db: Session, cycle: int, state: str, kept: set[str]) -> None:
    """Drop ballot-only rows this state's source no longer lists: the
    candidate withdrew, or filed with the FEC and now matches a real row.
    Only reached after a successful fetch — a source that failed says
    nothing about who is on the ballot."""
    stale = (
        db.query(Candidate)
        .join(Race, Candidate.race_id == Race.id)
        .filter(
            Race.state == state,
            Race.cycle_year == cycle,
            Candidate.id.startswith(BALLOT_ONLY_ID_PREFIX),
        )
        .all()
    )
    removed = [c for c in stale if c.id not in kept]
    for cand in removed:
        db.delete(cand)
    if removed:
        db.commit()
        logger.info("%s: removed %d ballot-only candidate(s) no longer listed", state, len(removed))


async def crawl_for_new_sources(
    db: Session, client: httpx.AsyncClient, cycle: int,
) -> dict:
    """Look for a usable results source in every state that doesn't have a
    hand-verified one, and keep the ones that prove out. Returns per-state
    outcomes for the run report.

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
    hand_verified = (_sources_file().get("states") or {})
    outcomes: dict[str, str] = {}
    for state in sorted(ELECTION_DOMAINS):
        hand = hand_verified.get(state)
        # A hand-verified state is left alone while its source works. When
        # it STOPS working — a state moves hosts between cycles, which is
        # the whole reason locations aren't trusted to stay put — it gets
        # crawled like any other, so a replacement can be found without
        # anyone editing a URL. Its LAW still comes from the hand-written
        # entry; only the location is rediscovered.
        if hand:
            strategy = STRATEGIES.get(hand.get("strategy"))
            still_works = await strategy(client, cycle, state, hand) if strategy else None
            if still_works is not None:
                # A primary date moves once a cycle, so it is read on the
                # weekly pass rather than nightly — off the same feed the
                # state's results already come from, never a stored
                # calendar anybody has to maintain.
                await _refresh_dates(client, cycle, state, hand)
                if not hand.get("filings"):
                    outcomes[state] = await _adopt_filings(db, client, cycle, state, hand)
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
                    continue
            else:
                logger.warning(
                    "Hand-verified source for %s is not fetching — looking for a "
                    "replacement location", state,
                )
        rules = {
            k: v for k, v in (hand or {}).items()
            if k in ("runoff_threshold_pct", "advance_count")
        }
        try:
            found = await discover_source(client, state, cycle, rules)
        except Exception:
            logger.exception("Source discovery raised for %s", state)
            outcomes[state] = "error"
            continue
        if not found:
            outcomes[state] = await _forget_if_broken(client, cycle, state)
            # A state with no usable RESULTS source can still publish a
            # filing list, and before its primary that is the only answer
            # there is — so it is looked for either way.
            if outcomes[state] in ("none", "forgotten"):
                filings = await _adopt_filings(db, client, cycle, state, {})
                if filings != "none":
                    outcomes[state] = filings
            continue

        strategy = STRATEGIES.get(found.get("strategy"))
        records = await strategy(client, cycle, state, found) if strategy else None
        if records is None:
            outcomes[state] = "unusable"
            continue
        matched = sum(
            1 for record in records
            if _confirmed_match(db, cycle, state, record) is not None
        )
        if not matched:
            logger.info(
                "Not adopting a source for %s: it names %d nominee(s), %d of whom are "
                "candidates on file for those races — %s",
                state, len(records), matched, found.get("_evidence"),
            )
            outcomes[state] = "unproven" if not records else "rejected"
            continue
        save_discovered(state, {k: v for k, v in found.items() if not k.startswith("_")}
                        | {"source_name": found.get("_evidence", "discovered"),
                           "description": f"Found automatically on {utcnow().date().isoformat()}: "
                                          f"{found.get('_evidence')}. Nomination rules are NOT "
                                          f"inferred — a state needing a runoff threshold, a "
                                          f"convention rule or top-two counting still needs a "
                                          f"hand-verified entry, which overrides this one."})
        outcomes[state] = f"adopted ({matched}/{len(records)} matched)"
        logger.info("Adopted a discovered source for %s: %s", state, found.get("_evidence"))
    return outcomes


async def _adopt_filings(
    db: Session, client: httpx.AsyncClient, cycle: int, state: str, base: dict,
) -> str:
    """Find and keep a state's candidate filing list, under the same bar
    the results sources face: the people it says are on the ballot have to
    be candidates on file for those races."""
    try:
        filings = await discover_filings(client, state, cycle)
    except Exception:
        logger.exception("Filing-list discovery raised for %s", state)
        return "none"
    if not filings:
        return "none"

    candidate_source = {**base, "filings": {
        k: v for k, v in filings.items() if not k.startswith("_")
    }}
    found = await fetch_ballot_candidates(client, cycle, state, candidate_source)
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

    stored = dict(_discovered_source(state) or base or {})
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
        logger.exception("Election-date read raised for %s", state)
        return
    if dates:
        election_dates.save(state, cycle, dates)


async def _no_strategy(*_args, **_kwargs) -> None:
    return None


def _discovered_source(state: str) -> dict | None:
    """What the crawler last proved for `state`, ignoring the
    hand-verified entry that normally shadows it."""
    from app.pipeline.fetch.state_candidate_sources import _load_discovered

    return _load_discovered().get(state.upper())


async def _forget_if_broken(client: httpx.AsyncClient, cycle: int, state: str) -> str:
    """Drop a previously discovered source that has stopped working.

    The other half of self-healing: finding a state's new location is only
    useful if the dead one goes away. A source that still fetches is kept
    even when this week's crawl didn't re-find it (a page can be down for
    an hour), so only one that actually fails is forgotten — and the state
    then falls back to showing every FEC filer, which is where it was
    before anything was discovered.
    """
    if state not in discovered_states():
        return "none"
    source = source_for_state(state) or {}
    strategy = STRATEGIES.get(source.get("strategy"))
    records = await strategy(client, cycle, state, source) if strategy else None
    if records is not None:
        return "kept"
    logger.warning(
        "Forgetting the discovered source for %s — it no longer fetches: %s",
        state, source.get("source_name"),
    )
    save_discovered(state, None)
    return "forgotten"


def _confirmed_match(db: Session, cycle: int, state: str, record: dict):
    race = db.query(Race).filter(
        Race.id == _race_id_for(cycle, state, record["office"], record["district"]),
    ).first()
    if race is None:
        return None
    return _match_candidate(
        _fec_candidates(race), record["last_name"], record["party"], record.get("display_name"),
    )


def _sync_statewide_nominees(
    db: Session, cycle: int, state: str, source: dict, records: list[dict],
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
        },
        normal_ttl_hours=JUDICIAL_MARKER_TTL_HOURS,
    )
    db.commit()
    return len(keep)


async def sync_confirmed_candidates(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """Confirm every registered state's general-election candidates
    against this cycle's Race/Candidate rows. Returns per-state counts —
    `confirmed` (candidates newly or already flagged), `unmatched`
    (records that couldn't be safely matched to one FEC candidate), and
    `status` (`ok` / `fetch_failed` / `not_configured`)."""
    # Refresh the national calendar FIRST and every run, not just on the
    # weekly crawl: a state whose results file is addressed by election
    # date (Minnesota) can't be fetched at all without it, so leaving it
    # to the weekly pass would leave that state dark until the next
    # Sunday — and dark on a fresh deploy. Three calls.
    try:
        calendar = await election_dates.fetch_fec_calendar(client, cycle)
        for state, dates in calendar.items():
            election_dates.save(state, cycle, dates)
        if calendar:
            election_dates.mark_calendar_read(cycle, utcnow().date().isoformat())
    except Exception:
        logger.exception("FEC election-date calendar read failed")

    results: dict[str, dict] = {}
    for state in sorted(configured_states()):
        source = source_for_state(state)
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
        if general and STRATEGIES.get(general.get("strategy")):
            try:
                general_records = await STRATEGIES[general["strategy"]](client, cycle, state, general)
            except Exception:
                logger.exception("Certified general list fetch raised for %s", state)
                general_records = None

        try:
            records = await strategy(client, cycle, state, source)
        except Exception:
            logger.exception("Confirmed-candidate fetch raised for %s", state)
            records = None

        fallback = source.get("fallback")
        if records is None and fallback and STRATEGIES.get(fallback.get("strategy")):
            # A state's own second choice, named in its entry — Wisconsin's
            # canvass file name changes between cycles, and until the new
            # one is known its national fallback still says something.
            logger.info("Falling back to %s for %s", fallback["strategy"], state)
            try:
                records = await STRATEGIES[fallback["strategy"]](client, cycle, state, fallback)
            except Exception:
                logger.exception("Fallback fetch raised for %s", state)
                records = None
            if records is not None:
                source = fallback
        if records is None:
            # A hand-verified source that has broken falls back to whatever
            # the crawler last proved for this state, rather than the state
            # going dark until someone edits a URL.
            spare = _discovered_source(state)
            if spare and spare != source:
                logger.info("Falling back to the discovered source for %s", state)
                records = await STRATEGIES.get(spare.get("strategy"), _no_strategy)(
                    client, cycle, state, spare,
                )
        if records is None and general_records is None:
            results[state] = {"confirmed": 0, "unmatched": 0, "status": "fetch_failed"}
            continue
        records = records or []

        # Neither a statewide executive office (Governor, AG, ...) nor a
        # seat in the state legislature has an FEC race to confirm
        # against, so each takes an entirely different path: the state's
        # own feed is the record, stored as-is. Split first rather than
        # branching inside the federal loop, which otherwise counts every
        # one of them as `unmatched` against a Race id that cannot exist.
        statewide = [r for r in records if r["office"] in STATEWIDE_OFFICE_LABELS]
        state_leg = [r for r in records if r["office"] in STATE_LEG_CHAMBER_LABELS]
        judicial = [r for r in records if r["office"] in JUDICIAL_COURT_LABELS]
        records = [
            r for r in records
            if r["office"] not in STATEWIDE_OFFICE_LABELS
            and r["office"] not in STATE_LEG_CHAMBER_LABELS
            and r["office"] not in JUDICIAL_COURT_LABELS
        ]
        statewide_count = _sync_statewide_nominees(db, cycle, state, source, statewide)
        state_leg_count = _sync_state_leg_nominees(db, cycle, state, source, state_leg)
        judicial_count = _sync_judicial_nominees(db, cycle, state, source, judicial)

        # A state with its own general FILING list gets its November ballot
        # from that list (sync_ballot_filings), which is what may speak for
        # candidates this results file cannot see or has gone stale on.
        # Here, it only confirms who the results name.
        if general_records is not None:
            # The certified ballot answered: it alone decides the federal
            # races. Primary results above still supplied the state
            # offices, which the list may not cover.
            general_federal = [r for r in general_records if r["office"] in ("S", "H")]
            applied = _apply_ballot(
                db, cycle, state, general_federal, keep_unlisted=True, authoritative=True,
            )
            _record_ballot_basis(db, cycle, state, {**general, "general_ballot_complete": True})
        else:
            ballot_is_elsewhere = _has_general_filings(source)
            applied = _apply_ballot(
                db, cycle, state, records,
                keep_unlisted=not ballot_is_elsewhere,
                authoritative=bool(source.get("general_ballot_complete")) and not ballot_is_elsewhere,
            )
            if not ballot_is_elsewhere:
                _record_ballot_basis(db, cycle, state, source)
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
    for state in sorted(states_with_filings()):
        source = source_for_state(state) or {}
        try:
            found = await fetch_ballot_candidates(client, cycle, state, source)
        except Exception:
            logger.exception("Ballot-filing fetch raised for %s", state)
            found = None
        if found is None:
            results[state] = {"primary": 0, "general": 0, "unmatched": 0,
                              "status": "fetch_failed"}
            continue

        if found["primary_date"]:
            election_dates.save(state, cycle, {"primary": found["primary_date"]})
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
            counts["primary"] += 1
        applied = {"ballotOnly": 0, "unconfirmed": 0}
        if found["general"]:
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
    return results
