"""Midterm-elections API — candidate rosters, race detail, and PVI
(2026-07). Plain camelCase dicts, same convention as api/action.py's
existing /action/elections endpoint (that endpoint is unchanged; this is
a separate, fuller namespace for the new candidate-research feature)."""

import json
import logging
import pathlib
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, not_
from sqlalchemy.orm import Session, selectinload

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
    ApiCache,
    BallotMeasure,
    Candidate,
    JudicialNominee,
    MeasureCoverage,
    Race,
    RaceCoverageItem,
    Representative,
    Senator,
    StateLegNominee,
    StatewideNominee,
)
from app.pipeline.cache import api_cache_get
from app.pipeline.analyze.score_calculator import (
    compute_overall_score,
    get_district_pvi_map,
    get_pvi_meta,
    get_state_pvi_map,
)
from app.pipeline.candidate_dedup import dedupe_candidates, normalized_surname
from app.pipeline.election_pipeline import current_election_cycle
from app.pipeline.fetch import ballot_pdf
from app.pipeline.fetch.ballot_lookup import lookup_for_state
from app.pipeline.analyze.election_coverage import vacuous_corroboration_clause
from app.pipeline.fetch.ballot_pdf_sources import source_for_town as ballot_pdf_source_for_town
from app.pipeline.fetch.ballot_pdf_sources import town_names_for_state as ballot_pdf_town_names_for_state
from app.pipeline.fetch.civic_info import fetch_town_ballot
from app.pipeline.fetch.civic_info import is_configured as civic_is_configured
from app.pipeline.fetch.state_candidate_sources import source_for_state
from app.pipeline.fetch.state_candidates_common import (
    BALLOT_BASIS_TIER,
    JUDICIAL_COURT_LABELS,
    JUDICIAL_MARKER_TIER,
    JUDICIAL_MARKER_TTL_HOURS,
    judicial_marker_key,
    PARTY_CODE_MAP,
    STATE_LEG_CHAMBER_LABELS,
    district_label,
    district_sort_key,
    STATEWIDE_MARKER_TIER,
    STATEWIDE_MARKER_TTL_HOURS,
    STATEWIDE_OFFICE_LABELS,
    ballot_basis_key,
    statewide_marker_key,
)
from app.pipeline.fetch.state_election_dates import primary_date
from app.pipeline.fetch.town_directory import address_for_town, towns_for_state
from app.services.senator_service import STATE_NAMES
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

_STATE_LEG_CROSSWALK_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "data" / "state_leg_district_crosswalk.json"
)
_state_leg_towns_cache: dict[str, list[str]] | None = None


def _state_leg_towns() -> dict[str, list[str]]:
    """"{ST}-{chamber}-{n}" -> the towns that district covers, so a reader
    can find their seat by a place they know instead of by a number
    nobody memorises. The state-legislative twin of _district_counties(),
    and static for the same reason: it changes only when a state
    redistricts. Built by scripts/fetch_state_leg_crosswalk.py, which
    documents why the obvious sources give wrong answers. Empty dict —
    never a guess — if the file is missing."""
    global _state_leg_towns_cache
    if _state_leg_towns_cache is None:
        try:
            _state_leg_towns_cache = json.loads(_STATE_LEG_CROSSWALK_PATH.read_text())["districts"]
        except Exception:
            logger.exception("state_leg_district_crosswalk.json unavailable")
            _state_leg_towns_cache = {}
    return _state_leg_towns_cache


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


