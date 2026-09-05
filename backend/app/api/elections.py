"""Midterm-elections API — candidate rosters, race detail, and PVI
(2026-07). Plain camelCase dicts, same convention as api/action.py's
existing /action/elections endpoint (that endpoint is unchanged; this is
a separate, fuller namespace for the new candidate-research feature)."""

import json
import logging
import pathlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.api.public import RateLimit
from app.api.response_helpers import CACHE_TTL_DETAIL_S, CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.election_calendar import (
    CLASS_I_STATES,
    CLASS_II_STATES,
    CLASS_III_STATES,
    next_election_day,
    next_senate_election_year,
    seats_up_for_year,
)
from app.http_client import make_async_client
from app.models import (
    BallotMeasure,
    Candidate,
    MeasureCoverage,
    Race,
    RaceCoverageItem,
    Representative,
    Senator,
)
from app.pipeline.analyze.score_calculator import (
    compute_overall_score,
    get_district_pvi_map,
    get_pvi_meta,
    get_state_pvi_map,
)
from app.pipeline.election_pipeline import current_election_cycle
from app.pipeline.fetch import ballot_pdf
from app.pipeline.fetch.ballot_lookup import lookup_for_state
from app.pipeline.fetch.ballot_pdf_sources import source_for_town as ballot_pdf_source_for_town
from app.pipeline.fetch.ballot_pdf_sources import town_names_for_state as ballot_pdf_town_names_for_state
from app.pipeline.fetch.civic_info import fetch_town_ballot
from app.pipeline.fetch.civic_info import is_configured as civic_is_configured
from app.pipeline.fetch.state_candidate_sources import source_for_state
from app.pipeline.fetch.state_election_dates import primary_date
from app.pipeline.fetch.town_directory import address_for_town, towns_for_state
from app.time_utils import utcnow

# The 50 states, from the same class sets election_pipeline.py derives its
# roster filter from — one source for "which jurisdictions hold federal
# elections", not a second hand-typed list that can drift from it.
STATE_CODES = CLASS_I_STATES | CLASS_II_STATES | CLASS_III_STATES

# DC is a valid BALLOT jurisdiction even though it has no voting member of
# Congress and is deliberately absent from the candidate roster (see
# election_pipeline.STATES_WITH_FEDERAL_RACES). It votes on statewide
# initiatives, and — decisively — the frontend's own map renders DC as a
# clickable, keyboard-focusable region, so refusing it here would 404 a
# link the site itself hands the user. The territories are not included:
# the map doesn't render them, so nothing links there.
BALLOT_STATE_CODES = STATE_CODES | {"DC"}

logger = logging.getLogger(__name__)

_COUNTY_CROSSWALK_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "data" / "county_district_crosswalk.json"
)
_district_counties_cache: dict[str, list[str]] | None = None


def _district_counties() -> dict[str, list[str]]:
    """"ST-N" -> sorted county names (a "(part)" suffix means that county
    also has population in another district) — lets a voter who doesn't
    know their district number recognize their county instead. District
    boundaries only change after redistricting (once a decade), so this
    is a static bundled file, not an auto-refreshed one like
    district_pvi.json. Empty dict (never a guess) if the file is
    missing."""
    global _district_counties_cache
    if _district_counties_cache is None:
        try:
            data = json.loads(_COUNTY_CROSSWALK_PATH.read_text())
            _district_counties_cache = data["districts"]
        except Exception:
            logger.exception("county_district_crosswalk.json unavailable")
            _district_counties_cache = {}
    return _district_counties_cache

router = APIRouter(prefix="/elections")


def _pvi_for_race(race: Race, state_pvi: dict, district_pvi: dict) -> tuple[int | None, str | None]:
    """(pvi, level) where level says which map the number came from —
    "district" or "state". A House race falling back to the statewide
    number is a materially different claim (a D+19 urban district in a
    red state is nothing like its state's lean), so the fallback is
    FLAGGED for the frontend to label rather than silently blended
    (2026-07 review F7)."""
    if race.office == "H":
        key = f"{race.state}-{race.district if race.district is not None else 0}"
        if key in district_pvi:
            return district_pvi[key], "district"
    pvi = state_pvi.get(race.state)
    return pvi, ("state" if pvi is not None else None)


