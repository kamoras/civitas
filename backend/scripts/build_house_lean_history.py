"""Build backend/app/data/forecast_house_lean.json: each U.S. House
district's lean as it stood BEFORE each election the 2026 map's House
method is fitted on, on the lines that election was held on.

WHY. The House method (app/forecast/model.py) is fitted on past House
elections. Its lean input must be what was knowable before each one, on
the district lines it was held on, and made the same way as the 2026
input (app/data/district_lean.json, scripts/build_district_lean.py), or
the fit would describe a different number from the one it is applied to.

METHOD. The 2020 half of build_district_lean.py, applied to 2016 as well:
every populated 2020 Census block's internal point is placed in the VEST
precinct containing it, the precinct's presidential votes are shared among
its blocks by population (build_district_lean.votes_vest), and blocks sum
to districts. Cook's formula then gives the lean (app.forecast.model.
two_party_lean).

    election  lines                      lean from
    2018      116th Congress (TIGER)     2016 alone
    2020      116th Congress (TIGER)     2016 alone (North Carolina excluded:
                                         it voted on new 2020 lines, the
                                         117th's, which Census publishes on
                                         2010 blocks only)
    2022      118th Congress (Census BEF) 25% 2016 + 75% 2020
    2024      119th Congress (Census BEF) 25% 2016 + 75% 2020

Not reconstructed, and why: 2016 and earlier have no open precinct
returns for the presidential elections before them (VEST begins with
2016), and 2018/2020 use 2016 alone because 2012 is not available on the
same terms (Cook's own 2017 index averaged 2012 and 2016). The 116th
Congress's block equivalency file is on 2010 blocks, so those lines come
from Census's TIGER/Line district polygons, with each 2020 block point
placed in the polygon containing it (one outside every polygon, on a
shoreline, goes to the nearest district vertex).

INPUTS (each open-licensed or public domain):
  * VEST 2016 precinct results and shapes (Harvard Dataverse
    doi:10.7910/DVN/NH5S2I, CC BY 4.0), each state's file checked by MD5;
  * VEST 2020 (doi:10.7910/DVN/K7760H, CC BY 4.0), as build_district_lean.py;
  * U.S. Census Bureau (public domain): 2020 P.L. 94-171 blocks, the 118th
    and 119th Congress block equivalency files, TIGER/Line 2019 116th
    Congress districts;
  * MEDSL U.S. President 1976-2024 (doi:10.7910/DVN/42MVDX, CC0): the 2016
    state totals each state's VEST file is gated against, and the 2016
    national share; the 2020 national share is state_pvi.json's (FEC).

GATES (any failure: nothing is written): each state's 2016 VEST two-party
total within 0.5% of MEDSL's certified count; every district of each line
set present exactly once, with the House's seat count per state.

Memory: one state at a time. Downloads: about 0.7 GB of VEST 2016 (each
state's file is deleted once read unless --keep), 64 MB of TIGER, 20 MB of
BEFs; the 2020 inputs are build_district_lean.py's cache.

    python backend/scripts/build_house_lean_history.py --cache DIR [--states AL,PA] [--keep]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_district_lean as bdl  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402
from app.forecast.model import two_party_lean  # noqa: E402
from fit_forecast_model import presidential_totals  # noqa: E402

OUTPUT = bdl.DATA / "forecast_house_lean.json"
REGENERATE = "backend/scripts/build_house_lean_history.py"
VEST16_DOI = "doi:10.7910/DVN/NH5S2I"
VEST16_VERSION = "98"
VEST16_VERSION_URL = (
    "https://dataverse.harvard.edu/api/datasets/:persistentId/versions/"
    f"{VEST16_VERSION}?persistentId={VEST16_DOI}"
)
VEST16_D, VEST16_R = "G16PREDCLI", "G16PRERTRU"
CD116_URL = "https://www2.census.gov/geo/tiger/TIGER2019/CD/tl_2019_us_cd116.zip"
CD118_URL = f"{bdl.cw._RDO}/2023/118-congressional-district-bef/cd118.zip"
PRESIDENT_URL = "https://dataverse.harvard.edu/api/access/datafile/13887042"
STATE_TOLERANCE = 0.005
CYCLES = {"2018": ("116", ("2016",)), "2020": ("116", ("2016",)),
          "2022": ("118", ("2016", "2020")), "2024": ("119", ("2016", "2020"))}
EXCLUDED = {"2020": {"NC": "Voted on new 2020 lines (the 117th Congress's), which Census publishes on 2010 "
                           "blocks only."}}


def vest16_files(cache: Path) -> dict[str, tuple[int, str]]:
    meta = json.loads(bdl.fetch(VEST16_VERSION_URL, cache / f"vest16_v{VEST16_VERSION}.json").read_text())["data"]
    return {f["dataFile"]["filename"]: (f["dataFile"]["id"], f["dataFile"]["md5"]) for f in meta["files"]}


def vest16_name(st: str, files: dict) -> str:
    for name in (f"{st.lower()}_2016.zip", f"{st.lower()}.zip"):
        if name in files:
            return name
    raise SystemExit(f"{st}: no VEST 2016 file")


def medsl_2016(cache: Path) -> dict[str, dict[str, int]]:
    """{state: {"D", "R"}} — each major-party candidate's 2016 votes on every
    ballot line (New York's fusion lines are separate rows in MEDSL's file,
    and a VEST precinct counts the candidate's total)."""
    text = bdl.fetch(PRESIDENT_URL, cache / "president.csv").read_text()
    return presidential_totals(text)["2016"]


def cd116_shapes(cache: Path) -> tuple[list, list[dict]]:
    shapes, records, _ = bdl.read_shapefile(bdl.fetch(CD116_URL, cache / "tl_2019_us_cd116.zip"),
                                            ("STATEFP", "CD116FP"))
    return shapes, records


def assign_116(st: str, lonlat: np.ndarray, shapes, records) -> tuple[list[str | None], int]:
    """Each block point's 116th-Congress district, by point in polygon."""
    fips = sl._FIPS[st]
    idx = [i for i, r in enumerate(records) if r["STATEFP"] == fips and bdl.seat(r["CD116FP"]) is not None]
    mine = [shapes[i] for i in idx]
    owner = bdl.locate(lonlat, mine)
    seats = [bdl.seat(records[i]["CD116FP"]) for i in idx]
    out = [seats[o] if o >= 0 else None for o in owner]
    missing = np.flatnonzero(owner < 0)
    if len(missing):
        verts = np.concatenate([np.concatenate(s) for s in mine])
        labels = np.concatenate([[k] * sum(len(r) for r in s) for k, s in enumerate(mine)])
        _, j = cKDTree(verts).query(lonlat[missing])
        for m, v in zip(missing, j):
            out[m] = seats[labels[v]]
    return out, len(missing)


def bef(cache: Path, url: str, name: str, congress: str, st: str) -> dict[str, str]:
    fips = sl._FIPS[st]
    with zipfile.ZipFile(bdl.fetch(url, cache / name)) as zf:
        member = f"{fips}_{st}_CD{congress}.txt"
        return bdl._bef_member(zf, member if member in zf.namelist() else f"NationalCD{congress}.txt", fips)


def build_state(st: str, cache: Path, files16: dict, files20: dict, shapes116, records116,
                keep: bool) -> dict:
    geoids, pop, lonlat, _ = bdl.state_blocks(st, cache)
    name = vest16_name(st, files16)
    fid, md5 = files16[name]
    path = bdl.fetch(bdl.VEST_FILE_URL.format(id=fid), cache / f"vest16_{name}", md5)
    v16, stats16 = bdl.votes_vest(path, VEST16_D, VEST16_R, lonlat, pop)
    if not keep:
        path.unlink()
    v20, _ = bdl.votes_2020(st, cache, files20, lonlat, pop)
    b116, unplaced = assign_116(st, lonlat, shapes116, records116)
    b118 = bef(cache, CD118_URL, "cd118.zip", "118", st)
    b119 = bef(cache, bdl.cw.CD119_URL, "cd119.zip", "119", st)
    lines = {"116": b116,
             "118": [bdl.seat(b118.get(g, "")) for g in geoids],
             "119": [bdl.seat(b119.get(g, "")) for g in geoids]}
    out: dict = {}
    for congress, labels in lines.items():
        sums = collections.defaultdict(lambda: np.zeros(4))
        for i, d in enumerate(labels):
            if d is not None:
                sums[d] += (v16[i, 0], v16[i, 1], v20[i, 0], v20[i, 1])
        out[congress] = {f"{st}-{d}": [round(float(x)) for x in v] for d, v in sorted(sums.items(), key=lambda kv: int(kv[0]))}
    print(f"{st}: {len(geoids)} blocks, {stats16['precincts']} VEST 2016 precincts, "
          f"{unplaced} block points outside every 116th polygon", flush=True)
    return {"lines": out, "total16": [float(v16[:, 0].sum()), float(v16[:, 1].sum())], "stats16": stats16}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--states")
    ap.add_argument("--keep", action="store_true", help="keep each VEST 2016 file after reading it")
    args = ap.parse_args(argv)
    states = args.states.split(",") if args.states else sorted(s for s in sl._STATES if s != "DC")
    files16, files20 = vest16_files(args.cache), bdl.vest_files(args.cache)
    official = medsl_2016(args.cache)
    shapes116, records116 = cd116_shapes(args.cache)
    lines: dict[str, dict] = {"116": {}, "118": {}, "119": {}}
    failures = []
    for st in states:
        res = build_state(st, args.cache, files16, files20, shapes116, records116, args.keep)
        for congress, table in res["lines"].items():
            lines[congress].update(table)
        o = official[st]
        for k, side in ((0, "D"), (1, "R")):
            if abs(res["total16"][k] - o[side]) > STATE_TOLERANCE * o[side]:
                failures.append(f"{st} 2016 {side}: VEST {res['total16'][k]:.0f} vs MEDSL {o[side]}")
    # Seat counts: the 116th from the TIGER file, the 118th/119th from the
    # 2020 apportionment (both BEFs carry it).
    by_fips = {fips: st for st, fips in sl._FIPS.items()}
    expected116 = collections.Counter(by_fips[r["STATEFP"]] for r in records116
                                      if bdl.seat(r["CD116FP"]) is not None and r["STATEFP"] in by_fips)
    for congress, table in lines.items():
        got = collections.Counter(k.split("-")[0] for k in table)
        for st in states:
            want = expected116[st] if congress == "116" else None
            if want is not None and got[st] != want:
                failures.append(f"{congress}th: {st} has {got[st]} districts, expected {want}")
        if congress != "116" and not args.states and sum(got.values()) != 435:
            failures.append(f"{congress}th: {sum(got.values())} districts, expected 435")
    if failures:
        print("GATE FAILURES:\n  " + "\n  ".join(failures))
        return 1
    if args.states:
        print("subset build: not written")
        return 0
    spvi = json.loads(bdl.STATE_PVI.read_text())["inputs"]
    nation = {"2016": {k: sum(v[k] for v in official.values()) for k in ("D", "R")},
              "2020": {k: sum(v[k] for v in spvi["2020"].values()) for k in ("D", "R")}}
    leans: dict[str, dict[str, float]] = {}
    for cycle, (congress, years) in CYCLES.items():
        leans[cycle] = {}
        for district, (d16, r16, d20, r20) in lines[congress].items():
            if district.split("-")[0] in EXCLUDED.get(cycle, {}):
                continue
            c16, c20 = {"D": d16, "R": r16}, {"D": d20, "R": r20}
            if years == ("2016",):
                lean = -100 * (d16 / (d16 + r16) - nation["2016"]["D"] / sum(nation["2016"].values()))
            else:
                lean = two_party_lean(c16, c20, nation["2016"], nation["2020"])
            leans[cycle][district] = round(lean, 2)
    OUTPUT.write_text(json.dumps({
        "_source": ("Computed by Civitas with build_district_lean.py's block method from VEST 2016 and 2020 "
                    "precinct results (Harvard Dataverse doi:10.7910/DVN/NH5S2I and doi:10.7910/DVN/K7760H, "
                    "CC BY 4.0) and U.S. Census Bureau 2020 blocks, 118th/119th Congress BEFs and TIGER/Line "
                    "2019 116th Congress districts (public domain); 2016 national and state gate totals from "
                    "MEDSL U.S. President 1976-2024 (CC0), 2020 national from state_pvi.json (FEC)."),
        "_regenerate": REGENERATE,
        "_sign": "positive = R lean, negative = D lean",
        "_method": {cycle: f"{congress}th Congress lines; " + ("2016 alone" if years == ("2016",) else
                                                                  "25% 2016 + 75% 2020 (Cook's formula)")
                    for cycle, (congress, years) in CYCLES.items()},
        "excluded": EXCLUDED,
        "nation": nation,
        "lines": {k: dict(sorted(v.items())) for k, v in lines.items()},
        "leans": leans,
    }, indent=0, separators=(",", ":")) + "\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
