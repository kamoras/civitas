"""Regenerate app/data/peer_gdp_per_capita.json: real GDP per person for the
US and the 13 peer economies, 1946 to the release's last year, from the Maddison Project
Database 2023 (Bolt & van Zanden 2024, "Maddison style estimates of the
evolution of the world economy: A new 2023 update", Journal of Economic
Surveys; dataverse.nl doi:10.34894/INZBF2, file mpd2023_web.xlsx).

app/pipeline/fetch/peer_gdp.py takes growth and every year's income gap with
the US from it through its last year, and the World Bank's live series only
after: the gap needs levels at purchasing-power parity (the World Bank's
constant-dollar series converts at 2015 exchange rates; its PPP series starts
in 1990). It is history, so it changes only with a new Maddison release.

Run from the repo (network required; openpyxl from
scripts/requirements-research.txt):
    cd backend && PYTHONPATH=. python3 scripts/fetch_peer_gdp.py
"""

import datetime
import io
import json
import pathlib
import urllib.request

import openpyxl

from app.contact import CONTACT_EMAIL
from app.pipeline.fetch.peer_gdp import PEER_COUNTRIES, US

MADDISON_FILE_URL = "https://dataverse.nl/api/access/datafile/421302"  # mpd2023_web.xlsx, version 1.0
# dataverse.nl challenges browser-style User-Agents (an Anubis proof-of-work
# page, served with a 200); a client that names itself without "Mozilla" is
# let through, so this one does (BOT_USER_AGENT starts "Mozilla/5.0").
USER_AGENT = f"Civitas/1.0 (+{CONTACT_EMAIL})"
FIRST_YEAR = 1946  # a base for 1947, where Effectiveness's postwar comparison begins
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "peer_gdp_per_capita.json"


def main() -> None:
    req = urllib.request.Request(MADDISON_FILE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    if not body.startswith(b"PK"):
        raise SystemExit(f"{MADDISON_FILE_URL} did not return the spreadsheet: {body[:120]!r}")
    book = openpyxl.load_workbook(io.BytesIO(body), read_only=True)
    wanted = {US, *PEER_COUNTRIES}
    countries: dict[str, dict[str, float]] = {}
    for code, _name, _region, year, gdppc, _pop in book["Full data"].iter_rows(min_row=2, values_only=True):
        if code in wanted and year is not None and int(year) >= FIRST_YEAR and gdppc:
            countries.setdefault(code, {})[str(int(year))] = float(gdppc)
    # The release's last year is the last one every economy has.
    last_year = min(max(int(y) for y in years) for years in countries.values()) if countries else 0
    countries = {c: {y: v for y, v in years.items() if int(y) <= last_year} for c, years in countries.items()}
    missing = sorted(c for c in wanted if len(countries.get(c, {})) != last_year - FIRST_YEAR + 1)
    if missing:
        raise SystemExit(f"incomplete Maddison coverage for {missing}")
    OUT.write_text(json.dumps({
        "_as_of": datetime.date.today().isoformat(),
        "_source": (
            "Maddison Project Database 2023 (Bolt & van Zanden 2024), GDP per capita in 2011 "
            "international dollars, dataverse.nl doi:10.34894/INZBF2 file mpd2023_web.xlsx, years "
            f"{FIRST_YEAR}-{last_year}. Written by backend/scripts/fetch_peer_gdp.py; "
            "app/pipeline/fetch/peer_gdp.py takes growth and the income gap with the US from it "
            "through its last year, and the World Bank's live series after."
        ),
        "countries": {c: countries[c] for c in sorted(countries)},
    }, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(countries)} economies)")


if __name__ == "__main__":
    main()