def _iso_utc(dt) -> str | None:
    """Serialize a stored naive-UTC datetime with an explicit Z suffix —
    an offset-less ISO string gets parsed as LOCAL time by JS Date
    (2026-07 review: coverage timestamps displayed shifted by the
    viewer's UTC offset). Same reasoning as main.py's PROCESS_STARTED_AT
    keeping its explicit +00:00 in an exposed field."""
    if dt is None:
        return None
    iso = dt.isoformat()
    return iso if ("+" in iso or iso.endswith("Z")) else iso + "Z"


def _coverage_item(item: RaceCoverageItem) -> dict:
    return {
        "id": item.id,
        "sourceType": item.source_type,
        "sourceName": item.source_name,
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "author": item.author,
        "publishedAt": _iso_utc(item.published_at),
    }


def _candidate_summary(cand: Candidate) -> dict:
    return {
        "id": cand.id,
        "name": cand.name,
        "party": cand.party,
        "incumbentChallenge": cand.incumbent_challenge,
        "hasRaisedFunds": cand.has_raised_funds,
        "candidateStatus": cand.candidate_status,
        "contributions": cand.contributions,
        "cashOnHand": cand.cash_on_hand,
        # Null = never synced. The frontend renders "awaiting FEC sync"
        # for null vs. real figures with an as-of date — a candidate whose
        # refresh turn hasn't come up must not read as "$0 raised"
        # (2026-07 review F10).
        "lastFinancialsSync": _iso_utc(cand.last_financials_sync),
    }


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalized_surname(name: str) -> str:
    """FEC's Candidate.name is "LAST, FIRST MIDDLE ..." -- the surname is
    everything before the comma, with a trailing generational suffix
    stripped, since FEC inconsistently attaches JR/SR/II/III to either
    half of the name (see _dedupe_candidates)."""
    tokens = name.split(",")[0].strip().lower().split()
    while tokens and tokens[-1].strip(".") in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Collapse two FEC candidate_ids that are the same real person under
    one race. A real, observed FEC artifact -- a candidate refiles (a name
    correction, a party-declaration change) and is assigned a NEW
    candidate_id, but FEC's own bulk data links the same committee's
    financial totals to both. Verified live across 22 real 2026 races:
    21 of 22 pairs share a name (exact or an obvious variant, e.g. "ONDER
    JR, ROBERT FRANK" / "ONDER, ROBERT FOR JR."); the one exception
    (CA-4's "BROWN, SHARON" / "GHUSAR, MANDY", both $7,000 raised / $0
    cash) proves identical financials ALONE is not safe evidence -- it
    takes a matching surname AND identical financials together, never
    either alone.

    Deliberately conservative: contributions and cash_on_hand must both
    be non-null and at least one non-zero (a shared "never synced"/$0
    pair is common and proves nothing). Unlike state_candidates.py's
    _match_candidate, this never falls back to a looser rule on a miss --
    the cost of NOT merging is one duplicate row; the cost of a wrong
    merge is misattributing a real candidate's identity, which this
    system treats as the worse failure everywhere else. Never touches the
    DB: both FEC ids are real, independently-filed records worth keeping
    for anyone who clicks through to fec.gov on either one -- this only
    shapes which rows a response includes, the same "never delete source
    data" precedent confirmed_general/on_primary_ballot already set.
    """
    by_fingerprint: dict[tuple[float, float], list[Candidate]] = {}
    for c in candidates:
        if c.contributions is None or c.cash_on_hand is None:
            continue
        if c.contributions == 0 and c.cash_on_hand == 0:
            continue
        by_fingerprint.setdefault((c.contributions, c.cash_on_hand), []).append(c)

    drop_ids: set[str] = set()
    for group in by_fingerprint.values():
        if len(group) < 2:
            continue
        by_surname: dict[str, list[Candidate]] = {}
        for c in group:
            by_surname.setdefault(_normalized_surname(c.name), []).append(c)
        for dupes in by_surname.values():
            if len(dupes) < 2:
                continue
            confirmed = [c for c in dupes if c.confirmed_general or c.on_primary_ballot]
            keep = confirmed[0] if len(confirmed) == 1 else sorted(dupes, key=lambda c: c.id)[0]
            drop_ids.update(c.id for c in dupes if c.id != keep.id)

    return [c for c in candidates if c.id not in drop_ids]


def _confirmed_or_all(candidates: list[Candidate]) -> list[Candidate]:
    """If a registered state source (state_candidate_sources.json /
    state_candidates.py) has confirmed any candidate in this race as an
    actual general-election nominee, return ONLY confirmed candidates — an
    FEC filer who lost their primary/runoff isn't a real ballot option
    (2026-08 fix: TX's Senate race listed 19 FEC filers as if all still
    running, months after the real primary/runoff resolved). A race with
    no confirmed data at all (not yet covered, or genuinely pre-primary)
    returns every active FEC filer, unchanged from before this existed.

    Failing that, a race whose state publishes a candidate FILING list
    falls back to whoever is actually on that state's PRIMARY ballot,
    which is the best answer available for the months before a primary
    happens — an FEC filer who never filed with the state is not a ballot
    option either. Deliberately the weaker rule and only reached when no
    nominee is confirmed: being on a primary ballot says nothing about
    surviving it.

    Shared by every endpoint that lists a race's candidates
    (_race_summary, _race_full, race_detail) — the bug this guards
    against previously resurfaced via race_detail even after _race_full
    was fixed, since a race's full candidate list is reachable from more
    than one route. Also the one place _dedupe_candidates runs, so every
    one of those endpoints gets it for free."""
    candidates = _dedupe_candidates(candidates)
    if any(c.confirmed_general for c in candidates):
        return [c for c in candidates if c.confirmed_general]
    if any(c.on_primary_ballot for c in candidates):
        return [c for c in candidates if c.on_primary_ballot]
    return candidates


def _candidate_source(candidates: list[Candidate], state: str) -> str:
    """WHICH of _confirmed_or_all's three answers a race's list is, so the
    page can say so instead of presenting three quite different things as
    one list. Computed here rather than in the frontend, which must not
    re-derive what the filter already decided.

    "confirmed"  — the state has named its whole November ballot, minor
                   parties included.
    "nominees"   — the state has confirmed nominees, but only from PRIMARY
                   results, which structurally cannot see a Libertarian,
                   Green or independent candidate who never ran in one. The
                   list is real and incomplete, and saying so is the
                   difference between a short ballot and a wrong one.
    "primary"    — no nominee yet, but the state lists these as being on
                   its primary ballot.
    "filers"     — nobody has confirmed anything for this race, so this is
                   every active FEC filer, some of whom may never appear on
                   a ballot.
    """
    if any(c.confirmed_general for c in candidates):
        source = source_for_state(state) or {}
        return "confirmed" if source.get("general_ballot_complete") else "nominees"
    if any(c.on_primary_ballot for c in candidates):
        return "primary"
    return "filers"


def _race_summary(race: Race, state_pvi: dict, district_pvi: dict) -> dict:
    candidates = sorted(
        _confirmed_or_all(race.candidates),
        key=lambda c: (c.cash_on_hand or 0.0),
        reverse=True,
    )
    top_candidates = candidates[:2]
    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    return {
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": pvi,
        "pviLevel": pvi_level,
        "candidateCount": len(candidates),
        "topCandidates": [_candidate_summary(c) for c in top_candidates],
    }


def _last_name_matches(last_name: str, full_name: str) -> bool:
    """True if `last_name` (FEC's — possibly multi-word, e.g. "van
    hollen") exactly matches the TRAILING tokens of `full_name`.
    Deliberately token-exact rather than a raw substring check: a
    substring match would let "lee" match "leeman" by coincidence,
    which is exactly the kind of wrong-person attribution
    _incumbent_link's docstring warns against. Token-trailing (not
    single-last-token) so multi-word surnames like "Van Hollen" still
    match against a full name of "Chris Van Hollen"."""
    cand_tokens = last_name.split()
    name_tokens = full_name.lower().split()
    return bool(cand_tokens) and name_tokens[-len(cand_tokens):] == cand_tokens


def _incumbent_link(
    cand: Candidate, race: Race, reps_by_district: dict[int, Representative], senators: list[Senator],
) -> dict | None:
    """{id, score} for this candidate's matching Senator/Representative
    scorecard row, or None — only ever populated for a real, uniquely-
    identified match; never a guess (a wrong match here would attribute
    one member's voting record to a different person on the ballot).

    House matches on the exact (state, district) key via `reps_by_district`
    — no ambiguity possible, since a district has exactly one
    representative. Senate has no seat-class field to key on (Senator only
    stores `state`, and a state has two), so it matches on state + last
    name against `senators` (pre-filtered to this race's state), checked
    UNIQUE before trusting it — the only real disambiguator available
    given the schema, and safe because two senators from the same state
    sharing a last name is not a real scenario this needs to handle
    "close enough". Both lookups are precomputed once per state_ballot
    call (not queried per-candidate here) — see that function.
    """
    if cand.incumbent_challenge != "I":
        return None
    # FEC's name field is "LAST, FIRST MIDDLE ..." (see fec.py's
    # _fec_first_name) — the last name is everything before the comma.
    last_name = cand.name.split(",")[0].strip().lower()
    if not last_name:
        return None

    if race.office == "H":
        rep = reps_by_district.get(race.district or 0)
        if rep and _last_name_matches(last_name, rep.name):
            return {"id": rep.id, "score": compute_overall_score(rep)}
        return None

    if race.office == "S":
        matches = [s for s in senators if _last_name_matches(last_name, s.name)]
        if len(matches) == 1:
            return {"id": matches[0].id, "score": compute_overall_score(matches[0])}
    return None


def _race_full(
    race: Race, state_pvi: dict, district_pvi: dict,
    reps_by_district: dict[int, Representative], senators: list[Senator],
) -> dict:
    """Same shape as race_detail's response, minus coverage — this backs
    the per-state ballot view, which needs every candidate (not just the
    top-2-by-funds _race_summary uses for the map/directory) but not the
    news feed, which stays one click away on the existing race-detail
    page. Confirmed-general filtering (see _confirmed_or_all) applies
    here too, same as race_detail."""
    candidates = sorted(_confirmed_or_all(race.candidates), key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    counties = None
    if race.office == "H":
        key = f"{race.state}-{race.district if race.district is not None else 0}"
        counties = _district_counties().get(key)
    return {
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": pvi,
        "pviLevel": pvi_level,
        "counties": counties,
        "candidateSource": _candidate_source(race.candidates, race.state),
        "candidates": [
            {**_candidate_summary(c), "incumbentRecord": _incumbent_link(c, race, reps_by_district, senators)}
            for c in candidates
        ],
    }


# How many state-wide coverage items the ballot page's top-of-page teaser
# shows. STATE_COVERAGE_QUERY_LIMIT is deliberately larger than this: a
# state with many races can have the same story matched under more than
# one race (see _state_coverage's dedup), so the raw query needs headroom
# above the post-dedup count actually shown.
STATE_COVERAGE_LIMIT = 20
STATE_COVERAGE_QUERY_LIMIT = 100


def _state_coverage(db: Session, races: list[Race]) -> list[dict]:
    """Every race's coverage for this state, newest first, deduplicated
    by URL and capped to STATE_COVERAGE_LIMIT — backs the ballot page's
    top-of-page coverage feed (front and center, not one click away on a
    per-race page, per 2026-08 review). A single story can match
    candidates in two different races within the same state (e.g. an
    article about both the Senate race and a House race) — the
    RaceCoverageItem table's uniqueness is per (race_id, url), so the
    same url can legitimately appear as separate rows across races;
    deduped here so the reader doesn't see one headline twice. Each item
    carries a `race` sub-object (id/office/district) so the frontend can
    label which race it's about via lib/elections.ts's raceBadgeLabel()
    — not a second copy of that formatting logic.

    The two (or more) rows for a deduplicated story share the same
    published_at/fetched_at (same article, ingested in the same pass),
    so the `.id` tiebreaker decides — deterministically, not whichever
    row SQL happens to return first for a tied sort key — which race's
    badge is shown for it."""
    races_by_id = {r.id: r for r in races}
    if not races_by_id:
        return []
    rows = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.race_id.in_(races_by_id.keys()))
        .order_by(
            RaceCoverageItem.published_at.desc().nullslast(),
            RaceCoverageItem.fetched_at.desc(),
            RaceCoverageItem.id.asc(),
        )
        .limit(STATE_COVERAGE_QUERY_LIMIT)
        .all()
    )
    seen_urls: set[str] = set()
    coverage = []
    for item in rows:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        race = races_by_id[item.race_id]
        coverage.append({
            **_coverage_item(item),
            "race": {"id": race.id, "office": race.office, "district": race.district},
        })
        if len(coverage) >= STATE_COVERAGE_LIMIT:
            break
    return coverage


@router.get("/states/{state}")
def state_ballot(state: str, db: Session = Depends(get_db)):
    """Every federal (Senate + House) race on `state`'s ballot this cycle
    with full candidate lists, plus its statewide ballot measures — backs
    the ballot-centric per-state page.

    Scope is deliberately narrow and named in the payload. A ballot is
    defined per ballot style, not per state (precinct splits mean one
    county can print dozens), so U.S. House district contests, state
    legislative districts, county/municipal offices, judicial questions
    and local measures cannot be shown on a state page without misstating
    somebody's ballot. `omits` carries that list so the frontend renders
    the limitation as content rather than a footnote, and
    `officialLookup` is the route to the rest.

    `measureCoverage` is the field that keeps an empty `measures` list
    honest: "confirmed_none" (the source says this state has none) and
    "not_yet_covered" / "ingest_failed" (we don't know) are different
    claims, and rendering them identically would tell a voter in a state
    with 17 amendments that there is nothing to research.

    DC is a valid ballot jurisdiction despite having no voting member of
    Congress and being absent from STATES_WITH_FEDERAL_RACES — it votes on
    statewide initiatives, and the frontend's own map renders it as a
    clickable region.
    """
    state = state.upper()
    if state not in BALLOT_STATE_CODES:
        raise HTTPException(status_code=404, detail="Unknown state")

    cycle = current_election_cycle()
    election_day = next_election_day(utcnow().date()).isoformat()

    races = (
        db.query(Race)
        .filter(Race.state == state, Race.cycle_year == cycle)
        .options(selectinload(Race.candidates))
        .all()
    )
    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    # Fetched once per request, not once per incumbent candidate — a
    # state can have up to ~50 House races, and querying Representative/
    # Senator inside _incumbent_link per candidate would be exactly the
    # N+1 shape the .candidates selectinload above already exists to
    # avoid for a different relationship.
    reps_by_district = {
        r.district: r for r in db.query(Representative).filter(Representative.state == state).all()
    }
    senators = db.query(Senator).filter(Senator.state == state, Senator.is_current).all()
    full = [_race_full(r, state_pvi, district_pvi, reps_by_district, senators) for r in races]
    senate_races = [r for r in full if r["office"] == "S"]
    house_races = sorted(
        (r for r in full if r["office"] == "H"),
        key=lambda r: r["district"] if r["district"] is not None else -1,
    )

    measures = (
        db.query(BallotMeasure)
        .filter(BallotMeasure.state == state)
        .order_by(BallotMeasure.election_date, BallotMeasure.number)
        .all()
    )
    coverage = (
        db.query(MeasureCoverage)
        .filter(MeasureCoverage.state == state, MeasureCoverage.election_date == election_day)
        .first()
    )

    return cached_json({
        "state": state,
        "cycleYear": cycle,
        # The federal general is the only date derivable from statute
        # (2 U.S.C. §7). Primaries are party-specific and set by each
        # state on ~50 different dates, so they are NOT covered here and
        # `omits` says so — rather than this page quietly implying that
        # the November ballot is the next one a visitor will see.
        "electionDate": election_day,
        "electionType": "general",
        # Read from this state's own election feed (state_election_dates.py),
        # never a calendar maintained here — null for a state whose source
        # doesn't date itself, which is the honest answer.
        "primaryDate": primary_date(state, cycle),
        "statePvi": state_pvi.get(state),
        "senateRaces": senate_races,
        # Only meaningful (and only computed) when this state's seat
        # genuinely ISN'T up this cycle — gated on the calendar
        # (seats_up_for_year), not merely on senate_races being empty.
        # Those are different claims: a state whose class IS up this
        # cycle but whose Race row simply hasn't synced yet (a real,
        # previously-seen pipeline-lag failure mode) would otherwise get
        # a confidently wrong "next election isn't until [later year]"
        # here — the same confirmed_none-vs-not_yet_covered distinction
        # measureCoverage below exists to preserve, just for Senate.
        # Null once senateRaces is non-empty (nothing to explain), and
        # null for a jurisdiction with no Senate seats at all (DC).
        "nextSenateElection": (
            next_senate_election_year(state, cycle)
            if not senate_races and state not in seats_up_for_year(cycle)
            else None
        ),
        "houseRaces": house_races,
        "coverage": _state_coverage(db, races),
        "measures": [_measure_json(m) for m in measures],
        "measureCoverage": {
            "status": coverage.status if coverage else MeasureCoverage.NOT_YET_COVERED,
            "sourceName": coverage.source_name if coverage else None,
            "checkedAt": _iso_utc(coverage.checked_at) if coverage else None,
        },
        "officialLookup": lookup_for_state(state),
        "omits": [
            "Governor and other statewide executive contests",
            "State legislative districts",
            "Judicial contests and retention questions",
            "County and municipal offices",
            "Local ballot measures",
            "Primary and runoff ballots",
        ] + ([
            # DC elects a Delegate with no floor vote, so it has no race in
            # the FEC-derived roster at all. Saying that is better than a
            # page that just looks empty.
            "DC's Delegate to the House (non-voting) is not covered here",
        ] if state == "DC" else []),
    }, max_age=CACHE_TTL_LIST_S)


@router.get("/races")
def list_races(db: Session = Depends(get_db)):
    """All races for the current cycle, with PVI and top-2-by-funds
    candidates — backs the map + directory."""
    races = (
        db.query(Race)
        # Filter matches the docstring's contract — harmless while only
        # one cycle exists, load-bearing the day a second cycle syncs.
        .filter(Race.cycle_year == current_election_cycle())
        # ~470 races each lazy-loading .candidates is an N+1 of ~500
        # queries per request on a Pi — batch them.
        .options(selectinload(Race.candidates))
        .all()
    )
    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    data = [_race_summary(r, state_pvi, district_pvi) for r in races]
    return cached_json(data, max_age=CACHE_TTL_LIST_S)


@router.get("/pvi")
def pvi_map():
    """State + district PVI maps (positive = R lean, negative = D lean) —
    already computed for internal scoring (score_calculator.py), exposed
    publicly here with their provenance metadata (source, method, election
    window, as-of date) so the frontend can label what the number is and
    is not (2026-07 review F7)."""
    return cached_json(
        {
            "states": get_state_pvi_map(),
            "districts": get_district_pvi_map(),
            "meta": get_pvi_meta(),
            # Lets the /elections directory page show "{cycleYear} MIDTERM
            # ELECTIONS" from the same fetch it already makes for map
            # coloring, instead of a second fetch of every race just to
            # read one field off the first result.
            "cycleYear": current_election_cycle(),
        },
        max_age=CACHE_TTL_LIST_S,
    )


def _measure_json(measure) -> dict:
    """One measure, with everything needed to read it honestly.

    Every text field here is verbatim from `sourceName` — nothing on this
    endpoint is model-generated (see BallotMeasure's docstring). `yesMeans`
    /`noMeans` are null whenever the source publishes no such framing,
    rather than inferred: the intuitive inference is inverted on a veto
    referendum, where approving RETAINS the law under challenge.
    """
    return {
        "id": measure.id,
        "state": measure.state,
        "electionDate": measure.election_date,
        "electionType": measure.election_type,
        "number": measure.number,
        "title": measure.title,
        "measureType": measure.measure_type,
        "origin": measure.origin,
        "status": measure.status,
        "officialTitle": measure.official_title,
        "officialSummary": measure.official_summary,
        "fiscalImpact": measure.fiscal_impact,
        "yesMeans": measure.yes_means,
        "noMeans": measure.no_means,
        "titleAuthority": measure.title_authority,
        "fiscalAuthority": measure.fiscal_authority,
        "sourceName": measure.source_name,
        "sourceUrl": measure.source_url,
        "asOf": _iso_utc(measure.as_of),
    }


@router.get("/states/{state}/towns")
def state_towns(state: str):
    """The curated town list for `state` — the union of two independent
    sources, since a town needs covering by exactly one of them, never
    both:

    - Towns with a hand-verified official ballot PDF (ballot_pdf.py) —
      always offered, no API key needed.
    - Towns on the Google Civic representative-address path
      (civic_info.py) — offered only when GOOGLE_CIVIC_API_KEY is set.

    Never an error: an empty list is exactly how the frontend knows not
    to offer the town selector, same as MeasureCoverage.NOT_YET_COVERED
    for statewide measures.

    No `db` dependency: both directories read a static bundled/volume
    JSON file, never the database — unlike town_ballot below, which
    needs `db` for the response caches."""
    state = state.upper()
    if state not in BALLOT_STATE_CODES:
        raise HTTPException(status_code=404, detail="Unknown state")

    civic_towns = towns_for_state(state) if civic_is_configured() else []
    civic_names = {t["name"].casefold() for t in civic_towns}

    pdf_towns = [
        {"name": name, "sourceName": (ballot_pdf_source_for_town(name) or {}).get("source_name") or ""}
        for name in ballot_pdf_town_names_for_state(state)
        if name.casefold() not in civic_names
    ]

    return cached_json(
        {"towns": pdf_towns + civic_towns}, max_age=CACHE_TTL_DETAIL_S,
    )


def _pdf_contest_json(c: dict) -> dict:
    """A ballot_pdf.py contest -> the same TownBallotItem shape the
    frontend already renders for Google Civic contests. PDF-sourced
    candidates carry no party/campaign-URL — the ballot itself doesn't
    print either — so those fields are null, never guessed."""
    return {
        "kind": "contest",
        "office": c["office"],
        "candidates": [
            {"name": cand["name"], "party": None, "candidateUrl": None}
            for cand in c["candidates"]
        ],
    }


def _uncovered_town_ballot(status: str) -> dict:
    """The not_yet_covered/ingest_failed shape, shared across both
    sources' failure paths so the null fields can't drift out of sync
    with the "covered" shape above."""
    return {
        "status": status, "address": None, "source": None, "sourceUrl": None,
        "electionName": None, "electionDate": None, "contests": [],
    }


@router.get("/states/{state}/towns/{town}/ballot")
async def town_ballot(state: str, town: str, db: Session = Depends(get_db)):
    """Contests and measures for `town`.

    Two sources, tried in order:

    1. A real, hand-verified official ballot PDF (ballot_pdf.py) — no API
       key, no representative-address approximation, the town's own
       published document. Only a handful of towns have one of these;
       most jurisdictions gate their sample ballot behind an address
       lookup with no static file to fetch at all (confirmed during
       research: Cambridge MA, Ann Arbor MI).
    2. Google Civic's voterInfoQuery against a fixed representative
       address (civic_info.py) — the fallback for every other curated
       town, an approximation rather than the town's own document.

    Live, on-demand either way — not a nightly pipeline phase like
    statewide measures, since town lookups can't be pre-fetched for every
    curated town at any meaningful scale ahead of a specific request.
    Bounded because the town list is small and curated (not user-typed
    free text), and each lookup is cached so repeat visits don't re-fetch.

    `status` mirrors MeasureCoverage's tri-state discipline: not_yet_
    covered when the town isn't in either directory (or Google Civic
    isn't configured and there's no PDF source either), ingest_failed on
    a fetch/parse error, covered on success — an empty contests list on
    success is real information ("nothing local at this address this
    cycle"), unlike a fetch failure.
    """
    state = state.upper()
    if state not in BALLOT_STATE_CODES:
        raise HTTPException(status_code=404, detail="Unknown state")

    async with make_async_client(timeout=30.0) as client:
        if ballot_pdf.is_configured(town):
            pdf_result = await ballot_pdf.fetch_town_ballot_pdf(client, db, town)
            if pdf_result is not None:
                source = ballot_pdf_source_for_town(town) or {}
                return cached_json({
                    "status": "covered",
                    "address": None,
                    "source": source.get("source_name") or "the town's official ballot",
                    "sourceUrl": pdf_result["sourceUrl"],
                    # Load-bearing, not decoration: this PDF is whichever
                    # election the town most recently published (right now,
                    # Somerville's Sept 2026 primary) — NOT necessarily the
                    # cycle's November general the rest of the page is
                    # titled for. Showing primary candidates under a page
                    # that says "GENERAL ELECTION" without saying so would
                    # be actively misleading, not just incomplete.
                    "electionName": source.get("election_name"),
                    "electionDate": source.get("election_date"),
                    "contests": [_pdf_contest_json(c) for c in pdf_result["contests"]],
                }, max_age=CACHE_TTL_DETAIL_S)
            # A configured PDF source that failed to fetch/parse is a real
            # ingest failure, not a reason to silently fall through to
            # the approximation below — that would quietly downgrade a
            # known-real source to a guess without saying so.
            return cached_json(_uncovered_town_ballot("ingest_failed"), max_age=CACHE_TTL_DETAIL_S)

        if not civic_is_configured() or address_for_town(state, town) is None:
            return cached_json(_uncovered_town_ballot("not_yet_covered"), max_age=CACHE_TTL_DETAIL_S)

        result = await fetch_town_ballot(client, db, state, town)

    if result is None:
        return cached_json(_uncovered_town_ballot("ingest_failed"), max_age=CACHE_TTL_DETAIL_S)
    return cached_json({
        "status": "covered",
        "address": result["address"],
        "source": "Google Civic Information API",
        "sourceUrl": None,
        "electionName": result["election_name"],
        "electionDate": result["election_date"],
        "contests": result["contests"],
    }, max_age=CACHE_TTL_DETAIL_S)


@router.get("/races/{race_id}")
def race_detail(race_id: str, db: Session = Depends(get_db)):
    """Full race detail: candidates (confirmed nominees only where known,
    see _confirmed_or_all), financials, coverage feed."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")

    state_pvi = get_state_pvi_map()
    district_pvi = get_district_pvi_map()
    candidates = sorted(_confirmed_or_all(race.candidates), key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
    coverage = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.race_id == race_id)
        .order_by(RaceCoverageItem.published_at.desc().nullslast(), RaceCoverageItem.fetched_at.desc())
        .limit(50)
        .all()
    )

    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    return cached_json({
        "id": race.id,
        "cycleYear": race.cycle_year,
        "office": race.office,
        "state": race.state,
        "district": race.district,
        "isSpecial": race.is_special,
        "pvi": pvi,
        "pviLevel": pvi_level,
        "candidateSource": _candidate_source(race.candidates, race.state),
        "candidates": [_candidate_summary(c) for c in candidates],
        "coverage": [_coverage_item(item) for item in coverage],
    }, max_age=CACHE_TTL_DETAIL_S)


@router.get("/candidates/{candidate_id}")
def candidate_detail(candidate_id: str, db: Session = Depends(get_db)):
    """Single candidate profile, with its parent race's identity."""
    cand = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if cand is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    race = cand.race
    return cached_json({
        **_candidate_summary(cand),
        "disbursements": cand.disbursements,
        "individualItemizedContributions": cand.individual_itemized_contributions,
        "race": {
            "id": race.id,
            "office": race.office,
            "state": race.state,
            "district": race.district,
        } if race else None,
    }, max_age=CACHE_TTL_DETAIL_S)


_CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


@router.get("/geocode")
async def geocode_address(address: str, _rl: RateLimit):
    """State + House district for a US mailing address, via the Census
    Bureau's free, no-key geocoder — lets the ballot page auto-select a
    visitor's district instead of requiring the manual dropdown (2026-08,
    address collection scoped explicitly: optional, resolve-only, never
    stored). The address is passed straight through to Census and never
    logged, cached, or persisted here — only the resolved state/district
    numbers are returned.

    Rate-limited (same RateLimit as qa.py's LLM-calling /ask) — this is
    the only /elections endpoint that makes a per-request outbound call
    to a third party rather than just querying the local DB, so it's the
    one route in this file an unauthenticated caller could otherwise use
    to hammer Census on Civitas's behalf for free.

    {"state": None, "district": None} for an address Census can't match
    or that doesn't resolve to a congressional district (e.g. outside the
    US) — never a guess. A malformed/empty address is a 400, not a
    silent null result, so the frontend can tell "you typed something
    Census rejected" apart from "a real address with no match"."""
    address = (address or "").strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")
    if len(address) > 200:
        raise HTTPException(status_code=400, detail="address is too long")

    try:
        async with make_async_client(timeout=15.0) as client:
            response = await client.get(
                _CENSUS_GEOCODER_URL,
                params={
                    "address": address,
                    "benchmark": "Public_AR_Current",
                    "vintage": "Current_Current",
                    "layers": "54",
                    "format": "json",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        # Never log `address` itself (a real visitor-entered street
        # address) — only that the lookup failed.
        logger.exception("Census geocode lookup failed")
        raise HTTPException(status_code=502, detail="Could not resolve that address right now.")

    matches = (payload.get("result") or {}).get("addressMatches") or []
    if not matches:
        return {"state": None, "district": None}

    match = matches[0]
    state = (match.get("addressComponents") or {}).get("state")
    districts = (match.get("geographies") or {}).get("119th Congressional Districts") or []
    if not state or not districts:
        return {"state": None, "district": None}

    cd = districts[0].get("CD119")
    if cd is None or not str(cd).isdigit():
        return {"state": None, "district": None}

    return {"state": state, "district": int(cd)}
