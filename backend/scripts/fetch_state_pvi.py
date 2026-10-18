"""Compute each state's Cook PVI from raw presidential returns.

Writes app/data/state_pvi.json, which _calc_constituent_alignment reads
(via _state_pvi()) as the seat expectation for SENATORS — the state-level
analog of district_pvi.json / fetch_district_pvi.py (which covers House
seats). Same "ST" -> signed int convention, positive = R lean, negative =
D lean.

Unlike fetch/district_pvi.py, which reads Cook's *published* district
figures from pinned Wikipedia revisions, this script COMPUTES the PVI from underlying
election data, so the value traces back to real vote counts rather than a
transcribed number:

    Cook PVI methodology — the state's Democratic share of the TWO-PARTY
    presidential vote minus the national two-party Democratic share, over
    the last two presidential elections, weighted 75% to the more recent
    and 25% to the earlier (Cook's formula since its 2022 release).
    Positive result => the state ran more Democratic than the nation
    (D lean); we negate so positive = R lean, matching the rest of this
    codebase.

        pvi_D(state) = 0.25 * [state_D_2020 - national_D_2020]
                     + 0.75 * [state_D_2024 - national_D_2024]
        STATE_PVI(state) = -round(pvi_D * 100)

The weighting is checked, not assumed: an equal-weight mean, which this
script used until 2026-10, put 18 of the 51 jurisdictions one point off
Cook's published 2025 state PVIs (Oregon D+7 against Cook's D+8); 75/25
reproduces all 51 exactly, and 70/30 or 80/20 miss four each (measured
2026-10-08 against this script's own pinned returns and the state table
of the Wikipedia revision pinned in district_pvi_sources.json). This is
over the same
2020+2024 window Cook's current (2025) published PVIs use — re-windowed
from 2016+2020 in 2026-07 (platform review F6) when the midterm-elections
feature made this number public: presenting a 2016+2020 lean on a page
titled "2026 midterm elections" was two cycles stale, and inconsistent
with district_pvi.json's 2020+2024-window figures shown beside it.

Data sources (network required to regenerate):

  2020: the Federal Election Commission's "Federal Elections 2020"
    (Table 2, 2020 presidential electoral and popular vote, by state),
    compiled from each state's certified results; a U.S. government work,
    public domain. Until 2026-10 the 2020 half came from MEDSL's
    "U.S. President 1976-2020" file (v20210113), which predates some
    states' final certified counts (New York 13,901 Biden and 7,199 Trump
    votes short; California, Indiana, North Dakota, New Hampshire, New
    Jersey and North Carolina by fewer); no state's figure changed with the
    switch. The workbook is not at an
    immutable URL, so it is gated: its states must sum to its own national
    total row, and to the national totals recorded below.

  2024: 2024-president-state.csv from MEDSL's GitHub repository
    MEDSL/2024-elections-official (released 2025-11, CC0 on Dataverse),
    transcribed from each state's certified canvass and pinned to an
    immutable commit (the FEC has not published its 2024 compilation).
    Until 2026-10 the 2024 half came from the tonmcg county dataset, whose
    2024 file is a scrape of a news site's results pages, not an official
    tabulation; MEDSL's official state file reproduces all 51 figures
    unchanged.

A 2024 candidate's votes are its party-simplified DEMOCRAT / REPUBLICAN
rows, fusion lines included (MEDSL's New York row already sums Harris's
Democratic and Working Families lines). The per-state
two-party counts are written beside the PVIs ("inputs"), so the figures
can be recomputed from the file alone (tests/test_score_calculator.py
does).

Fidelity gates (all must pass or the script exits 1 without trusting the
output): 51 jurisdictions each with a Democratic and a Republican row in
both cycles, plausible range/lean-split, and per-state continuity against
the previously committed file (a swapped D/R column or sign flip moves nearly
every state by far more than one election cycle ever does).

Run from the repo:
    python3 backend/scripts/fetch_state_pvi.py [output.json]

Exits 1 if any ingestion/fidelity gate fails.
"""

import csv
import io
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import CONTACT_NOTE  # noqa: E402

# FEC, Federal Elections 2020 (public domain). Table 2 is the workbook's
# third sheet; its columns are state, electoral votes (two), Biden, Trump,
# all others, total.
FEC_2020_URL = "https://www.fec.gov/resources/cms-content/documents/federalelections2020.xlsx"
FEC_2020_SHEET = "xl/worksheets/sheet3.xml"
# The FEC's published 2020 national totals for Biden and Trump (Table 2's
# "Total:" row), a citable fact: the gate that the sheet read is that table.
FEC_2020_NATIONAL = {"D": 81_283_501, "R": 74_223_975}

# MEDSL's official 2024 state-level presidential returns, pinned to the
# commit that last changed the file ("Fixed issue with VT state-level
# return party labels", 2025-11-20).
MEDSL_2024_URL = (
    "https://raw.githubusercontent.com/MEDSL/2024-elections-official/"
    "f67c9de44cc31e0faa4b94986ba0a01afa3708b3/2024-president-state.csv"
)

SOURCE_DESC = (
    "2020: Federal Election Commission, Federal Elections 2020, Table 2 (state "
    "presidential popular vote, from certified results; public domain). 2024: MEDSL 2024 official "
    "state-level presidential returns (github.com/MEDSL/2024-elections-official, "
    "2024-president-state.csv, CC0). Two-party vote shares, Cook PVI formula."
)
# For maintainers, kept out of _source: the site shows _source as the
# figure's provenance (PviMethodologyNote), where a regenerate command
# is noise.
REGENERATE = "backend/scripts/fetch_state_pvi.py"

