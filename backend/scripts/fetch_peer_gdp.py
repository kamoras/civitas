"""Regenerate app/data/peer_gdp_per_capita.json: real GDP per person for the
US and its peer economies, 1946 to the release's last year, from the newest
Maddison Project Database release.

THE RELEASE, by rule: DataverseNL's search for datasets named "Maddison
Project Database <year>", the newest year; its latest published version's
spreadsheet; within it, the sheet whose header row names countrycode, year
and gdppc, read by those headers. The citation is the dataset's own
(its publication citation, DOI, file and version), so a new release needs
no edit here. The 2023 release (Bolt & van Zanden 2024, doi:10.34894/INZBF2,
mpd2023_web.xlsx) was the first read this way.

app/pipeline/fetch/peer_gdp.py takes growth and every year's income gap with
the US from it through its last year, and the World Bank's live series only
after: the gap needs levels at purchasing-power parity (the World Bank's
constant-dollar series converts at 2015 exchange rates; its PPP series starts
in 1990). It is history, so it changes only with a new Maddison release.

THE PEERS, by a stated rule: every member of the OECD before its 1994
enlargement, the US aside. The OECD's membership was the twenty signatories
of the 1960 Convention (in force 30 September 1961) plus four accessions,
and then unchanged from 1973 until Mexico joined in 1994: the group of rich
market economies that met the postwar decades' shocks together, which is
what the comparison removes. The dates are the OECD's own record of each
country's deposit of its instrument of ratification or accession
(oecd.org, "Members and partners"; the Convention's depositary list). The
list is written into the data file with the rule, and
app/pipeline/fetch/peer_gdp.py reads it from there. Until 2026-10 the peers
were thirteen countries a research note had picked.

Run by app/data_refresh.py monthly (network required):
    cd backend && PYTHONPATH=. python3 scripts/fetch_peer_gdp.py [OUT]
"""

import datetime
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

from app.contact import USER_AGENT
from app.pipeline.fetch.state_candidates_tabular import xlsx_sheet_files, xlsx_sheet_rows
US = "USA"  # app.pipeline.fetch.peer_gdp.US; not imported, since that module reads the file this writes

# Each OECD member's date of becoming a member (its deposit of the
# instrument of ratification of, or accession to, the OECD Convention; for
# the twenty founders, the Convention's entry into force), as the OECD
# records it. Only the members before PEER_RULE_BEFORE are listed: a
# member that joined later can't change the peers.
OECD_MEMBERSHIP = {
    "AUT": "1961-09-30", "BEL": "1961-09-30", "CAN": "1961-09-30", "DNK": "1961-09-30",
    "FRA": "1961-09-30", "DEU": "1961-09-30", "GRC": "1961-09-30", "ISL": "1961-09-30",
    "IRL": "1961-09-30", "ITA": "1961-09-30", "LUX": "1961-09-30", "NLD": "1961-09-30",
    "NOR": "1961-09-30", "PRT": "1961-09-30", "ESP": "1961-09-30", "SWE": "1961-09-30",
    "CHE": "1961-09-30", "TUR": "1961-09-30", "GBR": "1961-09-30", "USA": "1961-09-30",
    "JPN": "1964-04-28", "FIN": "1969-01-28", "AUS": "1971-06-07", "NZL": "1973-05-29",
}
# The first enlargement after 1973 (Mexico, 1994-05-18): the peers are the
# members before it.
PEER_RULE_BEFORE = "1994-05-18"
PEER_RULE = ("Every member of the OECD before its 1994 enlargement (the 20 signatories of the 1960 "
             "Convention and Japan, Finland, Australia and New Zealand), the US aside; membership dates "
             "from the OECD's record of ratifications and accessions.")


def peer_countries() -> tuple[str, ...]:
    """The peers by PEER_RULE, in ISO 3166 alpha-3 order."""
    return tuple(sorted(c for c, joined in OECD_MEMBERSHIP.items() if joined < PEER_RULE_BEFORE and c != US))

DATAVERSE = "https://dataverse.nl"
RELEASE_NAME = re.compile(r"^Maddison Project Database (\d{4})$")
# dataverse.nl challenges browser-style User-Agents (an Anubis proof-of-work
# page, served with a 200); USER_AGENT names Civitas, not a browser, and is
# let through.
FIRST_YEAR = 1946  # a base for 1947, where Effectiveness's postwar comparison begins
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "peer_gdp_per_capita.json"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _api(path: str, **params) -> dict:
    return json.loads(_get(f"{DATAVERSE}/api/{path}?{urllib.parse.urlencode(params)}"))["data"]


