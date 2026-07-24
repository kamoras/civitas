"""Compute each state's Cook PVI from raw presidential returns.

Writes app/data/state_pvi.json, which _calc_constituent_alignment reads
(via _state_pvi()) as the seat expectation for SENATORS — the state-level
analog of district_pvi.json / fetch_district_pvi.py (which covers House
seats). Same "ST" -> signed int convention, positive = R lean, negative =
D lean.

Unlike fetch_district_pvi.py, which scrapes Cook's *published* PVI integer
out of Wikipedia infoboxes, this script COMPUTES the PVI from underlying
election data, so the value traces back to real vote counts rather than a
transcribed number:

    Cook PVI methodology — the state's Democratic share of the TWO-PARTY
    presidential vote, averaged over the last two presidential elections,
    minus the national two-party Democratic share over those same
    elections. Positive result => the state ran more Democratic than the
    nation (D lean); we negate so positive = R lean, matching the rest of
    this codebase.

        pvi_D(state) = mean over {2020, 2024} of
                         [ state_two_party_D_share - national_two_party_D_share ]
        STATE_PVI(state) = -round(pvi_D * 100)

This is the standard, widely-reproduced PVI formula, over the same
2020+2024 window Cook's current (2025) published PVIs use — re-windowed
from 2016+2020 in 2026-07 (platform review F6) when the midterm-elections
feature made this number public: presenting a 2016+2020 lean on a page
titled "2026 midterm elections" was two cycles stale, and inconsistent
with district_pvi.json's 2020+2024-window figures shown beside it.

Data sources (network required to regenerate):

  2020 (canonical): MIT Election Data & Science Lab (MEDSL),
    "U.S. President 1976-2020" state-level returns, version 20210113
    (Harvard Dataverse doi:10.7910/DVN/42MVDX). The Dataverse download API
    is frequently blocked by outbound proxies, so we fetch a byte-identical
    mirror of the same file pinned to an immutable commit hash.

  2024: MEDSL has not published a state-level 2024 returns file in a
    proxy-reachable location (their 2024 repo is precinct-level, the
    Dataverse API is blocked), so 2024 comes from the tonmcg
    county-level presidential results dataset (compiled from official
    county canvasses; the de-facto standard public aggregation), summed
    to state level. Its trustworthiness is not assumed — it is GATED:
    this script also sums the same dataset's 2020 file and requires the
    resulting two-party D share to agree with MEDSL's canonical 2020
    figure within 0.3pp for every state before the 2024 numbers are
    accepted (measured agreement at adoption: worst state 0.21pp).

Fidelity gates (all must pass or the script exits 1 without trusting the
output): 51 jurisdictions, plausible range/lean-split, per-state
cross-source 2020 agreement, and per-state continuity against the
previously committed file (a swapped D/R column or sign flip moves nearly
every state by far more than one election cycle ever does).

Run from the repo:
    python3 backend/scripts/fetch_state_pvi.py [output.json]

Exits 1 if any ingestion/fidelity gate fails.
"""

import csv
import io
import json
import pathlib
import sys
import urllib.request

# MEDSL 1976-2020 president state-level returns (version 20210113), pinned
# to an immutable commit so a regeneration years from now fetches the exact
# same file. Byte-identical to the Harvard Dataverse original
# (doi:10.7910/DVN/42MVDX); see module docstring.
MEDSL_2020_URL = (
    "https://raw.githubusercontent.com/highcharts/highcharts/"
    "90063e89a89d1a7ee84651170d1e976cf4489616/samples/data/"
    "us-2008-2020-president.csv"
)

# tonmcg county-level presidential results (official county canvasses),
# pinned to an immutable commit. 2024 is the scored input; 2020 exists
# only to gate the dataset against MEDSL's canonical 2020 numbers.
_TONMCG_BASE = (
    "https://raw.githubusercontent.com/tonmcg/US_County_Level_Election_Results_08-24/"
    "8cccc82f9235dced94a76d143c59a43f4a6bf979/"
)
COUNTY_2024_URL = _TONMCG_BASE + "2024_US_County_Level_Presidential_Results.csv"
COUNTY_2020_URL = _TONMCG_BASE + "2020_US_County_Level_Presidential_Results.csv"

