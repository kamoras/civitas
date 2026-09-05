"""Indiana's own election-night-reporting API — a single-state deployment
(enr.indianavoters.in.gov), so this is a vendor module in the same sense
state_candidates_pa.py is, not a per-state fetcher (see state_candidates.py
for why that distinction is the whole design).

Three hops, nothing cycle-specific written down (found by driving the site
in a browser and reading its own network activity, then confirmed with no
browser at all):

1. data/settings.json carries `VersionType` (a short cache-busting tag
   every OTHER file's name is suffixed with — "OffCatC_1005_A.json", not
   a fixed name, so it must be read fresh every call) and `Certified`
   ("T"/"F") — Indiana's own official certification flag for this
   election, gating everything below the same way require_official does
   for the Enhanced Voting states.
2. data/statewideElectionsC_{version}.json is the FULL office manifest,
   grouped under headings ("Federal", "State", ...). The federal House/
   Senate categories' OFFICECATEGORYID is read off this manifest EVERY
   run, never hardcoded — verified live 2026-09-03 that Indiana's 2026
   primary manifest lists exactly one Federal category ("US
   Representative", id 1005) and no Senate category at all, meaning no
   Indiana Senate seat is on this cycle's ballot; a state whose Senate
   class next comes up would simply add a second Federal entry here,
   picked up with no code change.
3. data/OffCatC_{id}_{version}.json holds every race in that category —
   ALL 9 Indiana US House districts came back in the one 1005 file.
   Each candidate carries both Indiana's own isWinner flag AND a raw
   vote TOTAL. isWinner is NOT trusted directly: it has no visible
   tie-handling of its own, and this module instead runs the same
   TOTAL every vote-count-based strategy uses through the shared,
   tie-safe pick_nominee (state_candidates_common.py) — a genuine tie
   (or a stray isWinner="T" on two same-party candidates) confirms
   nobody rather than silently confirming both.

Verified live 2026-09-03 against the real, certified 2026 primary
(WriteTime 2026-08-20, Certified "T"): all 9 US House districts
resolved, each pick_nominee winner matching the state's own isWinner
flag exactly.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, pick_nominee, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE = "https://enr.indianavoters.in.gov/site"
_rate_limiter = RateLimiter(rps=1.0)

# A race's own SubSortOrder carries its district as a NUMBER, not a
# spelled-out ordinal ("United States Representative, (1) District" —
# verified live, alongside OFFICE_TITLE's "...First District" for the
# same race) — matching that is evergreen the way OFFICE_TITLE's ordinal
# WORDING isn't (no upper bound, no dependency on English ordinal
# spelling surviving a wording change). Kept as a fallback rather than
# the only source: a race missing SubSortOrder, or one this pattern
# doesn't match, still resolves off OFFICE_TITLE's own ordinal word.
_DISTRICT_NUMBER_RE = re.compile(r"\((\d+)\)\s*District")
_ORDINALS = {
    "First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6,
    "Seventh": 7, "Eighth": 8, "Ninth": 9, "Tenth": 10, "Eleventh": 11,
    "Twelfth": 12, "Thirteenth": 13, "Fourteenth": 14, "Fifteenth": 15,
}


def _unwrap_root(body: dict | list | None) -> dict | None:
    """This vendor wraps some (not all) payloads in a "Root" envelope —
    unwrapped here rather than in the shared fetch helper, since that's
    a vendor-specific shape, not part of the retry/parse mechanics."""
    return body.get("Root", body) if isinstance(body, dict) else None


def _office_and_district(title: str, sub_sort_order: str = "") -> tuple[str, int | None] | None:
    """(office, district) for a race, or None if it isn't a federal race —
    this module only ever reads from the Federal manifest heading, but a
    race's own title is still cross-checked here rather than trusted
    blindly. `title` (OFFICE_TITLE) decides office; `sub_sort_order`, when
    it matches, gives a more evergreen district NUMBER than title's own
    ordinal WORD."""
    if title.startswith("United States Senator"):
        return "S", None
    if not title.startswith("United States Representative"):
        return None
    num_m = _DISTRICT_NUMBER_RE.search(sub_sort_order)
    if num_m:
        return "H", int(num_m.group(1))
    for word, num in _ORDINALS.items():
        if title.endswith(f"{word} District"):
            return "H", num
    logger.info("IN: House race title %r matched no known district", title)
    return None


def _candidates(race: dict) -> list[dict]:
    cand = (race.get("Candidates") or {}).get("Candidate")
    if cand is None:
        return []
    return cand if isinstance(cand, list) else [cand]


def _race_results(race: dict, office: str, district: int | None) -> list[dict]:
    """This race's confirmed nominees, one per party — chosen via the
    SAME tie-safe pick_nominee every vote-count-based strategy uses,
    rather than trusting the state's own isWinner flag directly: Indiana
    marks isWinner per candidate with no visible tie-handling of its
    own, and a genuine tie (or a stray isWinner="T" on more than one
    same-party candidate) would otherwise silently confirm two people
    for one nomination. The TOTAL vote count is right there in the same
    payload, so there is no reason not to run it through the same
    safety net as every other state."""
    by_party: dict[str, list[tuple[str, int]]] = {}
    for cand in _candidates(race):
        party = normalize_party(cand.get("PARTY", ""))
        if party is None:
            continue
        last_name = surname(cand.get("CandidateName", ""), last_first=True)
        votes = cand.get("TOTAL")
        if not last_name or not isinstance(votes, int):
            continue
        by_party.setdefault(party, []).append((last_name, votes))

    results = []
    for party, choices in by_party.items():
        won = pick_nominee(choices, runoff_threshold_pct=None)
        if won:
            results.append({
                "office": office, "district": district,
                "party": party, "last_name": won[0],
            })
    return results


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is IN-only by construction
) -> list[dict] | None:
    settings = _unwrap_root(
        await fetch_json_with_retry(client, _rate_limiter, f"{BASE}/data/settings.json", f"IN settings {year}")
    )
    if settings is None:
        return None
    if settings.get("Certified") != "T":
        logger.info("IN primary certification for %d not yet final", year)
        return []
    version = settings.get("VersionType")
    if not version:
        return None

    manifest = _unwrap_root(await fetch_json_with_retry(
        client, _rate_limiter, f"{BASE}/data/statewideElectionsC_{version}.json",
        f"IN office manifest {year}",
    ))
    if manifest is None:
        return None

    category_ids = [
        item["OFFICECATEGORYID"]
        for heading in manifest.get("List", [])
        if heading.get("Heading") == "Federal"
        for item in (heading.get("Items") or {}).get("Item", [])
        if item.get("OFFICECATEGORYID")
    ]
    if not category_ids:
        logger.warning("IN office manifest for %d lists no Federal category", year)
        return None

    results: list[dict] = []
    for cat_id in category_ids:
        data = _unwrap_root(await fetch_json_with_retry(
            client, _rate_limiter, f"{BASE}/data/OffCatC_{cat_id}_{version}.json",
            f"IN federal races (category {cat_id}) {year}",
        ))
        if data is None:
            return None
        races = (data.get("StatewideSummary") or {}).get("Race", [])
        races = races if isinstance(races, list) else [races]
        for race in races:
            office_district = _office_and_district(
                race.get("OFFICE_TITLE", ""), race.get("SubSortOrder", ""),
            )
            if office_district is None:
                continue
            office, district = office_district
            results.extend(_race_results(race, office, district))

    if not results:
        logger.warning("IN federal races for %d yielded no confirmed nominees", year)
        return None

    return results
