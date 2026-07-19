"""Fetch each state's 2020 Census population from Wikipedia.

Regenerates app/data/state_population.json, the single source of truth
for state population used by:
  - score_calculator.py's _state_population() (Funding Independence's
    small-donor component, via _state_small_donor_baseline)
  - fetch_state_small_donor_baseline.py's regression-fitting audit

Previously this data was a hardcoded dict literal duplicated in both
files (2026-07 review feedback on PR #152: "state populations shouldn't
be hard coded. Should be fetched" / "this is a repeat of the same data
we used earlier. Should be centralized"). This follows the exact same
pattern fetch_district_pvi.py already established for Cook PVI: scrape a
stable public source into a checked-in JSON file, load it through a
cached in-process function, regenerate only when the source data changes
(population, unlike PVI, doesn't shift enough between censuses to need
per-cycle regeneration — rerun after the 2030 census).

Run from the repo (network required):
    python3 backend/scripts/fetch_state_population.py [output.json]

Exits 1 if any state fails to parse.
"""

import json
import pathlib
import re
import sys
import urllib.request

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

UA = {"User-Agent": "CivitasCivicPlatform/1.0 (state population ingestion; contact: mack.ryanm@gmail.com)"}
SOURCE_URL = "https://en.wikipedia.org/wiki/List_of_states_and_territories_of_the_United_States_by_population"

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "state_population.json"

# Wikipedia disambiguates these three against a country/city of the same
# name — the link's `title` attribute carries the disambiguated form even
# though the visible row text is still just the plain state name.
_DISAMBIGUATED_TITLES = {
    "Georgia": "Georgia (U.S. state)",
    "New York": "New York (state)",
    "Washington": "Washington (state)",
}

# Matches each state's row in the page's "2020 census rank" comparison
# table: the state's wikilink immediately followed by a rank cell, then
# the 2020 census population cell (comma-formatted integer). Verified
# against known figures (e.g. California 39,538,223) before trusting
# this pattern in production — Wikipedia's MediaWiki output is heavily
# templated and does not have a stable simple table structure, so this
# is intentionally strict (anchored on the exact tag sequence) rather
# than a loose "grab the next number" scan, to fail loudly (no match)
# instead of silently reading the wrong column if the page layout shifts.
_ROW_RE_TEMPLATE = (
    r'title="{title}">{name}</a></span></td>\s*'
    r'<td[^>]*><span[^>]*>\d+</span></td>\s*'
    r'<td[^>]*><span[^>]*>([\d,]+)</span></td>'
)


def fetch_state_populations() -> dict[str, float]:
    req = urllib.request.Request(SOURCE_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8")

    result: dict[str, float] = {}
    missing = []
    for abbr, name in STATE_NAMES.items():
        title = _DISAMBIGUATED_TITLES.get(name, name)
        pattern = _ROW_RE_TEMPLATE.format(title=re.escape(title), name=re.escape(name))
        m = re.search(pattern, html)
        if not m:
            missing.append(name)
            continue
        population = int(m.group(1).replace(",", ""))
        result[abbr] = round(population / 1_000_000, 1)

    if missing:
        print(f"FAILED to parse {len(missing)} states: {', '.join(missing)}")

    return result


def main() -> int:
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    result = fetch_state_populations()
    print(f"parsed {len(result)}/{len(STATE_NAMES)} states")

    if len(result) != len(STATE_NAMES):
        return 1

    json.dump(
        {
            "_source": "Wikipedia 2020 Census population by state, retrieved 2026-07-19; "
                       "regenerate with backend/scripts/fetch_state_population.py",
            "_unit": "millions, rounded to 1 decimal",
            "states": result,
        },
        open(output, "w"),
        indent=1, sort_keys=True,
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
