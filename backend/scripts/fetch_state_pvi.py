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

Data sources (network required to regenerate), found by rule, never typed
per cycle, so the window advances by itself (data_refresh runs this
script monthly):

  The two cycles are the two newest presidential elections an official
  compilation covers: the newest version of MEDSL's "U.S. President
  1976-<year>" dataset on Harvard Dataverse (doi:10.7910/DVN/42MVDX, CC0,
  transcribed from each state's certified canvass) and the FEC's Federal
  Elections compilations its results index links (public domain).

  Each cycle is read from the FEC's compilation (Table 2, the state
  presidential popular vote, from certified results) when the FEC has
  published it, else from MEDSL's file. Until 2026-10 the 2020 half came
  from MEDSL's 1976-2020 file (v20210113), which predated some states'
  final certified counts (New York 13,901 Biden and 7,199 Trump votes
  short); no state's figure changed with the switch to the FEC. MEDSL's
  newest file (1976-2024) reproduces the 2024 counts of the
  MEDSL/2024-elections-official repository this script read until
  2026-10.

  An FEC table is gated: its states must sum to its own "Total:" row, its
  Democratic and Republican columns are read by their own "(D)" and "(R)"
  labels (the 2016 table lists Trump first, the 2020 table Biden), and every
  state's two-party share must agree with MEDSL's for the same cycle to
  within FEC_MEDSL_TOLERANCE.

