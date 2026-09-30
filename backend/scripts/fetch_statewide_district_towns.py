"""Build app/data/statewide_district_towns.json — which towns each district
of a statewide body elected BY DISTRICT covers.

The ballot page lists every seat of such a body (New Hampshire's Executive
Council, Massachusetts's Governor's Council, ...) under its district, the
way it lists legislative seats, and a reader can vote in exactly one of
them. Civitas never asks where the reader lives (AGENTS.md principle 8),
so "which one is mine" has to be answerable from a place they already
know: the same reason county_district_crosswalk.json and
state_leg_district_crosswalk.json exist.

Both sources here are the states' own official results for the district
contests, which is the most direct statement of a district's extent
there is -- a town appears in a district's results because its voters
voted in that contest:

  New Hampshire. The Secretary of State's primary-results pages link one
  "Executive Council District N" workbook per district and party; its
  first column lists every town (and city ward) in the district, then a
  TOTALS row. Read from the Democratic primary's workbooks.

  Massachusetts. The Secretary of the Commonwealth's election statistics
  archive (electionstats.state.ma.us, PD43+) publishes each Governor's
  Council primary's results as a CSV by city/town
  (/elections/download/{id}/precincts_include:0/). A city split between
  districts (Boston) appears under each district it is in, which is the
  truth: a Boston reader has to look at their ward.

Both states print some names their own way -- PD43 abbreviates ("N.
Andover", "W. Springfield"), New Hampshire's workbooks shorten its
unincorporated places ("At.& Gil. Ac. Gt.") -- and a reader types the
real name. So every printed name is resolved against the Census Bureau's
list of each state's county subdivisions (TIGERweb, the same service
fetch_state_leg_crosswalk.py reads), which in both states are exactly the
municipalities and unincorporated places, and the official name is what
is written. See _resolve for the matching; a name that resolves to no
place, or to more than one, stops the run rather than being guessed. A
city ward ("Manchester Ward 4") keeps its ward, the city resolved.

Other district-elected statewide bodies (Nebraska's, New Mexico's and the
state boards of education of AL, CO, KS, TX and UT) have no official
machine-readable district-to-place list this script could read; the page
says each voter votes in one district and links the official lookup for
them instead (statewide_seats.json). Colorado's regents need nothing
here: their districts ARE congressional districts, whose counties
county_district_crosswalk.json already carries.

Rerun after a redistricting (the districts change once a decade):

    cd backend && .venv/bin/python scripts/fetch_statewide_district_towns.py [year]
"""

import asyncio
import csv
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.contact import BOT_USER_AGENT  # noqa: E402
from app.pipeline.fetch import state_candidates_ma as ma  # noqa: E402
from app.pipeline.fetch import state_candidates_nh as nh  # noqa: E402
from app.pipeline.fetch.http_utils import fetch_text_with_retry  # noqa: E402
from app.pipeline.fetch.state_candidates_common import parse_statewide_office  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "statewide_district_towns.json"

_COUSUB_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
    "/Places_CouSub_ConCity_SubMCD/MapServer/1/query"
)
_FIPS = {"MA": "25", "NH": "33"}
_WARD_RE = re.compile(r"^(?P<place>.+?)\s+(?P<ward>Ward\s+\d+)$", re.IGNORECASE)


async def official_places(client: httpx.AsyncClient, state: str) -> list[str]:
    """Every county subdivision's official name forms in `state`: its
    BASENAME ("North Andover", "Agawam Town") and its full NAME with the
    legal description ("Ervings location", "Atkinson and Gilmanton Academy
    grant"). "County subdivisions not defined" (water) is not a place."""
    resp = await client.get(_COUSUB_URL, params={
        "where": f"STATE='{_FIPS[state]}'", "outFields": "BASENAME,NAME",
        "returnGeometry": "false", "f": "json", "resultRecordCount": 5000,
    })
    resp.raise_for_status()
    names: list[str] = []
    for feature in resp.json()["features"]:
        attrs = feature["attributes"]
        for name in (attrs.get("BASENAME"), attrs.get("NAME")):
            if name and "not defined" not in name and name not in names:
                names.append(name)
    return names


def _tokens(name: str) -> list[str]:
    """Comparison tokens: "&" read as "and", apostrophes and a possessive
    or plural "s" dropped (the Census writes "Ervings location" where the
    state prints "Erving's Location"), case folded. A token ending in "."
    stays marked as an abbreviation."""
    name = name.replace("&", " and ").replace("\u2019", "'").replace("'", "")
    out = []
    for word in name.split():
        abbreviated = word.endswith(".")
        word = word.strip(".,").casefold()
        if not abbreviated and len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        out.append(word + ("." if abbreviated else ""))
    return out


def _abbreviates(short: str, word: str) -> bool:
    """Whether `short` (an abbreviation, without its ".") could stand for
    `word`: same first letter, its letters in order -- a prefix ("N." for
    North) or a contraction ("Gt." for Grant)."""
    if not short or not word or short[0] != word[0]:
        return False
    rest = iter(word[1:])
    return all(ch in rest for ch in short[1:])


def _same(printed: list[str], official: list[str]) -> bool:
    return len(printed) == len(official) and all(
        _abbreviates(p[:-1], o) if p.endswith(".") else p == o for p, o in zip(printed, official)
    )