WINDOW = "2020+2024"
AS_OF = "2026-10-15"  # last regenerated from the pinned sources above

CYCLES = ("2020", "2024")

# Cook's weights for the two elections, most recent heaviest (module
# docstring). A methodology fact, not a fitted value.
CYCLE_WEIGHTS = {"2020": 0.25, "2024": 0.75}

# Max per-state PVI movement tolerated vs. the previously committed file
# (2016+2020 window -> 2020+2024 window). One shared election plus one new
# one moves states a few points (measured max at adoption: 3); a swapped
# column or sign flip moves most of the map by tens of points.
CONTINUITY_TOLERANCE = 5

UA = {"User-Agent": f"CivitasCivicPlatform/1.0 (state PVI ingestion{CONTACT_NOTE})"}

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "state_pvi.json"

_XL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _fec_2020(raw: bytes) -> dict[str, dict[str, int]]:
    """{"ST": {"D": Biden, "R": Trump}} from the FEC's Table 2 sheet. Raises
    unless the 51 states sum to the sheet's own "Total:" row and that row
    is the FEC's published national total."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        shared = [
            "".join(t.text or "" for t in si.iter(f"{_XL}t"))
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml"))
        ]
        sheet = ET.fromstring(zf.read(FEC_2020_SHEET))
    counts, total = {}, None
    for row in sheet.iter(f"{_XL}row"):
        cells = {}
        for c in row.iter(f"{_XL}c"):
            v = c.find(f"{_XL}v")
            if v is not None and v.text is not None:
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                cells[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
        label = cells.get("A", "").strip().rstrip("*")  # "ME*", "NE*": split electoral votes
        try:
            d, r = int(float(cells["D"])), int(float(cells["E"]))
        except (KeyError, ValueError):
            continue
        if re.fullmatch(r"[A-Z]{2}", label):
            counts[label] = {"D": d, "R": r}
        elif label.startswith("Total"):
            total = {"D": d, "R": r}
    summed = {p: sum(v[p] for v in counts.values()) for p in ("D", "R")}
    if len(counts) != 51 or total != summed or total != FEC_2020_NATIONAL:
        raise SystemExit(f"FEC 2020 table: {len(counts)} states summing to {summed}, total row {total}, "
                         f"published {FEC_2020_NATIONAL}")
    return counts


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def _get(url: str) -> str:
    return _get_bytes(url).decode("utf-8")


def _medsl_two_party(text: str, year: str) -> dict[str, dict[str, int]]:
    """{"ST": {"D": votes, "R": votes}} from MEDSL's state-level returns CSV.
    The writein flag is not read: the 2024 release has no Democratic or
    Republican write-in rows."""
    counts: dict[str, dict[str, int]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row["year"].strip() != year:
            continue
        party = row["party_simplified"].strip().upper()
        key = "D" if party == "DEMOCRAT" else "R" if party == "REPUBLICAN" else None
        if key is None:
            continue
        st = row["state_po"].strip().upper()
        try:
            votes = int(row["votes"])
        except (ValueError, KeyError):
            continue
        counts.setdefault(st, {"D": 0, "R": 0})[key] += votes
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
            out[st] = -round(sum(CYCLE_WEIGHTS[y] * m for y, m in zip(CYCLES, margins)) * 100)
    return out


def ingestion_gates(
    pvi: dict[str, int], previous: dict[str, int] | None,
    by_cycle: dict[str, dict[str, dict[str, int]]],
) -> list[str]:
    """Structural + fidelity checks — guard the ingestion, not the scores:
    a sign flip or swapped column would silently invert every senator's
    seat expectation downstream."""
    failures = []
    for y in CYCLES:
        for st, dr in sorted(by_cycle[y].items()):
            if not dr["D"] or not dr["R"]:
                failures.append(f"{y}: {st} has no {'Democratic' if not dr['D'] else 'Republican'} votes")
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

    by_cycle = {
        "2020": _fec_2020(_get_bytes(FEC_2020_URL)),
        "2024": _medsl_two_party(_get(MEDSL_2024_URL), "2024"),
    }
    pvi = compute_pvi(by_cycle)

    vals = list(pvi.values())
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    even = sum(1 for v in vals if v == 0)
    print(f"computed {len(pvi)} jurisdictions: R-leaning {r_lean}, D-leaning "
          f"{d_lean}, EVEN {even}, min {min(vals)}, max {max(vals)}")

    failures = ingestion_gates(pvi, previous, by_cycle)
    for f in failures:
        print("GATE FAILED:", f)
    if failures:
        print("NOT writing output — gates failed")
        return 1

    json.dump(
        {
            "_source": SOURCE_DESC,
            "_regenerate": REGENERATE,
            "_method": (
                "STATE_PVI(st) = -round(100 * (0.25 * d2020 + 0.75 * d2024)), "
                "d = state two-party D share - national two-party D share; "
                "positive = R lean. Cook Political Report's formula and window "
                "for its current (2025) published PVIs, all 51 of which it "
                "reproduces."
            ),
            "_sign": "positive = R lean, negative = D lean (matches district_pvi.json)",
            "_window": WINDOW,
            "_as_of": AS_OF,
            "inputs": {y: {st: by_cycle[y][st] for st in pvi} for y in CYCLES},
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
