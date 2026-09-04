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

import httpx
from sqlalchemy.orm import Session

from app.models import Candidate, Race
from app.time_utils import utcnow
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
from app.pipeline.fetch.state_candidates_al import fetch_confirmed_candidates as _fetch_al
from app.pipeline.fetch.state_candidates_ar import fetch_confirmed_candidates as _fetch_ar
from app.pipeline.fetch.state_candidates_canvass_xml import fetch_confirmed_candidates as _fetch_canvass_xml
from app.pipeline.fetch.state_candidates_clarity import fetch_confirmed_candidates as _fetch_clarity
from app.pipeline.fetch.state_candidates_in import fetch_confirmed_candidates as _fetch_in
from app.pipeline.fetch.state_candidates_ky import fetch_confirmed_candidates as _fetch_ky
from app.pipeline.fetch.state_candidates_ms import fetch_confirmed_candidates as _fetch_ms
from app.pipeline.fetch.state_candidates_nj import fetch_confirmed_candidates as _fetch_nj
from app.pipeline.fetch.state_candidates_tabular import fetch_confirmed_candidates as _fetch_tabular
from app.pipeline.fetch.state_candidates_pa import fetch_confirmed_candidates as _fetch_pa
from app.pipeline.fetch.state_candidates_tn import fetch_confirmed_candidates as _fetch_tn
from app.pipeline.fetch.state_candidates_tx import fetch_confirmed_candidates as _fetch_tx

logger = logging.getLogger(__name__)

# Every strategy takes the SAME (client, cycle, state, source) arguments so
# one adapter can serve many states — a state whose vendor is already here
# is a state_candidate_sources.json entry, never new code. Only a genuinely
# different vendor earns a new module.
STRATEGIES = {
    "al_special_primary": _fetch_al,
    "ar_enr": _fetch_ar,
    "tx_civix": _fetch_tx,
    "pa_returns": _fetch_pa,
    "clarity": _fetch_clarity,
    "tabular": _fetch_tabular,
    "canvass_xml": _fetch_canvass_xml,
    "nj_certification": _fetch_nj,
    "ky_certification": _fetch_ky,
    "ms_recap": _fetch_ms,
    "in_enr": _fetch_in,
    "tn_precinct": _fetch_tn,
}

# A state's own party lettering (mostly single-letter) doesn't match FEC's
# 3-letter codes — used only as a tiebreaker among same-surname candidates
# in one race, never as the primary match signal (surname already scopes
# tightly within one race's small candidate list).
_PARTY_CODE_MAP = {
    "R": "REP", "D": "DEM", "L": "LIB", "G": "GRE", "I": "IND", "C": "CON",
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


def _candidate_surname(name: str) -> str:
    """FEC's Candidate.name is "LAST, FIRST MIDDLE ..." — the surname is
    everything before the comma (same extraction elections.py's
    _incumbent_link does)."""
    return name.split(",")[0].strip().lower()


def _match_candidate(
    candidates: list[Candidate], last_name: str, party_code: str,
) -> Candidate | None:
    target = last_name.strip().lower()
    matches = [c for c in candidates if _candidate_surname(c.name) == target]
    if not matches:
        # A MULTI-WORD surname survives on the FEC side ("WASSERMAN
        # SCHULTZ, DEBBIE") but not on the state's, because a state
        # publishes a display name and the trailing token is all that can
        # be taken from "Debbie Wasserman Schultz" without guessing where
        # the surname begins. Falling back to the FEC surname's own last
        # token matches them up. Deliberately only a fallback, and still
        # inside one race's small candidate list, so the
        # never-guess-between-two rule below is what decides anything
        # ambiguous. Affects every state, not just the one that surfaced
        # it: Florida's 2024 file is where it showed up, but a Wasserman
        # Schultz, a Van Drew or a De La Cruz would have gone unmatched
        # anywhere.
        matches = [
            c for c in candidates
            if _candidate_surname(c.name).split()[-1:] == [target]
        ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        expected_party = _PARTY_CODE_MAP.get(party_code)
        party_matches = [c for c in matches if c.party == expected_party]
        if len(party_matches) == 1:
            return party_matches[0]
    return None


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
                continue
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
    return _match_candidate(race.candidates, record["last_name"], record["party"])


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
        for state, dates in (await election_dates.fetch_fec_calendar(client, cycle)).items():
            election_dates.save(state, cycle, dates)
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

        try:
            records = await strategy(client, cycle, state, source)
        except Exception:
            logger.exception("Confirmed-candidate fetch raised for %s", state)
            records = None

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
        if records is None:
            results[state] = {"confirmed": 0, "unmatched": 0, "status": "fetch_failed"}
            continue

        confirmed = unmatched = 0
        for record in records:
            race_id = _race_id_for(cycle, state, record["office"], record["district"])
            race = db.query(Race).filter(Race.id == race_id).first()
            if race is None:
                unmatched += 1
                continue
            match = _match_candidate(race.candidates, record["last_name"], record["party"])
            if match is None:
                unmatched += 1
                logger.info(
                    "No FEC match for confirmed %s candidate %s (%s) in %s",
                    state, record["last_name"], record["party"], race_id,
                )
                continue
            if not match.confirmed_general:
                match.confirmed_general = True
                db.commit()
            confirmed += 1

        results[state] = {"confirmed": confirmed, "unmatched": unmatched, "status": "ok"}

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
        for kind, flag in (("primary", "on_primary_ballot"),
                           ("general", "confirmed_general")):
            for record in found[kind]:
                match = _confirmed_match(db, cycle, state, record)
                if match is None:
                    unmatched += 1
                    continue
                if not getattr(match, flag):
                    setattr(match, flag, True)
                    db.commit()
                counts[kind] += 1
        results[state] = {
            **counts, "unmatched": unmatched,
            "primary_date": found["primary_date"], "status": "ok",
        }
    return results
