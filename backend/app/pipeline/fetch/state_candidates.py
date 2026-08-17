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
from app.pipeline.fetch.state_candidate_sources import configured_states, source_for_state
from app.pipeline.fetch.state_candidates_clarity import fetch_confirmed_candidates as _fetch_clarity
from app.pipeline.fetch.state_candidates_tabular import fetch_confirmed_candidates as _fetch_tabular
from app.pipeline.fetch.state_candidates_tx import fetch_confirmed_candidates as _fetch_tx

logger = logging.getLogger(__name__)

# Every strategy takes the SAME (client, cycle, state, source) arguments so
# one adapter can serve many states — a state whose vendor is already here
# is a state_candidate_sources.json entry, never new code. Only a genuinely
# different vendor earns a new module.
STRATEGIES = {
    "tx_civix": _fetch_tx,
    "clarity": _fetch_clarity,
    "tabular": _fetch_tabular,
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


async def sync_confirmed_candidates(db: Session, client: httpx.AsyncClient, cycle: int) -> dict:
    """Confirm every registered state's general-election candidates
    against this cycle's Race/Candidate rows. Returns per-state counts —
    `confirmed` (candidates newly or already flagged), `unmatched`
    (records that couldn't be safely matched to one FEC candidate), and
    `status` (`ok` / `fetch_failed` / `not_configured`)."""
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