def newest_release(items: list[dict]) -> tuple[int, str]:
    """(year, DOI) of the newest dataset named "Maddison Project Database <year>"."""
    releases = [(int(m[1]), i["global_id"]) for i in items if (m := RELEASE_NAME.match(i.get("name", "").strip()))]
    if not releases:
        raise SystemExit("DataverseNL's search lists no Maddison Project Database release")
    return max(releases)


def release_file(version: dict) -> tuple[int, str, str, str]:
    """(file id, file name, version, citation) of a dataset version's spreadsheet."""
    sheets = [f["dataFile"] for f in version.get("files", [])
              if f["dataFile"].get("filename", "").lower().endswith(".xlsx")]
    if len(sheets) != 1:
        raise SystemExit(f"expected one spreadsheet in the release, found {[s.get('filename') for s in sheets]}")
    citation = ""
    for field in version.get("metadataBlocks", {}).get("citation", {}).get("fields", []):
        if field.get("typeName") == "publication":
            citation = next((p.get("publicationCitation", {}).get("value", "") for p in field["value"]), "")
    return (sheets[0]["id"], sheets[0]["filename"], f"{version['versionNumber']}.{version['versionMinorNumber']}",
            citation)


def per_capita_rows(body: bytes, wanted: set[str]):
    """(country, year, GDP per person) from the sheet whose first row names
    countrycode, year and gdppc, read by those headers (standard library)."""
    for sheet in (xlsx_sheet_files(body) or {}).values():
        rows = xlsx_sheet_rows(body, sheet) or []
        names = [c.strip().lower() for c in rows[0]] if rows else []
        if not {"countrycode", "year", "gdppc"} <= set(names):
            continue
        code, year, gdppc = (names.index(n) for n in ("countrycode", "year", "gdppc"))
        for row in rows[1:]:
            cells = row + [""] * (len(names) - len(row))
            if cells[code] in wanted and cells[year] and cells[gdppc]:
                yield cells[code], int(float(cells[year])), float(cells[gdppc])
        return
    raise SystemExit("no sheet names countrycode, year and gdppc")


def main() -> None:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    year, doi = newest_release(_api("search", q='"Maddison Project Database"', type="dataset", per_page=50)["items"])
    file_id, filename, version, citation = release_file(
        _api("datasets/:persistentId/versions/:latest-published", persistentId=doi))
    body = _get(f"{DATAVERSE}/api/access/datafile/{file_id}")
    if not body.startswith(b"PK"):
        raise SystemExit(f"datafile {file_id} did not return the spreadsheet: {body[:120]!r}")
    peers = peer_countries()
    wanted = {US, *peers}
    countries: dict[str, dict[str, float]] = {}
    for code, y, gdppc in per_capita_rows(body, wanted):
        if y >= FIRST_YEAR:
            countries.setdefault(code, {})[str(y)] = gdppc
    # The release's last year is the last one every economy has.
    last_year = min(max(int(y) for y in years) for years in countries.values()) if countries else 0
    countries = {c: {y: v for y, v in years.items() if int(y) <= last_year} for c, years in countries.items()}
    # Every economy runs without a gap from its first year to the last; the
    # US from FIRST_YEAR. A peer Maddison starts later (Luxembourg, 1950)
    # joins the median from then: peer_gdp takes the median of the peers
    # with figures, given a majority of them.
    first = {c: min(int(y) for y in years) for c, years in countries.items()}
    missing = sorted(c for c in wanted if c not in countries or len(countries[c]) != last_year - first[c] + 1)
    if missing or first.get(US) != FIRST_YEAR:
        raise SystemExit(f"incomplete Maddison coverage for {missing or [US]}")
    late = {c: y for c, y in sorted(first.items()) if y > FIRST_YEAR}
    out.write_text(json.dumps({
        "_as_of": datetime.date.today().isoformat(),
        "_source": (
            f"Maddison Project Database {year}" + (f" ({citation})" if citation else "") + ", GDP per capita, "
            f"dataverse.nl {doi} file {filename} version {version}, years {FIRST_YEAR}-{last_year}. Written by "
            "backend/scripts/fetch_peer_gdp.py; app/pipeline/fetch/peer_gdp.py takes growth and the income gap "
            "with the US from it through its last year, and the World Bank's live series after."
        ),
        "release": year,
        "peers": list(peers),
        "_peer_rule": PEER_RULE,
        "_first_year": late,
        "countries": {c: countries[c] for c in sorted(countries)},
    }, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(countries)} economies, Maddison {year})")


if __name__ == "__main__":
    main()