SOURCE_DESC = (
    "2020: MIT Election Data & Science Lab (MEDSL) U.S. President state-level "
    "returns v20210113 (doi:10.7910/DVN/42MVDX). 2024: tonmcg county-level "
    "presidential results (official county canvasses) summed to state level, "
    "cross-validated against MEDSL for 2020 (<=0.3pp per state, gated). "
    "Two-party vote shares, Cook PVI formula. Regenerate with "
    "backend/scripts/fetch_state_pvi.py."
)

WINDOW = "2020+2024"
AS_OF = "2026-07-24"  # retrieval date of the pinned sources above

CYCLES = ("2020", "2024")

# Max per-state divergence (percentage points of two-party D share)
# tolerated between MEDSL's and the county dataset's 2020 numbers before
# the county dataset is rejected as a 2024 source.
CROSS_SOURCE_TOLERANCE_PP = 0.3

# Max per-state PVI movement tolerated vs. the previously committed file
# (2016+2020 window -> 2020+2024 window). One shared election plus one new
# one moves states a few points (measured max at adoption: 3); a swapped
# column or sign flip moves most of the map by tens of points.
CONTINUITY_TOLERANCE = 5

UA = {"User-Agent": "CivitasCivicPlatform/1.0 (state PVI ingestion; contact: mack.ryanm@gmail.com)"}

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "state_pvi.json"

STATE_PO = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN",
    "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8")


def fetch_medsl_2020() -> dict[str, dict[str, int]]:
    """{"ST": {"D": votes, "R": votes}} for 2020 from the MEDSL file."""
    counts: dict[str, dict[str, int]] = {}
    for row in csv.DictReader(io.StringIO(_get(MEDSL_2020_URL))):
        if row["year"].strip() != "2020":
            continue
        party = row["party_simplified"].strip().upper()
        key = "D" if party == "DEMOCRAT" else "R" if party == "REPUBLICAN" else None
        if key is None:
            continue
        st = row["state_po"].strip().upper()
        try:
            votes = int(row["candidatevotes"])
        except (ValueError, KeyError):
            continue
        counts.setdefault(st, {"D": 0, "R": 0})[key] += votes
    return counts


def fetch_county_sums(url: str) -> dict[str, dict[str, int]]:
    """{"ST": {"D": votes, "R": votes}} by summing a tonmcg county CSV."""
    counts: dict[str, dict[str, int]] = {}
    for row in csv.DictReader(io.StringIO(_get(url))):
        st = STATE_PO.get(row["state_name"].strip())
        if st is None:
            # Unknown jurisdiction label — surface it; silently dropping a
            # renamed state would corrupt the national baseline.
            print(f"WARNING: unmapped state_name {row['state_name']!r} — skipped")
            continue
        bucket = counts.setdefault(st, {"D": 0, "R": 0})
        try:
            bucket["D"] += int(float(row["votes_dem"]))
            bucket["R"] += int(float(row["votes_gop"]))
        except (ValueError, KeyError):
            continue
    return counts


def _two_party_d(counts: dict[str, dict[str, int]]) -> dict[str, float]:
    out = {}
    for st, dr in counts.items():
        total = dr["D"] + dr["R"]
        if total > 0:
            out[st] = dr["D"] / total
    return out


def compute_pvi(by_cycle: dict[str, dict[str, dict[str, int]]]) -> dict[str, int]:
    """Cook PVI per state (positive = R lean). See module docstring."""
    nat_tp_d = {}
    for y in CYCLES:
        nat = {"D": 0, "R": 0}
        for dr in by_cycle[y].values():
            nat["D"] += dr["D"]
            nat["R"] += dr["R"]
        nat_tp_d[y] = nat["D"] / (nat["D"] + nat["R"])

    states = set()
    for y in CYCLES:
        states |= set(by_cycle[y])

    out: dict[str, int] = {}
    for st in sorted(states):
        margins = []
        ok = True
        for y in CYCLES:
            c = by_cycle[y].get(st)
            if not c or (c["D"] + c["R"]) == 0:
                ok = False
                break
            margins.append(c["D"] / (c["D"] + c["R"]) - nat_tp_d[y])
        if ok:
            out[st] = -round(sum(margins) / len(margins) * 100)
    return out


