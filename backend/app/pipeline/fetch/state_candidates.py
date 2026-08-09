"""Generic confirmed-general-election-candidate sync: fetch, match, and
flag existing FEC-derived Candidate rows for ANY state with a registered
source (state_candidate_sources.json) — no API key beyond what each
state's own strategy needs, no guessing.

Deliberately NOT one parser that guesses an arbitrary state's candidate-
data shape. Every state's Secretary of State (or equivalent) publishes its
own system — Texas's is a REST JSON API (see state_candidates_tx.py);
other states may need a completely different strategy, or none yet. So
each state gets its own small, hand-verified fetch function, built against
that state's real, currently-fetched data — but the matching/flagging code
around it (this module) is shared, same STRATEGIES-dispatch shape as
ballot_measures_pdf.py.

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
from app.pipeline.fetch.state_candidates_tx import fetch_confirmed_candidates as _fetch_tx

logger = logging.getLogger(__name__)

STRATEGIES = {
    "tx_civix": _fetch_tx,
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
    race. No state currently registered here (just TX) has a special
    election on this cycle's federal ballot — a future state that does
    would need this taught the "-SPECIAL" suffix too."""
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
            records = await strategy(client, cycle)
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
