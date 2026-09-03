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
   Unlike every other state on file, Indiana's own system computes and
   publishes the winner directly (`isWinner: "T"` per candidate), so this
   module trusts that flag rather than re-deriving a plurality winner
   from vote totals — the state's own authority is the more direct
   source of truth here, not a shortcut around one.

Verified live 2026-09-03 against the real, certified 2026 primary
(WriteTime 2026-08-20, Certified "T"): all 9 US House districts resolved,
each cross-checked against the state's own isWinner flag and vote totals.
"""

import logging

import httpx

from app.pipeline.fetch.http_utils import BROWSER_JSON_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE = "https://enr.indianavoters.in.gov/site"
_HEADERS = BROWSER_JSON_HEADERS
_rate_limiter = RateLimiter(rps=1.0)

_ORDINALS = {
    "First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6,
    "Seventh": 7, "Eighth": 8, "Ninth": 9, "Tenth": 10, "Eleventh": 11,
    "Twelfth": 12, "Thirteenth": 13, "Fourteenth": 14, "Fifteenth": 15,
}


async def _get_json(client: httpx.AsyncClient, url: str, label: str) -> dict | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0,
        log_label=label, headers=_HEADERS,
    )
    if resp is None:
        return None
    try:
        body = resp.json()
    except ValueError:
        logger.warning("%s did not return valid JSON", label)
        return None
    return body.get("Root", body) if isinstance(body, dict) else None


def _office_and_district(title: str) -> tuple[str, int | None] | None:
    """(office, district) for a race's OFFICE_TITLE, or None if it isn't a
    federal race — this module only ever reads from the Federal manifest
    heading, but a race's own title is still cross-checked here rather
    than trusted blindly."""
    if title.startswith("United States Senator"):
        return "S", None
    if not title.startswith("United States Representative"):
        return None
    for word, num in _ORDINALS.items():
        if title.endswith(f"{word} District"):
            return "H", num
    return None


def _candidates(race: dict) -> list[dict]:
    cand = race.get("Candidates", {}).get("Candidate")
    if cand is None:
        return []
    return cand if isinstance(cand, list) else [cand]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is IN-only by construction
) -> list[dict] | None:
    settings = await _get_json(client, f"{BASE}/data/settings.json", f"IN settings {year}")
    if settings is None:
        return None
    if settings.get("Certified") != "T":
        logger.info("IN primary certification for %d not yet final", year)
        return []
    version = settings.get("VersionType")
    if not version:
        return None

    manifest = await _get_json(
        client, f"{BASE}/data/statewideElectionsC_{version}.json",
        f"IN office manifest {year}",
    )
    if manifest is None:
        return None

    category_ids = [
        item["OFFICECATEGORYID"]
        for heading in manifest.get("List", [])
        if heading.get("Heading") == "Federal"
        for item in heading.get("Items", {}).get("Item", [])
        if item.get("OFFICECATEGORYID")
    ]
    if not category_ids:
        logger.warning("IN office manifest for %d lists no Federal category", year)
        return None

    results: list[dict] = []
    for cat_id in category_ids:
        data = await _get_json(
            client, f"{BASE}/data/OffCatC_{cat_id}_{version}.json",
            f"IN federal races (category {cat_id}) {year}",
        )
        if data is None:
            return None
        races = data.get("StatewideSummary", {}).get("Race", [])
        races = races if isinstance(races, list) else [races]
        for race in races:
            office_district = _office_and_district(race.get("OFFICE_TITLE", ""))
            if office_district is None:
                continue
            office, district = office_district
            for cand in _candidates(race):
                if cand.get("isWinner") != "T":
                    continue
                party = normalize_party(cand.get("PARTY", ""))
                if party is None:
                    continue
                last_name = surname(cand.get("CandidateName", ""), last_first=True)
                if not last_name:
                    continue
                results.append({
                    "office": office, "district": district,
                    "party": party, "last_name": last_name,
                })

    return results