A candidate's votes are every ballot line in the state carrying their
name (fit_forecast_model.presidential_totals: New York's fusion lines sum).
The per-state two-party counts are written beside the PVIs ("inputs"), so
the figures can be recomputed from the file alone
(tests/test_score_calculator.py does).

Fidelity gates (all must pass or the script exits 1 without trusting the
output): 51 jurisdictions each with a Democratic and a Republican count in
both cycles, plausible range/lean-split, and, when the output path holds a
previous file, per-state continuity against it (a swapped D/R column or
sign flip moves nearly every state by far more than one election cycle ever
does; under data_refresh the same check is its gate against the copy in
use).

Run from the repo:
    python3 backend/scripts/fetch_state_pvi.py [output.json]

Exits 1 if any ingestion/fidelity gate fails.
"""

import io
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.time_utils import utcnow  # noqa: E402
from fit_forecast_model import (  # noqa: E402  (one reader of each source)
    FEC_RESULTS_INDEX,
    PRESIDENT_DOI,
    download,
    medsl_file,
    presidential_totals,
)

# The most two compilations of one cycle may disagree on a state's two-party
# Democratic share, in points: certified counts revised after MEDSL's
# transcription. Measured 2026-10-23, FEC against MEDSL's 1976-2024 file:
# at most 0.005 points in 2016 (Maryland) and 0.012 in 2020 (North Dakota).
# A column read under the wrong party is tens of points off.
FEC_MEDSL_TOLERANCE = 0.5

# For maintainers, kept out of _source: the site shows _source as the
# figure's provenance (PviMethodologyNote), where a regenerate command
# is noise.
REGENERATE = "backend/scripts/fetch_state_pvi.py"

# Cook's weights for the earlier and the more recent election (module
# docstring). A methodology fact, not a fitted value.
WEIGHTS = (0.25, 0.75)

# Max per-state PVI movement tolerated vs. the previous file (a new window
# shares one election with the last). One shared election plus one new one
# moves states a few points (measured max at the 2016+2020 to 2020+2024
# re-window: 3); a swapped column or sign flip moves most of the map by
# tens of points.
CONTINUITY_TOLERANCE = 5

DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "state_pvi.json"

_XL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def fec_compilation(year: int) -> bytes | None:
    """The FEC's Federal Elections <year> workbook, as its results index
    and the year's page link it; None when the index links no such year."""
    index = download(FEC_RESULTS_INDEX).decode("utf-8", "replace")
    page = re.search(r'href="([^"]*federal-elections-%d/?)"' % year, index)
    if not page:
        return None
    url = page.group(1) if page.group(1).startswith("http") else "https://www.fec.gov" + page.group(1)
    book = re.search(r'href="([^"]*federalelections%d\.xlsx)"' % year, download(url).decode("utf-8", "replace"))
    if not book:
        raise SystemExit(f"FEC Federal Elections {year}: its page links no federalelections{year}.xlsx")
    return download(book.group(1) if book.group(1).startswith("http") else "https://www.fec.gov" + book.group(1))


def fec_published_years() -> set[int]:
    """Presidential years the FEC's results index links a compilation for."""
    index = download(FEC_RESULTS_INDEX).decode("utf-8", "replace")
    return {int(y) for y in re.findall(r"federal-elections-(\d{4})", index) if int(y) % 4 == 0}


def _table_2(raw: bytes) -> tuple[ET.Element, list[str]]:
    """The workbook's Table 2 sheet (state presidential electoral and
    popular vote), found by its name."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        wb = zf.read("xl/workbook.xml").decode()
        rels = zf.read("xl/_rels/workbook.xml.rels").decode()
        sheets = [m for m in re.findall(r"<sheet\b[^>]*/>", wb) if re.search(r'name="[^"]*Table 2\b', m)]
        if len(sheets) != 1:
            raise SystemExit(f"FEC workbook: {len(sheets)} sheets named Table 2")
        rid = re.search(r'r:id="([^"]+)"', sheets[0]).group(1)
        rel = re.search(r'<Relationship\b[^>]*Id="%s"[^>]*/>' % re.escape(rid), rels).group(0)
        target = re.search(r'Target="([^"]+)"', rel).group(1).lstrip("/").removeprefix("xl/")
        return ET.fromstring(zf.read("xl/" + target)), [
            "".join(t.text or "" for t in si.iter(f"{_XL}t")) for si in ET.fromstring(zf.read("xl/sharedStrings.xml"))
        ]


def fec_counts(raw: bytes) -> dict[str, dict[str, int]]:
    """{"ST": {"D", "R"}} from an FEC compilation's Table 2: the popular-vote
    columns labelled "(D)" and "(R)". Raises unless 51 states sum to the
    table's own "Total:" row."""
    sheet, shared = _table_2(raw)
    rows = []
    for row in sheet.iter(f"{_XL}row"):
        cells = {}
        for c in row.iter(f"{_XL}c"):
            v = c.find(f"{_XL}v")
            if v is not None and v.text is not None:
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                cells[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
        rows.append(cells)
    popular = {col for r in rows for col, v in r.items() if v.strip().upper() == "POPULAR VOTE"}
    side = {}
    for r in rows:
        for col in popular:
            label = r.get(col, "").strip()
            if label.endswith("(D)"):
                side["D"] = col
            elif label.endswith("(R)"):
                side["R"] = col
    if set(side) != {"D", "R"}:
        raise SystemExit(f"FEC Table 2: no popular-vote columns labelled (D) and (R) ({sorted(popular)})")
    counts, total = {}, None
    for cells in rows:
        label = cells.get("A", "").strip().rstrip("*")  # "ME*", "NE*": split electoral votes
        try:
            d, r = int(float(cells[side["D"]])), int(float(cells[side["R"]]))
        except (KeyError, ValueError):
            continue
        if re.fullmatch(r"[A-Z]{2}", label):
            counts[label] = {"D": d, "R": r}
        elif label.startswith("Total"):
            total = {"D": d, "R": r}
    summed = {p: sum(v[p] for v in counts.values()) for p in ("D", "R")}
    if len(counts) != 51 or total != summed:
        raise SystemExit(f"FEC Table 2: {len(counts)} states summing to {summed}, total row {total}")
    return counts


def cross_check(fec: dict, medsl: dict, year: int) -> None:
    """Raises unless every state's two-party Democratic share in the FEC's
    table is within FEC_MEDSL_TOLERANCE points of MEDSL's."""
    off = []
    for st, c in fec.items():
        m = medsl.get(st)
        if not m:
            off.append(f"{st} not in MEDSL")
            continue
        gap = 100 * abs(c["D"] / (c["D"] + c["R"]) - m["D"] / (m["D"] + m["R"]))
        if gap > FEC_MEDSL_TOLERANCE:
            off.append(f"{st} {gap:.2f} points")
    if off:
        raise SystemExit(f"FEC and MEDSL {year} disagree: {', '.join(off)}")


def _two_party_d(counts: dict[str, dict[str, int]]) -> dict[str, float]:
    out = {}
    for st, dr in counts.items():
        total = dr["D"] + dr["R"]
        if total > 0:
            out[st] = dr["D"] / total
    return out


def compute_pvi(by_cycle: dict[str, dict[str, dict[str, int]]]) -> dict[str, int]:
    """Cook PVI per state (positive = R lean), over the two cycles in
    `by_cycle`, earlier then later (WEIGHTS). See module docstring."""
    cycles = sorted(by_cycle)
    nat_tp_d = {}
    for y in cycles:
        nat = {"D": 0, "R": 0}
        for dr in by_cycle[y].values():
            nat["D"] += dr["D"]
            nat["R"] += dr["R"]
        nat_tp_d[y] = nat["D"] / (nat["D"] + nat["R"])

    states = set()
    for y in cycles:
        states |= set(by_cycle[y])

    out: dict[str, int] = {}
    for st in sorted(states):
        margins = []
        ok = True
        for y in cycles:
            c = by_cycle[y].get(st)
            if not c or (c["D"] + c["R"]) == 0:
                ok = False
                break
            margins.append(c["D"] / (c["D"] + c["R"]) - nat_tp_d[y])
        if ok:
            out[st] = -round(sum(w * m for w, m in zip(WEIGHTS, margins)) * 100)
    return out


def ingestion_gates(
    pvi: dict[str, int], previous: dict[str, int] | None,
    by_cycle: dict[str, dict[str, dict[str, int]]],
) -> list[str]:
    """Structural + fidelity checks — guard the ingestion, not the scores:
    a sign flip or swapped column would silently invert every senator's
    seat expectation downstream."""
    failures = []
    for y in sorted(by_cycle):
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


def gather() -> tuple[dict[str, dict[str, dict[str, int]]], dict[str, str]]:
    """({cycle: {"ST": {"D", "R"}}}, {cycle: its source}) for the two newest
    presidential elections an official compilation covers."""
    url, name = medsl_file(PRESIDENT_DOI)
    medsl = {int(y): dict(v) for y, v in presidential_totals(download(url).decode()).items()}
    complete = {y for y, states in medsl.items() if len(states) == 51}
    fec_years = fec_published_years()
    newest = max(complete | fec_years)
    by_cycle, sources = {}, {}
    for year in (newest - 4, newest):
        raw = fec_compilation(year) if year in fec_years else None
        if raw is not None:
            by_cycle[str(year)] = fec_counts(raw)
            if year in complete:
                cross_check(by_cycle[str(year)], medsl[year], year)
            sources[str(year)] = (f"Federal Election Commission, Federal Elections {year}, Table 2 (state "
                                  f"presidential popular vote, from certified results; public domain)")
        elif year in complete:
            by_cycle[str(year)] = {st: c for st, c in medsl[year].items()}
            sources[str(year)] = (f"MIT Election Data and Science Lab, U.S. President {name.split('-president')[0]} "
                                  f"(Harvard Dataverse, {PRESIDENT_DOI}, {name}, CC0; from certified canvasses)")
        else:
            raise SystemExit(f"{year}: neither the FEC nor MEDSL's newest file has its presidential returns")
    return by_cycle, sources


def main() -> int:
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    previous: dict[str, int] | None = None
    if output.exists():
        try:
            previous = json.load(open(output)).get("states")
        except (ValueError, OSError):
            previous = None

    by_cycle, sources = gather()
    earlier, later = sorted(by_cycle)
    pvi = compute_pvi(by_cycle)

    vals = list(pvi.values())
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    even = sum(1 for v in vals if v == 0)
    print(f"computed {len(pvi)} jurisdictions over {earlier}+{later}: R-leaning {r_lean}, D-leaning "
          f"{d_lean}, EVEN {even}, min {min(vals)}, max {max(vals)}")

    failures = ingestion_gates(pvi, previous, by_cycle)
    for f in failures:
        print("GATE FAILED:", f)
    if failures:
        print("NOT writing output — gates failed")
        return 1

    json.dump(
        {
            "_source": f"{earlier}: {sources[earlier]}. {later}: {sources[later]}. Two-party vote shares, "
                       f"Cook PVI formula.",
            "_regenerate": REGENERATE,
            "_method": (
                f"STATE_PVI(st) = -round(100 * ({WEIGHTS[0]} * d{earlier} + {WEIGHTS[1]} * d{later})), "
                "d = state two-party D share - national two-party D share; "
                "positive = R lean. Cook Political Report's formula (since its 2022 release) over the two "
                "newest presidential elections; on the 2020+2024 window it reproduces all 51 of Cook's "
                "2025 published state PVIs."
            ),
            "_sign": "positive = R lean, negative = D lean (matches district_pvi.json)",
            "_window": f"{earlier}+{later}",
            "_as_of": utcnow().date().isoformat(),
            "inputs": {y: {st: by_cycle[y][st] for st in pvi} for y in (earlier, later)},
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
