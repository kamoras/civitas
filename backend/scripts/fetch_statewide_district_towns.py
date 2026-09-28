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

from app.pipeline.fetch import state_candidates_ma as ma  # noqa: E402
from app.pipeline.fetch import state_candidates_nh as nh  # noqa: E402
from app.pipeline.fetch.http_utils import fetch_text_with_retry  # noqa: E402
from app.pipeline.fetch.state_candidates_common import parse_statewide_office  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "statewide_district_towns.json"


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
            headers={"User-Agent": "Mozilla/5.0"},
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
    OUT.write_text(json.dumps({
        "_source": (
            f"Official {year} primary results for each district contest, read {date.today().isoformat()}: "
            "the New Hampshire Secretary of State's 'Executive Council District N' workbooks "
            "(sos.nh.gov, town and ward rows) and the Massachusetts Secretary of the Commonwealth's "
            "PD43+ Governor's Council results by city/town (electionstats.state.ma.us). "
            "Built by scripts/fetch_statewide_district_towns.py; rerun after a redistricting."
        ),
        "districts": districts,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(districts)} districts to {OUT}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026))
