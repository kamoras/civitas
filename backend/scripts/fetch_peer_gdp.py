"""Regenerate app/data/peer_gdp_per_capita.json: real GDP per person for the
US and its peer economies, 1946 to the release's last year, from the Maddison Project
Database 2023 (Bolt & van Zanden 2024, "Maddison style estimates of the
evolution of the world economy: A new 2023 update", Journal of Economic
Surveys; dataverse.nl doi:10.34894/INZBF2, file mpd2023_web.xlsx).

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

from app.contact import CONTACT_COMMENT
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

MADDISON_FILE_URL = "https://dataverse.nl/api/access/datafile/421302"  # mpd2023_web.xlsx, version 1.0
# dataverse.nl challenges browser-style User-Agents (an Anubis proof-of-work
# page, served with a 200); a client that names itself without "Mozilla" is
# let through, so this one does (BOT_USER_AGENT starts "Mozilla/5.0").
USER_AGENT = f"Civitas/1.0{CONTACT_COMMENT}"
FIRST_YEAR = 1946  # a base for 1947, where Effectiveness's postwar comparison begins
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "peer_gdp_per_capita.json"


def main() -> None:
    req = urllib.request.Request(MADDISON_FILE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    if not body.startswith(b"PK"):
        raise SystemExit(f"{MADDISON_FILE_URL} did not return the spreadsheet: {body[:120]!r}")
    book = openpyxl.load_workbook(io.BytesIO(body), read_only=True)
    peers = peer_countries()
    wanted = {US, *peers}
    countries: dict[str, dict[str, float]] = {}
    for code, _name, _region, year, gdppc, _pop in book["Full data"].iter_rows(min_row=2, values_only=True):
        if code in wanted and year is not None and int(year) >= FIRST_YEAR and gdppc:
            countries.setdefault(code, {})[str(int(year))] = float(gdppc)
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
    OUT.write_text(json.dumps({
        "_as_of": datetime.date.today().isoformat(),
        "_source": (
            "Maddison Project Database 2023 (Bolt & van Zanden 2024), GDP per capita in 2011 "
            "international dollars, dataverse.nl doi:10.34894/INZBF2 file mpd2023_web.xlsx, years "
            f"{FIRST_YEAR}-{last_year}. Written by backend/scripts/fetch_peer_gdp.py; "
            "app/pipeline/fetch/peer_gdp.py takes growth and the income gap with the US from it "
            "through its last year, and the World Bank's live series after."
        ),
        "peers": list(peers),
        "_peer_rule": PEER_RULE,
        "_first_year": late,
        "countries": {c: countries[c] for c in sorted(countries)},
    }, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(countries)} economies)")


if __name__ == "__main__":
    main()
