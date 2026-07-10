"""Fetch Cook PVI for every congressional district from Wikipedia infoboxes.

Regenerates app/data/district_pvi.json, which _calc_constituent_alignment
uses as the seat expectation for House members (senators use STATE_PVI).
Covers all 435 seats from the post-2020 apportionment — NOT the current
representatives table — so vacant seats still have a lean on file when a
special-election winner enters the pipeline.

Output maps "ST-N" -> signed int (positive = R lean, negative = D lean,
0 = EVEN), same sign convention as STATE_PVI in score_calculator.py.
At-large seats use district 0 ("AK-0"), matching _extract_district.

Run from the repo (network required):
    python3 backend/scripts/fetch_district_pvi.py [output.json]

Exits 1 if any district fails to parse or any ingestion gate fails.
"""

import json
import pathlib
import re
import sys
import time
import urllib.parse
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

# Post-2020-census apportionment (118th Congress onward), 435 seats.
SEATS = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5,
    "DE": 1, "FL": 28, "GA": 14, "HI": 2, "ID": 2, "IL": 17, "IN": 9,
    "IA": 4, "KS": 4, "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9,
    "MI": 13, "MN": 8, "MS": 4, "MO": 8, "MT": 2, "NE": 3, "NV": 4,
    "NH": 2, "NJ": 12, "NM": 3, "NY": 26, "NC": 14, "ND": 1, "OH": 15,
    "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7, "SD": 1, "TN": 9,
    "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2, "WI": 8,
    "WY": 1,
}

UA = {"User-Agent": "CivitasCivicPlatform/1.0 (district PVI ingestion; contact: mack.ryanm@gmail.com)"}
API = "https://en.wikipedia.org/w/api.php"

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "district_pvi.json"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def district_title(state: str, district: int) -> str:
    name = STATE_NAMES[state]
    possessive = f"{name}'s"
    if district == 0:
        return f"{possessive} at-large congressional district"
    return f"{possessive} {ordinal(district)} congressional district"


_PVI_RE = re.compile(r"(?i)\|\s*(?:cpvi|cook[_ ]?pvi)\s*=\s*([^\n|}]+)")
_VALUE_RE = re.compile(r"(?i)\b(EVEN|[DR]\s*\+\s*\d+)\b")


def parse_pvi(wikitext: str) -> int | None:
    m = _PVI_RE.search(wikitext)
    if not m:
        return None
    v = _VALUE_RE.search(m.group(1))
    if not v:
        return None
    raw = v.group(1).upper().replace(" ", "")
    if raw == "EVEN":
        return 0
    sign = 1 if raw.startswith("R") else -1
    return sign * int(raw.split("+")[1])


def fetch_batch(titles: list[str]) -> dict[str, str]:
    params = {
        "action": "query", "prop": "revisions", "rvprop": "content",
        "rvslots": "main", "format": "json", "redirects": "1",
        "titles": "|".join(titles),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    # map redirected titles back to what we asked for
    back = {}
    for rd in data["query"].get("redirects", []):
        back[rd["to"]] = rd["from"]
    for norm in data["query"].get("normalized", []):
        back[norm["to"]] = norm["from"]
    out = {}
    for page in data["query"]["pages"].values():
        title = page.get("title", "")
        asked = back.get(title, title)
        revs = page.get("revisions")
        if revs:
            out[asked] = revs[0]["slots"]["main"]["*"]
    return out


def ingestion_gates(result: dict[str, int]) -> list[str]:
    """Structural sanity checks on the retrieved table.

    These guard the ingestion (sign convention, coverage, parse drift),
    not the scores: a sign flip or a wrong-column parse would silently
    invert every House seat expectation downstream.
    """
    failures = []
    if len(result) != 435:
        failures.append(f"expected 435 districts, got {len(result)}")
    states = {k.split("-")[0] for k in result}
    if states != set(SEATS):
        failures.append(f"state coverage mismatch: {sorted(set(SEATS) ^ states)}")
    vals = list(result.values())
    if not all(-45 <= v <= 45 for v in vals):
        failures.append("PVI outside plausible ±45 range — parse drift?")
    # Sign convention: the national map has both leans in the hundreds;
    # a parser that flipped D/R or dropped a sign can't produce that.
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    if not (150 <= r_lean <= 285 and 150 <= d_lean <= 285):
        failures.append(f"implausible lean split R={r_lean} D={d_lean}")
    return failures


def main() -> int:
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    pairs = []
    for st, n in sorted(SEATS.items()):
        pairs.extend([(st, 0)] if n == 1 else [(st, i) for i in range(1, n + 1)])
    titles = {district_title(s, d): f"{s}-{d}" for s, d in pairs}

    result: dict[str, int] = {}
    missing: list[str] = []
    title_list = list(titles)
    for i in range(0, len(title_list), 50):
        batch = title_list[i:i + 50]
        pages = fetch_batch(batch)
        for t in batch:
            key = titles[t]
            wt = pages.get(t)
            pvi = parse_pvi(wt) if wt else None
            if pvi is None:
                missing.append(f"{key} ({t})")
            else:
                result[key] = pvi
        time.sleep(1)

    print(f"parsed {len(result)} districts, missing {len(missing)}")
    for m in missing[:20]:
        print("  MISSING:", m)

    vals = list(result.values())
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    even = sum(1 for v in vals if v == 0)
    print(f"R-leaning {r_lean}, D-leaning {d_lean}, EVEN {even}, "
          f"min {min(vals)}, max {max(vals)}")

    failures = ingestion_gates(result)
    for f in failures:
        print("GATE FAILED:", f)

    json.dump(
        {
            "_source": "Wikipedia district infoboxes (Cook PVI), retrieved 2026-07-10; regenerate with backend/scripts/fetch_district_pvi.py",
            "_sign": "positive = R lean, negative = D lean (matches STATE_PVI)",
            "districts": result,
        },
        open(output, "w"),
        indent=1, sort_keys=True,
    )
    print(f"wrote {output}")
    return 0 if not missing and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