def cross_source_gate(
    medsl_2020: dict[str, dict[str, int]], county_2020: dict[str, dict[str, int]],
) -> list[str]:
    """Reject the county dataset unless its 2020 two-party shares agree
    with MEDSL's canonical 2020 figures for every state."""
    failures = []
    medsl_share = _two_party_d(medsl_2020)
    county_share = _two_party_d(county_2020)
    for st, canonical in sorted(medsl_share.items()):
        if st not in county_share:
            failures.append(f"cross-source: {st} missing from county 2020 data")
            continue
        diff_pp = abs(canonical - county_share[st]) * 100
        if diff_pp > CROSS_SOURCE_TOLERANCE_PP:
            failures.append(
                f"cross-source: {st} 2020 two-party D share diverges "
                f"{diff_pp:.2f}pp from MEDSL (> {CROSS_SOURCE_TOLERANCE_PP}pp) "
                "— county dataset not trustworthy for 2024"
            )
    return failures


def ingestion_gates(pvi: dict[str, int], previous: dict[str, int] | None) -> list[str]:
    """Structural + fidelity checks — guard the ingestion, not the scores:
    a sign flip or swapped column would silently invert every senator's
    seat expectation downstream."""
    failures = []
    if len(pvi) != 51:
        failures.append(f"expected 51 jurisdictions (50 states + DC), got {len(pvi)}")
    if not all(-50 <= v <= 50 for v in pvi.values()):
        failures.append("PVI outside plausible +/-50 range — parse drift?")
    r_lean = sum(1 for v in pvi.values() if v > 0)
    d_lean = sum(1 for v in pvi.values() if v < 0)
    if not (18 <= r_lean <= 32 and 18 <= d_lean <= 32):
        failures.append(f"implausible state lean split R={r_lean} D={d_lean}")
    # DC is structurally the most Democratic jurisdiction by a wide margin
    # in every modern cycle — the cheapest possible sign-flip detector.
    if pvi.get("DC", 0) > -30:
        failures.append(f"DC computed {pvi.get('DC')} — expected strongly negative (D)")
    if previous:
        for st, old in sorted(previous.items()):
            if st not in pvi:
                failures.append(f"continuity: {st} present before, missing now")
            elif abs(pvi[st] - old) > CONTINUITY_TOLERANCE:
                failures.append(
                    f"continuity: {st} moved {old:+d} -> {pvi[st]:+d} "
                    f"(> {CONTINUITY_TOLERANCE} — column swap/sign flip?)"
                )
    return failures


def main() -> int:
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    previous: dict[str, int] | None = None
    if output.exists():
        try:
            previous = json.load(open(output)).get("states")
        except (ValueError, OSError):
            previous = None

    medsl_2020 = fetch_medsl_2020()
    county_2020 = fetch_county_sums(COUNTY_2020_URL)
    county_2024 = fetch_county_sums(COUNTY_2024_URL)

    failures = cross_source_gate(medsl_2020, county_2020)
    pvi = compute_pvi({"2020": medsl_2020, "2024": county_2024})

    vals = list(pvi.values())
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    even = sum(1 for v in vals if v == 0)
    print(f"computed {len(pvi)} jurisdictions: R-leaning {r_lean}, D-leaning "
          f"{d_lean}, EVEN {even}, min {min(vals)}, max {max(vals)}")

    failures += ingestion_gates(pvi, previous)
    for f in failures:
        print("GATE FAILED:", f)
    if failures:
        print("NOT writing output — gates failed")
        return 1

    json.dump(
        {
            "_source": SOURCE_DESC,
            "_method": (
                "STATE_PVI(st) = -round(100 * mean over {2020,2024} of "
                "[state two-party D share - national two-party D share]); "
                "positive = R lean. Same window as Cook Political Report's "
                "current (2025) published PVIs."
            ),
            "_sign": "positive = R lean, negative = D lean (matches district_pvi.json)",
            "_window": WINDOW,
            "_as_of": AS_OF,
            "states": pvi,
        },
        open(output, "w"),
        indent=1,
        sort_keys=True,
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