def _candidate_summary(cand: Candidate, stale_incumbent_ids: frozenset[str] = frozenset()) -> dict:
    return {
        "id": cand.id,
        "name": cand.name,
        "party": cand.party,
        # Per-CANDIDATE confidence, which `candidateSource` cannot carry:
        # a race's list can now mix a state-confirmed nominee with an
        # unopposed one the primary file never listed (see
        # _unopposed_nominees). The page must render the second kind
        # less confidently rather than silently promoting it.
        "confirmed": bool(cand.confirmed_general),
        # False for a candidate the state's ballot lists who never filed
        # with the FEC: no FEC page to link, no totals ever coming.
        "fecFiled": cand.fec_filed,
        "incumbentChallenge": None if cand.id in stale_incumbent_ids else cand.incumbent_challenge,
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


# Only these two nominate through a primary in the first place. An
# independent or minor-party candidate qualifies by petition, so a
# primary-results file structurally cannot see them — which is exactly
# what `_candidate_source`'s "nominees" answer already discloses, and is
# NOT something to paper over by re-admitting every such FEC filer.
# Measured against real 2026 data: re-admitting every unconfirmed party
# brought back 540 candidates, 118 of them independents; the rule below
# brings back 36, all of them major-party.
_PRIMARY_NOMINATING_PARTIES = frozenset({"DEM", "REP"})


def _ballot_basis_markers(db: Session, cycle: int) -> dict[str, bool]:
    """{state: whether its last successful source was the complete certified
    ballot} for `cycle`, in one query — see BALLOT_BASIS_TIER."""
    suffix = f"-{cycle}"
    out: dict[str, bool] = {}
    for row in db.query(ApiCache).filter(ApiCache.tier == BALLOT_BASIS_TIER).all():
        if row.cache_key.endswith(suffix):
            try:
                out[row.cache_key[: -len(suffix)]] = bool(json.loads(row.data_json).get("complete"))
            except ValueError:
                continue
    return out


def _complete_from(markers: dict[str, bool], state: str) -> bool:
    """A state the pipeline has not recorded yet (the first night after a
    deploy) falls back to what its configured source claims."""
    if state in markers:
        return markers[state]
    source = source_for_state(state) or {}
    return bool(source.get("general_ballot_complete") or source.get("general_list"))


def _ballot_complete(db: Session, state: str, cycle: int) -> bool:
    marker = api_cache_get(
        db, BALLOT_BASIS_TIER, ballot_basis_key(state, cycle), max_age_hours=STATEWIDE_MARKER_TTL_HOURS,
    )
    if marker is not None:
        return bool(marker.get("complete"))
    return _complete_from({}, state)


def _unopposed_nominees(
    candidates: list[Candidate], confirmed: list[Candidate], state: str, complete: bool,
) -> list[Candidate]:
    """Real November candidates a primary-results file cannot see, because
    their primary was never held.

    A state that cancels an uncontested primary (Delaware does, and so in
    practice do most party-primary states) publishes no row for a
    candidate who drew no opponent. `_confirmed_or_all` would then treat
    that candidate as a loser and drop them — which deleted 36 real
    candidates from live races, 19 of them SITTING members of Congress
    running for re-election: Warner and Ernst in their own Senate races,
    Crockett, Himes, Castor, Griffith, Bilirakis and a dozen more in
    theirs. A voter reading those pages saw a one-party ballot.

    Scoped tightly, because the filter it relaxes exists for a real
    reason (TX's 19 stale FEC filers). A candidate comes back only when
    ALL of these hold:

    * The state nominates one-per-party. In a top-two/top-four state the
      single combined contest really does decide every advancer
      regardless of party, so a party with nobody confirmed genuinely
      has nobody — `advance_count > 1` is left alone entirely.
    * Their party has NO confirmed nominee here. A party that actually
      held a primary has one, which also means an incumbent who LOST a
      primary stays filtered: losing implies the primary happened, which
      implies their party is covered, which excludes this path.
    * They are either the only filer of that party (nobody to lose to),
      or the race's single FEC-coded incumbent. "Single" matters — FEC's
      own incumbent coding is only self-consistent when one candidate
      carries it, the same condition `_stale_incumbent_ids` already
      refuses to trust below.

    A party with several filers and no incumbent is deliberately NOT
    guessed at: that is a genuine coverage gap in the state's own feed,
    and inventing a nominee would be worse than the gap. Everything
    returned here is unconfirmed, and `_candidate_summary` marks it so —
    the page must not present it as a confirmed nominee."""
    source = source_for_state(state) or {}
    if (source.get("advance_count", 1) or 1) > 1:
        return []
    # A certified ballot already names everyone on it. A party missing
    # from it has nobody on the November ballot, so re-admitting an FEC
    # filer for that party would add someone who is not running.
    if complete:
        return []
    covered = {c.party for c in confirmed}
    coded_incumbents = [c for c in candidates if c.incumbent_challenge == "I"]
    sole_incumbent = coded_incumbents[0] if len(coded_incumbents) == 1 else None

    recovered: list[Candidate] = []
    for party in _PRIMARY_NOMINATING_PARTIES - covered:
        pool = [c for c in candidates if c.party == party and not c.confirmed_general]
        if len(pool) == 1:
            recovered.append(pool[0])
        elif sole_incumbent is not None and sole_incumbent in pool:
            recovered.append(sole_incumbent)
    return recovered


def _confirmed_or_all(candidates: list[Candidate], state: str, complete: bool) -> list[Candidate]:
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
    than one route. Also the one place dedupe_candidates runs, so every
    one of those endpoints gets it for free."""
    candidates = dedupe_candidates(candidates)
    confirmed = [c for c in candidates if c.confirmed_general]
    if confirmed:
        return confirmed + _unopposed_nominees(candidates, confirmed, state, complete)
    if any(c.on_primary_ballot for c in candidates):
        return [c for c in candidates if c.on_primary_ballot]
    return candidates


def _candidate_source(candidates: list[Candidate], complete: bool) -> str:
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
        return "confirmed" if complete else "nominees"
    if any(c.on_primary_ballot for c in candidates):
        return "primary"
    return "filers"


# Order from weakest evidence to strongest, so a state's basis is the
# WEAKEST of its races — a page is only as certain as its least certain
# contest, and saying "confirmed" while one race is still guesswork is
# the failure this exists to prevent.
_BASIS_STRENGTH = ("filers", "primary", "nominees", "confirmed")


def _ballot_basis(races: list[dict], primary_date_iso: str | None) -> dict:
    """What this state's candidate lists actually ARE, and whether that
    is still defensible given the calendar.

    `_candidate_source` already answers the first half per race. The
    second half is what nothing surfaced: "filers" means something
    completely different before and after a primary. Before, it is the
    honest best answer — nobody knows the ballot yet. After, it means the
    ballot HAS been decided and this platform does not have it, while the
    page goes on showing every FEC filer as though the race were open.

    Measured 2026-09-26 across all 50 states: 39 had certified
    candidates, and ELEVEN were still on `filers` after their primary —
    Ohio by 144 days, Louisiana 133, New York 95, with up to 25 filers
    listed in a single race whose real ballot holds about two. Those
    states' pages were not merely out of date, they were telling a voter
    that a settled contest was still open.

    So the state-level basis is reported with the calendar attached, and
    the frontend is handed the judgement rather than re-deriving it (the
    page must not compute what the API already knows).
    """
    sources = {r.get("candidateSource") for r in races if r.get("candidateSource")}
    if not sources:
        basis = None
    else:
        basis = min(sources, key=lambda x: _BASIS_STRENGTH.index(x)
                    if x in _BASIS_STRENGTH else 0)

    passed, days_since = None, None
    if primary_date_iso:
        try:
            pd = date.fromisoformat(primary_date_iso)
        except (TypeError, ValueError):
            pd = None
        if pd:
            today = utcnow().date()
            passed = pd < today
            days_since = (today - pd).days if passed else None

    return {
        "basis": basis,
        "primaryPassed": passed,
        "daysSincePrimary": days_since,
        # The one field the page actually branches on: the list is FEC
        # filers for a contest whose primary is already decided.
        "supersededByPrimary": bool(basis == "filers" and passed),
    }


def _stale_incumbent_ids(candidates: list[Candidate]) -> frozenset[str]:
    """FEC candidate_ids whose incumbent_challenge == "I" should NOT be
    trusted for this race. FEC's own incumbent_challenge coding is
    self-consistent for any real race shape: a defended seat has exactly
    one "I" and the rest "C"; an open seat has every candidate "O". A
    race carrying BOTH "O" and "I" at once is not a real shape -- it
    happens when one candidate's own committee record went stale (e.g. an
    incumbent announces they won't seek re-election but their still-open
    committee never gets reclassified, while every other filer correctly
    syncs to "O" once the seat is recognized as open). FEC has no
    "declined to run" status code, so this O-vs-I mix is the only usable
    signal -- not a financial threshold: a stale incumbent can still show
    real, non-trivial fundraising activity, so "low recent activity"
    would not reliably catch this either.

    Race-scoped and conservative: an ordinary defended-seat race (one
    "I", nobody "O") never matches, so this can only ever REMOVE a
    trusted incumbent claim, never invent one.
    """
    statuses = {c.incumbent_challenge for c in candidates}
    if "O" in statuses and "I" in statuses:
        return frozenset(c.id for c in candidates if c.incumbent_challenge == "I")
    return frozenset()


def _race_summary(race: Race, state_pvi: dict, district_pvi: dict, complete: bool) -> dict:
    candidates = sorted(
        _confirmed_or_all(race.candidates, race.state, complete),
        key=lambda c: (c.cash_on_hand or 0.0),
        reverse=True,
    )
    top_candidates = candidates[:2]
    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    stale_incumbent_ids = _stale_incumbent_ids(race.candidates)
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
        "topCandidates": [_candidate_summary(c, stale_incumbent_ids) for c in top_candidates],
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
    stale_incumbent_ids: frozenset[str] = frozenset(),
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
    if cand.incumbent_challenge != "I" or cand.id in stale_incumbent_ids:
        return None
    # Reuses candidate_dedup's surname extraction rather than a second
    # inline copy — this also fixes a real, if narrow, matching gap: an
    # incumbent whose OWN surname carries a generational suffix (FEC's
    # real "ONDER JR, ROBERT FRANK") previously included "jr" as part of
    # last_name, which could never match a Representative/Senator row's
    # plain name.
    last_name = normalized_surname(cand.name)
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
    complete: bool,
) -> dict:
    """Same shape as race_detail's response, minus coverage — this backs
    the per-state ballot view, which needs every candidate (not just the
    top-2-by-funds _race_summary uses for the map/directory) but not the
    news feed, which stays one click away on the existing race-detail
    page. Confirmed-general filtering (see _confirmed_or_all) applies
    here too, same as race_detail."""
    candidates = sorted(_confirmed_or_all(race.candidates, race.state, complete), key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
    pvi, pvi_level = _pvi_for_race(race, state_pvi, district_pvi)
    stale_incumbent_ids = _stale_incumbent_ids(race.candidates)
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
        "candidateSource": _candidate_source(race.candidates, complete),
        "candidates": [
            {
                **_candidate_summary(c, stale_incumbent_ids),
                "incumbentRecord": _incumbent_link(c, race, reps_by_district, senators, stale_incumbent_ids),
            }
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

# Which coverage reaches a reader at all.
#
# The Bluesky half of this feed is an open keyword search of the whole
# network for a candidate's name, and a name-mention is not coverage.
# What that produced on Connecticut's page, verbatim: a tabloid item
# about a diver's death, five reposts of one YouTube video, a
# bill-notification bot and a Spanish health-tip post — 74 items, of
# which 5 were journalism.
#
# A vetted news source passes on provenance alone. A social post has to
# earn its place by answering BOTH questions, because they have
# different answers and only asking one gets it wrong in a different
# direction each time:
#
#   relevance    — is this about the race? (analyze/race_relevance.py)
#   has_advocacy — does it tell a reader how to vote?
#
# Measured over 1,500 real Bluesky items: 31.5% clear relevance, and 7%
# of those are campaign advocacy — "Elect Jonathan Nez to Congress!"
# scores 0.632, because campaign material is maximally on-topic for a
# campaign. Gating on relevance alone would have concentrated the feed
# toward exactly the content that caused the 2026-09-23 incident;
# gating on source alone (the first fix here) threw away real local
# newsrooms — @nebraskaexaminer, @ksntnews, @connecticutintel all clear
# both bars.
#
# Unscored items (relevance IS NULL) are excluded: fail closed, and the
# next ingest scores them.
COVERAGE_SOURCE_TYPES = ("news",)


def _coverage_is_displayable(db: Session):
    """SQL predicate for the feed — vetted news only.

    This relaxed once, to let real local newsrooms that publish on
    Bluesky back in (@nebraskaexaminer, @journalstar), gated on
    relevance, no-advocacy and a DNS-verified domain handle. That was
    wrong, and Minnesota's page is why: it carried "Dave Hughes still a
    whiny cunt" from @crowbar.wtf — which passes the domain rule,
    because it IS a domain — and directly beneath it a post about the
    AUSTRALIAN comedian of the same name defending Pauline Hanson's One
    Nation, filed as MN-7 coverage.

    The measurement that justified relaxing was too narrow: it found 7%
    of relevant social items were "advocacy", but advocacy meant
    ELECTIONEERING PHRASING, not professionalism. The other 93% includes
    abuse and mistaken identity, which no phrasing test sees.

    A name mention is not coverage. Four filters could not make it one.

    The second clause hides — rather than deletes — a surname match from
    a state's own newsroom that cannot clear the relevance bar. That
    corroboration is vacuous (election_coverage._corroboration_is_vacuous:
    the Kentucky Lantern says "Kentucky" in every article, so surname +
    state name identifies nobody).

    It is a DISPLAY filter and not a delete on purpose. Deleting those
    rows was tried and churned: the article is still in the outlet's RSS
    feed, so removing the row is exactly what stops _already_ingested
    from blocking it, and the next pass re-ingested, re-embedded and
    re-deleted the same items every 15 minutes — observed live as "36
    ingested" immediately followed by "Dropped 36". The row has to STAY
    for the ingest-time check to keep working; what must not happen is
    showing it to a reader.

    The syndication sweep can delete safely because it KEEPS the first
    outlet's copy, and that surviving row is what makes every later
    reprint a duplicate at ingest.
    """
    return and_(
        RaceCoverageItem.source_type.in_(COVERAGE_SOURCE_TYPES),
        not_(vacuous_corroboration_clause(db)),
    )


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
        .filter(
            RaceCoverageItem.race_id.in_(races_by_id.keys()),
            _coverage_is_displayable(db),
        )
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


def _statewide_marker(db: Session, state: str, cycle: int) -> dict | None:
    """The pipeline's record that it actually looked at this state's
    non-federal contests, or None if it never has.

    One marker covers both the executive offices and the legislature:
    they ride the same ballot in the same response, and the
    `statewide_offices` opt-in that gates writing it means the same thing
    for each — this state's real contest labels were read against the
    parsers. Split markers would let the two drift into disagreeing about
    whether the state had been checked.
    """
    return api_cache_get(
        db, STATEWIDE_MARKER_TIER, statewide_marker_key(state, cycle),
        max_age_hours=STATEWIDE_MARKER_TTL_HOURS,
    )


class StatewideCoverageStatus:
    """The three things an empty statewide-executive section can mean.

    Same null-is-not-zero discipline MeasureCoverage encodes for ballot
    measures, and for the same reason: "Rhode Island elects no Secretary
    of State this cycle" and "nobody has taught this state's feed to us
    yet" render identically as an empty section, and a reader takes the
    empty section to mean the first.
    """
    NOT_YET_COVERED = "not_yet_covered"
    COVERED = "covered"
    CONFIRMED_NONE = "confirmed_none"


def _statewide_section(db: Session, state: str, cycle: int) -> tuple[list[dict], dict]:
    """This state's statewide-executive contests and what we actually
    know about them.

    Coverage comes from the sync marker the pipeline writes, NOT from
    whether rows happen to exist: a state with zero rows and no marker
    has never been looked at, while a state with zero rows AND a marker
    was checked and genuinely has none on this ballot. Deriving the
    status from the row count alone would collapse exactly the two cases
    this function exists to keep apart.
    """
    marker = _statewide_marker(db, state, cycle)
    nominees = (
        db.query(StatewideNominee)
        .filter(StatewideNominee.state == state, StatewideNominee.cycle_year == cycle)
        .all()
    )

    by_office: dict[tuple[str, str | None], list[dict]] = {}
    for row in nominees:
        by_office.setdefault((row.office, row.district), []).append({
            # FEC's own 3-letter codes, so the frontend colours a nominee
            # through the exact same majorPartyOf() every federal
            # candidate already goes through — a second party vocabulary
            # on one page is how the two drift apart.
            "party": PARTY_CODE_MAP.get(row.party, row.party),
            "name": row.display_name or row.last_name,
        })

    # STATEWIDE_OFFICE_LABELS is insertion-ordered by seniority of the
    # office, which is the order a state prints them on the real ballot.
    # A statewide body seated by district (Georgia's Public Service
    # Commission) contributes one entry per seat, labelled with it —
    # otherwise District 3 and District 5 render as one indistinguishable
    # "Public Service Commission" row.
    races = [
        {
            "office": code if district is None else f"{code}-{district}",
            "label": label if district is None else f"{label}, District {district}",
            "nominees": sorted(by_office[(code, district)], key=lambda n: n["party"]),
        }
        for code, label in STATEWIDE_OFFICE_LABELS.items()
        for district in sorted(
            (d for (c, d) in by_office if c == code),
            key=lambda d: district_sort_key(d) if d else (-1, ""),
        )
    ]

    if marker is None:
        status = StatewideCoverageStatus.NOT_YET_COVERED
    elif races:
        status = StatewideCoverageStatus.COVERED
    else:
        status = StatewideCoverageStatus.CONFIRMED_NONE

    return races, {
        "status": status,
        "sourceName": (marker or {}).get("sourceName") or None,
        "checkedAt": (marker or {}).get("checkedAt") or None,
    }


def _state_leg_section(db: Session, state: str, cycle: int, marker: dict | None) -> list[dict]:
    """This state's legislative seats, grouped by chamber and ordered by
    district number.

    Shares the statewide marker rather than keeping its own: one
    `statewide_offices` opt-in covers both because it means the same
    thing for each — this state's real contest labels were read against
    the parsers. The two kinds ride the same ballot in the same response,
    so a state that has one checked has both.

    Only seats with a nominee appear. A chamber where every seat is
    uncontested this cycle simply has fewer rows, which is the truth; the
    page never invents a row for a seat nobody filed for.
    """
    if marker is None:
        return []

    towns = _state_leg_towns()
    rows = (
        db.query(StateLegNominee)
        .filter(StateLegNominee.state == state, StateLegNominee.cycle_year == cycle)
        .all()
    )
    seats: dict[tuple[str, str, str | None], list[dict]] = {}
    for row in rows:
        seats.setdefault((row.chamber, row.district, row.seat), []).append({
            "party": PARTY_CODE_MAP.get(row.party, row.party),
            "name": row.display_name,
        })

    out = []
    for chamber, label in STATE_LEG_CHAMBER_LABELS.items():
        districts = [
            {
                # What the reader sees: "5" where a district elects one
                # member, "1A" or "5-2" where it elects several. The
                # TOWNS still come from the bare district, because both
                # seats of a multi-member district share one geography.
                "district": district_label(district, seat),
                "towns": towns.get(f"{state}-{chamber}-{district}") or [],
                "nominees": sorted(people, key=lambda n: n["party"]),
            }
            for (ch, district, seat), people in sorted(
                seats.items(), key=lambda kv: (district_sort_key(kv[0][1]), kv[0][2] or "")
            )
            if ch == chamber
        ]
        if districts:
            out.append({"chamber": chamber, "label": label, "districts": districts})
    return out


class JudicialCoverageStatus:
    """The three things an empty judicial section can mean — the same
    null-is-not-zero discipline StatewideCoverageStatus encodes, and it
    matters more here than anywhere else on this page.

    A state whose judicial seats were ALL decided in its primary has
    genuinely zero November contests. Idaho is exactly that: all three
    of its matched contests were unopposed, so all three were elected in
    May under Idaho Code 34-1217. Rendering that identically to "nobody
    has read this state's judicial statute yet" tells a reader there is
    nothing to research when the truth is that the races are over.
    """
    NOT_YET_COVERED = "not_yet_covered"
    COVERED = "covered"
    CONFIRMED_NONE = "confirmed_none"


def _judicial_marker(db: Session, state: str, cycle: int) -> dict | None:
    """The pipeline's record that it read this state's judicial contests.

    Its OWN marker, not the statewide one: that covers the executive
    offices and the legislature together because they are one claim,
    whereas judicial additionally asserts that this state's statute on
    what a majority MEANS has been read (Washington and Idaho mean
    opposite things by it). A state can be checked for one and not the
    other.
    """
    return api_cache_get(
        db, JUDICIAL_MARKER_TIER, judicial_marker_key(state, cycle),
        max_age_hours=JUDICIAL_MARKER_TTL_HOURS,
    )


def _judicial_section(
    db: Session, state: str, cycle: int, marker: dict | None,
) -> tuple[list[dict], dict]:
    """This state's elected judgeships grouped by court, plus what an
    empty list means.

    Ordered by court seniority (supreme, appeals, superior, district)
    rather than alphabetically, because that is how a ballot and a
    reader both order them.
    """
    if marker is None:
        return [], {"status": JudicialCoverageStatus.NOT_YET_COVERED,
                    "checkedAt": None, "sourceName": None}

    coverage = {
        "checkedAt": marker.get("checkedAt"),
        "sourceName": marker.get("sourceName") or None,
    }
    rows = (
        db.query(JudicialNominee)
        .filter(JudicialNominee.state == state, JudicialNominee.cycle_year == cycle)
        .all()
    )
    if not rows:
        coverage["status"] = JudicialCoverageStatus.CONFIRMED_NONE
        return [], coverage
    coverage["status"] = JudicialCoverageStatus.COVERED

    seats: dict[tuple[str, str | None, str | None], list[dict]] = {}
    for row in rows:
        seats.setdefault((row.court, row.district, row.seat), []).append({
            "party": PARTY_CODE_MAP.get(row.party, row.party),
            "name": row.display_name,
        })

    out = []
    for court, label in JUDICIAL_COURT_LABELS.items():
        entries = [
            {
                # "District 14, Seat 3" for a trial seat; "Seat 3" alone
                # for an appellate one, which is elected statewide and
                # has no district to name.
                "seat": ", ".join(
                    part for part in (
                        f"District {district}" if district else "",
                        f"Seat {seat}" if seat else "",
                    ) if part
                ) or label,
                "nominees": sorted(people, key=lambda n: n["party"]),
            }
            for (ct, district, seat), people in sorted(
                seats.items(),
                key=lambda kv: (district_sort_key(kv[0][1] or ""),
                                district_sort_key(kv[0][2] or "")),
            )
            if ct == court
        ]
        if entries:
            out.append({"court": court, "label": label, "seats": entries})
    return out, coverage


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
    complete = _ballot_complete(db, state, cycle)
    full = [_race_full(r, state_pvi, district_pvi, reps_by_district, senators, complete) for r in races]
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
    statewide_races, statewide_coverage = _statewide_section(db, state, cycle)
    state_leg_races = _state_leg_section(db, state, cycle, _statewide_marker(db, state, cycle))
    judicial_races, judicial_coverage = _judicial_section(
        db, state, cycle, _judicial_marker(db, state, cycle))

    return cached_json({
        "state": state,
        # The full name is what people search for ("California ballot
        # measures"), and the page's title and heading had only the code.
        "stateName": STATE_NAMES.get(state, state),
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
        # What the candidate lists on this page actually are, and whether
        # the calendar has overtaken them — see _ballot_basis. The page
        # must not re-derive this from primaryDate itself.
        "ballotBasis": _ballot_basis(senate_races + house_races, primary_date(state, cycle)),
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
        "statewideRaces": statewide_races,
        "statewideCoverage": statewide_coverage,
        "stateLegRaces": state_leg_races,
        "judicialRaces": judicial_races,
        "judicialCoverage": judicial_coverage,
        "omits": ([
            # Dropped the moment this state's executive contests are
            # genuinely covered — the list has to shrink as the gaps
            # actually close, or it stops describing the page and starts
            # being boilerplate a reader learns to skip.
            "Governor and other statewide executive contests",
        ] if statewide_coverage["status"] == StatewideCoverageStatus.NOT_YET_COVERED else []) + ([
            "State legislative districts",
        ] if not state_leg_races else []) + (
            # Same rule as the two above: the line shrinks the moment this
            # state's judgeships are genuinely covered. It shrinks rather
            # than disappearing, because retention questions are a
            # separate yes/no ballot item — not a contest between
            # candidates — and nothing here reads them yet. Saying
            # "judicial contests" is covered while retention questions
            # are not is the honest half-statement.
            ["Judicial retention questions"]
            if judicial_coverage["status"] != JudicialCoverageStatus.NOT_YET_COVERED
            else ["Judicial contests and retention questions"]
        ) + [
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
    markers = _ballot_basis_markers(db, current_election_cycle())
    data = [
        _race_summary(r, state_pvi, district_pvi, _complete_from(markers, r.state))
        for r in races
    ]
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
    complete = _ballot_complete(db, race.state, race.cycle_year)
    candidates = sorted(_confirmed_or_all(race.candidates, race.state, complete), key=lambda c: (c.cash_on_hand or 0.0), reverse=True)
    stale_incumbent_ids = _stale_incumbent_ids(race.candidates)
    coverage = (
        db.query(RaceCoverageItem)
        .filter(
            RaceCoverageItem.race_id == race_id,
            _coverage_is_displayable(db),
        )
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
        "candidateSource": _candidate_source(race.candidates, complete),
        "candidates": [_candidate_summary(c, stale_incumbent_ids) for c in candidates],
        "coverage": [_coverage_item(item) for item in coverage],
    }, max_age=CACHE_TTL_DETAIL_S)


@router.get("/candidates/{candidate_id}")
def candidate_detail(candidate_id: str, db: Session = Depends(get_db)):
    """Single candidate profile, with its parent race's identity."""
    cand = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if cand is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    race = cand.race
    stale_incumbent_ids = _stale_incumbent_ids(race.candidates) if race else frozenset()
    return cached_json({
        **_candidate_summary(cand, stale_incumbent_ids),
        "disbursements": cand.disbursements,
        "individualItemizedContributions": cand.individual_itemized_contributions,
        "race": {
            "id": race.id,
            "office": race.office,
            "state": race.state,
            "district": race.district,
        } if race else None,
    }, max_age=CACHE_TTL_DETAIL_S)