def _resolve(printed: str, places: list[str]) -> str:
    """The official place name `printed` refers to. Tried in order, the
    first that yields exactly one name wins: the whole official name, word
    for word (an abbreviated word matching a word it abbreviates, see _abbreviates);
    then the official name without its last word, which is the legal
    description in a NAME ("Agawam Town" is Agawam, "Thompson and
    Meserves purchase" is what New Hampshire prints as "Thompson &
    Meserve's"). Raises when none or several match."""
    ward = _WARD_RE.match(printed)
    if ward:
        return f"{_resolve(ward.group('place'), places)} {ward.group('ward')}"
    want = _tokens(printed)
    for trim in (0, 1):
        found = sorted({
            " ".join(place.split()[: len(place.split()) - trim])
            for place in places
            if len(place.split()) > trim and _same(want, _tokens(place)[: len(_tokens(place)) - trim])
        })
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise SystemExit(f"{printed!r} is ambiguous among {found}")
    raise SystemExit(f"{printed!r} matches no official place")


def _places(names: list[str]) -> list[str]:
    """Unique place names in order, without totals or blank rows."""
    out: list[str] = []
    for name in names:
        name = " ".join((name or "").split())
        if not name or "total" in name.lower() or name in out:
            continue
        out.append(name)
    return out


async def new_hampshire(client: httpx.AsyncClient, year: int) -> dict[str, list[str]]:
    root = await nh._get_text(client, nh._ROOT_URL, "NH elections root")
    index_href = next(h for h, _ in nh._links(root) if re.fullmatch(rf"/{year}-state-primary-election-results", h))
    index = await nh._get_text(client, urljoin(nh._ROOT_URL, index_href), "NH results index")
    page_href = next(h for h, t in nh._links(index) if t == f"{year} Democratic State Primary")
    page = await nh._get_text(client, urljoin(nh._ROOT_URL, page_href), "NH Democratic primary page")
    out: dict[str, list[str]] = {}
    for href, text in nh._links(page):
        parsed = parse_statewide_office(text)
        if not href.lower().endswith(".xlsx") or not parsed or parsed[0] != "executive_council":
            continue
        content = await nh._get_bytes(client, urljoin(nh._ROOT_URL, href), text)
        rows = _xlsx_rows(content, skip=2) or []
        out[f"NH-executive_council-{parsed[1]}"] = _places([next(iter(r.values()), "") for r in rows])
    if len(out) != 5:
        raise SystemExit(f"NH: expected 5 Executive Council districts, found {sorted(out)}")
    return out


async def massachusetts(client: httpx.AsyncClient, year: int) -> dict[str, list[str]]:
    url = f"{ma._BASE_URL}/elections/search/year_from:{year}/year_to:{year}/stage:Primaries"
    page = await fetch_text_with_retry(client, ma._rate_limiter, url, "MA all-offices search")
    first: dict[str, str] = {}
    for election_id, office, district, _party, _word in ma._search_rows(page, year):
        parsed = parse_statewide_office(ma._statewide_label(office, district))
        if parsed and parsed[0] == "governors_council" and parsed[1]:
            first.setdefault(parsed[1], election_id)
    out: dict[str, list[str]] = {}
    for district, election_id in sorted(first.items(), key=lambda kv: int(kv[0])):
        resp = await client.get(
            f"{ma._BASE_URL}/elections/download/{election_id}/precincts_include:0/",
            headers={"User-Agent": BOT_USER_AGENT},
        )
        resp.raise_for_status()
        rows = list(csv.reader(io.StringIO(resp.text)))
        out[f"MA-governors_council-{district}"] = _places([r[0] for r in rows[1:] if r])
    if len(out) != 8:
        raise SystemExit(f"MA: expected 8 Governor's Council districts, found {sorted(out)}")
    return out


async def main(year: int) -> None:
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        districts = {**await new_hampshire(client, year), **await massachusetts(client, year)}
        places = {state: await official_places(client, state) for state in _FIPS}
    districts = {
        key: sorted({_resolve(name, places[key[:2]]) for name in names})
        for key, names in districts.items()
    }
    OUT.write_text(json.dumps({
        "_source": (
            f"Official {year} primary results for each district contest, read {date.today().isoformat()}: "
            "the New Hampshire Secretary of State's 'Executive Council District N' workbooks "
            "(sos.nh.gov, town and ward rows) and the Massachusetts Secretary of the Commonwealth's "
            "PD43+ Governor's Council results by city/town (electionstats.state.ma.us). "
            "Each printed name resolved to its official county-subdivision name from the "
            "Census Bureau's TIGERweb (listed in officialPlaces). "
            "Built by scripts/fetch_statewide_district_towns.py; rerun after a redistricting."
        ),
        "districts": districts,
        # Every name a district entry may use (a ward is its city's name
        # plus "Ward N"), so a test can check the file without the network.
        "officialPlaces": {
            state: sorted({
                " ".join(p.split()[: len(p.split()) - trim]) for p in names for trim in (0, 1)
                if len(p.split()) > trim
            })
            for state, names in places.items()
        },
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(districts)} districts to {OUT}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026))
