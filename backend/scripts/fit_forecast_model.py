"""Fit the 2026 map's poll-informed ranges on official historical results.

Writes two files (AGENTS.md section 3a: calculated values are generated
data, never hand-typed):

  app/data/forecast_training.json — every contested U.S. Senate general
      election 1998-2022 as the method sees it: the result, the state's
      lean before the election, the incumbent, and every poll 538's file
      holds for it, plus each year's generic-ballot polls. The fit can be
      re-run from this file alone (tests/test_forecast_model.py does).
  app/data/forecast_model.json — the fitted parameters at each horizon
      (app.forecast.model.HORIZONS) and the leave-one-cycle-out evaluation:
      calibration table, interval coverage, Brier score and log loss for
      the combined method against fundamentals-only and polls-only.

SOURCES (every one public domain or open-licensed, nothing behind a wall):

  * Results: MIT Election Data and Science Lab, "U.S. Senate statewide
    1976-2024" (Harvard Dataverse doi:10.7910/DVN/PEJ5QU, CC0 1.0).
  * Lean: MEDSL, "U.S. President 1976-2024" (doi:10.7910/DVN/42MVDX, CC0
    1.0). A race's lean is Cook's formula over the two presidential
    elections BEFORE it (so a presidential year's race never sees its own
    year's presidential vote), the same formula and weights as
    app/data/state_pvi.json, which is the 2026 races' lean.
  * Incumbency: the Federal Election Commission's candidate master files
    (fec.gov bulk downloads cnYY.zip, public domain): a nominee is the
    incumbent when the FEC codes their candidacy "I" for that election.
  * Polls: FiveThirtyEight, pollster-ratings/raw_polls.csv
    (github.com/fivethirtyeight/data, CC BY 4.0), pinned to a commit: the
    final weeks' polls of each race with the certified result, 1998-2022,
    and the generic congressional ballot (House-G-US).

WHICH RACES. A November general with a Democrat and a Republican as the
top two finishers (MEDSL's party_simplified); the margin is the
Republican's two-party share minus the Democrat's, in points. Runoffs
(Georgia, Louisiana's December round) are excluded: the general's count
is the one the method describes. 2024 is excluded because 538's file
ends with 2022, so it has no polls to fit or check against.

Run from the repo (network on the first run, about 8 MB, cached):

    python3 backend/scripts/fit_forecast_model.py [--cache DIR]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.forecast import model  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows  # noqa: E402
from build_district_lean import _as_first_sheet  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
TRAINING = DATA / "forecast_training.json"
MODEL = DATA / "forecast_model.json"
REGENERATE = "backend/scripts/fit_forecast_model.py"

SENATE_URL = "https://dataverse.harvard.edu/api/access/datafile/13887039?format=original"
PRESIDENT_URL = "https://dataverse.harvard.edu/api/access/datafile/13887042"
FEC_CN_URL = "https://www.fec.gov/files/bulk-downloads/{year}/cn{yy}.zip"
POLLS_COMMIT = "be387bc1649413be1c85f2181b2c32aacee24315"  # 2024-01-25, the file's last change
POLLS_URL = f"https://raw.githubusercontent.com/fivethirtyeight/data/{POLLS_COMMIT}/pollster-ratings/raw_polls.csv"

YEARS = tuple(range(1998, 2023, 2))
PRESIDENTIAL_YEARS = tuple(range(1976, 2025, 4))

SOURCE = (
    "Results: MIT Election Data and Science Lab, U.S. Senate statewide 1976-2024 (doi:10.7910/DVN/PEJ5QU, CC0). "
    "Lean: MEDSL U.S. President 1976-2024 (doi:10.7910/DVN/42MVDX, CC0), Cook's formula over the two "
    "presidential elections before each race. Incumbency: FEC candidate master files (public domain). "
    f"Polls: FiveThirtyEight pollster-ratings/raw_polls.csv at commit {POLLS_COMMIT[:7]} (CC BY 4.0)."
)


def fetch(url: str, dest: Path) -> bytes:
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
    return dest.read_bytes()


def presidential_totals(text: str) -> dict[str, dict[str, dict[str, int]]]:
    """{year: {state: {"D", "R"}}}: each major-party candidate's votes on
    every ballot line. MEDSL's 1976-2020 rows put a fusion line (New York's
    Working Families, Conservative) under its own party, so a candidate's
    total is every row with their name in that state."""
    side_of: dict = {}
    rows = list(csv.DictReader(io.StringIO(text)))
    # The write-in flag is not used: MEDSL's file marks DC's 2020 major-party
    # candidates as write-ins. A row with no candidate named (pooled
    # write-ins, which MEDSL files under a party) is no candidate's vote.
    rows = [r for r in rows if r["candidate"].strip()]
    for r in rows:
        side = {"DEMOCRAT": "D", "REPUBLICAN": "R"}.get(r["party_simplified"])
        if side:
            side_of[(r["year"], r["state_po"], r["candidate"])] = side
    out: dict = defaultdict(lambda: defaultdict(lambda: {"D": 0, "R": 0}))
    for r in rows:
        side = side_of.get((r["year"], r["state_po"], r["candidate"]))
        if side:
            out[r["year"]][r["state_po"]][side] += int(float(r["candidatevotes"]))
    return out


def presidential_counts(text: str) -> dict[int, dict[str, dict[str, int]]]:
    """{year: {state: {"D", "R"}, "US": {...}}} two-party presidential votes."""
    out = {int(y): dict(v) for y, v in presidential_totals(text).items()}
    for y, states in out.items():
        states["US"] = {k: sum(s[k] for s in states.values()) for k in ("D", "R")}
        if len(states) != 52:
            raise SystemExit(f"president {y}: {len(states) - 1} jurisdictions, expected 51")
    return out


def lean_before(pres: dict, year: int, state: str) -> float:
    older, newer = [y for y in PRESIDENTIAL_YEARS if y < year][-2:]
    return model.two_party_lean(pres[older][state], pres[newer][state], pres[older]["US"], pres[newer]["US"])


def senate_races(text: str) -> list[dict]:
    """Contested November generals: the top two are a D and an R."""
    votes: dict[tuple, dict[str, list]] = defaultdict(dict)
    for r in csv.DictReader(io.StringIO(text)):
        y = int(r["year"])
        if y not in YEARS or r["stage"].lower() != "gen" or r["mode"].lower() != "total":
            continue
        key = (y, r["state_po"], r["special"].upper() == "TRUE")
        name = r["candidate"].strip().upper()
        entry = votes[key].setdefault(name, [0.0, set()])
        entry[0] += float(r["candidatevotes"] or 0)
        entry[1].add(r["party_simplified"])
    races = []
    for (y, st, special), cands in sorted(votes.items()):
        top = sorted(cands.items(), key=lambda kv: -kv[1][0])[:2]
        parties = {}
        for name, (v, ps) in top:
            side = "D" if "DEMOCRAT" in ps else "R" if "REPUBLICAN" in ps else None
            if side:
                parties[side] = (name, v)
        if set(parties) != {"D", "R"}:
            continue
        d, r = parties["D"][1], parties["R"][1]
        races.append({"year": y, "state": st, "special": special, "margin": round(100 * (r - d) / (r + d), 3),
                      "dem": parties["D"][0], "rep": parties["R"][0]})
    return races


def incumbents(cache: Path) -> dict[tuple[int, str], list[tuple[str, str]]]:
    """{(year, state): [(party D/R, surname)]} of Senate candidacies the FEC
    codes as incumbent for that election."""
    out: dict = defaultdict(list)
    for y in YEARS:
        raw = fetch(FEC_CN_URL.format(year=y, yy=str(y)[2:]), cache / f"cn{str(y)[2:]}.zip")
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            text = zf.read(zf.namelist()[0]).decode("latin-1")
        for line in text.splitlines():
            f = line.split("|")
            if len(f) < 8 or f[5] != "S" or f[3] != str(y) or f[7] != "I":
                continue
            side = {"DEM": "D", "REP": "R"}.get(f[2])
            if side:
                out[(y, f[4])].append((side, f[1].split(",")[0].strip().upper()))
    return out


def incumbent_code(race: dict, inc: dict) -> int:
    """+1 the Republican nominee is the incumbent, -1 the Democrat, 0 neither.
    The nominee is matched to the FEC's incumbent candidacy by party and
    surname (MEDSL prints names first-last, the FEC last-first)."""
    code = 0
    for side, surname in inc.get((race["year"], race["state"]), []):
        nominee = race["dem" if side == "D" else "rep"]
        words = nominee.replace(".", " ").replace(",", " ").split()
        if surname in words or surname.replace(" ", "") in "".join(words):
            code = 1 if side == "R" else -1
    return code


def polls(text: str) -> tuple[dict, dict, dict]:
    """({(year, state, special): [Senate poll rows]}, {year: [generic poll
    rows]}, {(year, "ST-N"): [House poll rows]}) — 538 numbers districts
    from 1, so an at-large seat is "AK-1" there and "AK-0" here."""
    races: dict = defaultdict(list)
    generic: dict = defaultdict(list)
    house: dict = defaultdict(list)
    for r in csv.DictReader(io.StringIO(text)):
        kind = r["race"].split("_")[1]
        if kind not in ("Sen-G", "Sen-GS", "House-G") or r["electiondate"][5:7] != "11":
            continue
        pct = {r["cand1_party"]: float(r["cand1_pct"]), r["cand2_party"]: float(r["cand2_pct"])}
        if set(pct) != {"DEM", "REP"}:
            continue
        n = int(float(r["samplesize"])) if r["samplesize"] not in ("", "NA") else None
        partisan = {"REP": "R", "DEM": "D"}.get(r["partisan"], "")
        row = model.Poll(r["pollster"], int(r["time_to_election"]), n, round(pct["REP"] - pct["DEM"], 2),
                         partisan).as_row()
        year = int(r["electiondate"][:4])
        if kind == "House-G" and r["location"] == "US":
            generic[str(year)].append(row)
        elif kind == "House-G":
            house[(year, r["location"])].append(row)
        else:
            races[(year, r["location"], kind == "Sen-GS")].append(row)
    return races, generic, house


# ---------------------------------------------------------------------------
# The House (app/data/forecast_house_training.json, forecast_house_model.json)
# ---------------------------------------------------------------------------

HOUSE_TRAINING = DATA / "forecast_house_training.json"
HOUSE_MODEL = DATA / "forecast_house_model.json"
HOUSE_LEAN = DATA / "forecast_house_lean.json"
# Federal Elections 2018, 2020 and 2022, the FEC's compilation of each
# state's certified results (public domain): the "US House Results by
# State" sheet of each. 2024 is not fitted: the FEC has published no 2024
# compilation of House results, and no openly licensed archive holds the
# 2024 generic-ballot polls the method needs (538's ends with 2022; MEDSL's
# House file sits behind a sign-in form, which Civitas does not pass).
FEC_HOUSE = {
    2018: ("https://www.fec.gov/documents/2706/federalelections2018.xlsx", "2018 US House Results by State"),
    2020: ("https://www.fec.gov/documents/4228/federalelections2020.xlsx", "13. US House Results by State"),
    2022: ("https://www.fec.gov/documents/5676/federalelections2022.xlsx", "8. US House Results by State"),
}
HOUSE_SOURCE = (
    "Results and incumbency: Federal Election Commission, Federal Elections 2018, 2020 and 2022, "
    "US House Results by State (certified results, public domain). Lean: app/data/forecast_house_lean.json "
    "(Civitas, from VEST 2016 and 2020 precinct results, CC BY 4.0, and Census blocks and districts), "
    f"each district's lean before the election on the lines it was held on. Polls: FiveThirtyEight "
    f"pollster-ratings/raw_polls.csv at commit {POLLS_COMMIT[:7]} (CC BY 4.0)."
)
_D_PARTIES = {"D", "DEM", "DFL"}
_R_PARTIES = {"R", "REP"}


def _sheet_file(raw: bytes, title: str) -> str:
    """The worksheet file ("sheet13") of the sheet named `title`."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    wb = zf.read("xl/workbook.xml").decode()
    rels = zf.read("xl/_rels/workbook.xml.rels").decode()
    sheet = re.search(r'<sheet\b[^>]*name="%s"[^>]*/>' % re.escape(title.replace("&", "&amp;")), wb).group(0)
    rid = re.search(r'r:id="([^"]+)"', sheet).group(1)
    rel = re.search(r'<Relationship\b[^>]*Id="%s"[^>]*/>' % re.escape(rid), rels).group(0)
    return re.search(r'Target="([^"]+)"', rel).group(1).rsplit("/", 1)[-1].removesuffix(".xml")


def fec_house(raw: bytes, sheet: str) -> list[dict]:
    """Contested House generals from one FEC workbook: the top two by
    general-election votes are a Democrat and a Republican. A candidate on
    several lines (New York's and Connecticut's fusion parties) counts once,
    every line summed."""
    rows = _xlsx_rows(_as_first_sheet(raw, _sheet_file(raw, sheet)))
    vcol = next(k for k in rows[0] if k.strip() == "GENERAL VOTES")
    icol = next(k for k in rows[0] if k.startswith("(I)"))
    races: dict = defaultdict(dict)
    for r in rows:
        st, dist = r.get("STATE ABBREVIATION", "").strip(), r.get("DISTRICT", "").strip()
        if not re.fullmatch(r"\d\d", dist) or not st:
            continue  # totals, headers, unexpired-term specials ("05 - UNEXPIRED TERM")
        if not (r.get("CANDIDATE NAME") or "").strip() or (r.get("TOTAL VOTES") or "").strip():
            continue  # "Party Votes:" / "District Votes:" total rows
        # A ranked-choice count (Maine, Alaska from 2022) is its last round
        # where the workbook gives the rounds; 2018's gives only the first.
        rounds = [r[k] for k in sorted(k for k in r if "ROUND RCV VOTES" in k) if re.fullmatch(r"\d+", (r[k] or "").strip())]
        votes = re.sub(r"[,\s]", "", rounds[-1] if rounds else (r.get(vcol) or ""))
        if not re.fullmatch(r"\d+", votes):
            continue  # "Unopposed", blank, write-in notes
        fec = r.get("FEC ID#") or r.get("FEC ID") or ""
        name = (fec if fec not in ("", "n/a") else r.get("CANDIDATE NAME", "")).strip()
        cand = races[(st, int(dist))].setdefault(name, {"votes": 0, "parties": set(), "inc": False})
        cand["votes"] += int(votes)
        cand["parties"].add((r.get("PARTY") or "").strip().upper())
        cand["inc"] |= (r.get(icol) or "").strip() == "(I)"
    out = []
    for (st, dist), cands in races.items():
        top = sorted(cands.values(), key=lambda c: -c["votes"])[:2]
        if len(top) < 2:
            continue
        side = {("D" if c["parties"] & _D_PARTIES else "R" if c["parties"] & _R_PARTIES else "X"): c for c in top}
        if set(side) != {"D", "R"}:
            continue
        d, rep = side["D"], side["R"]
        out.append({"state": st, "district": dist,
                    "margin": round(100 * (rep["votes"] - d["votes"]) / (rep["votes"] + d["votes"]), 3),
                    "inc": 1 if rep["inc"] else -1 if d["inc"] else 0})
    return out


def fit_house(cache: Path, house_polls: dict, generic: dict, senate: tuple) -> None:
    leans = json.loads(HOUSE_LEAN.read_text())
    rows, missing = [], []
    for year, (url, sheet) in FEC_HOUSE.items():
        lean = leans["leans"][str(year)]
        lines = leans["lines"][leans["_method"][str(year)].split("th", 1)[0]]
        at_large = {k.split("-")[0] for k in lines if k.endswith("-0")}
        states = {k.split("-")[0] for k in lines}
        for race in fec_house(fetch(url, cache / f"federalelections{year}.xlsx"), sheet):
            if race["state"] not in states:
                continue  # the delegates (DC, the territories) are not states' seats
            seat = f"{race['state']}-{0 if race['state'] in at_large else race['district']}"
            if race["state"] in leans["excluded"].get(str(year), {}):
                continue
            if seat not in lean:
                missing.append(f"{year} {seat}")
                continue
            poll_key = (year, f"{race['state']}-{1 if race['state'] in at_large else race['district']}")
            rows.append({"year": year, "state": seat, "special": False, "margin": race["margin"],
                         "lean": lean[seat], "inc": race["inc"], "polls": house_polls.get(poll_key, [])})
    per_year = {y: sum(1 for r in rows if r["year"] == y) for y in FEC_HOUSE}
    print(f"House: {len(rows)} races, {sum(1 for r in rows if r['polls'])} polled; per year {per_year}; "
          f"no lean for {missing}")
    if missing or any(v < 300 for v in per_year.values()):
        raise SystemExit("House: a results row has no lean, or a cycle parsed too few contested races")
    gen = {y: v for y, v in generic.items() if int(y) in FEC_HOUSE}
    HOUSE_TRAINING.write_text(json.dumps({
        "_source": HOUSE_SOURCE, "_regenerate": REGENERATE,
        "_license": "Poll rows derived from FiveThirtyEight's raw_polls.csv, CC BY 4.0; leans derived from "
                    "VEST, CC BY 4.0; results public domain.",
        "_columns": {"polls": "[pollster, days before election day, sample size, R-D margin, partisan sponsor]"},
        "races": rows, "generic": dict(sorted(gen.items())),
    }, separators=(",", ":")) + "\n")
    write_model(HOUSE_MODEL, HOUSE_SOURCE, rows, gen, borrow=senate)


def write_model(path: Path, source: str, rows: list[dict], generic: dict, borrow: tuple | None = None) -> dict:
    """Fit and evaluate one chamber. `borrow` is (rows, generic) of the
    chamber whose national coefficient this one takes (model.fit): the
    full fit's for the published parameters, and for each held-out cycle a
    fit without that cycle."""
    out = {"_source": source, "_regenerate": REGENERATE, "_method": "app/forecast/model.py (module docstring)",
           "fits": {}, "evaluation": {}}
    if borrow:
        out["_nationalCoefficient"] = ("the Senate fit's generic-ballot coefficient at the same horizon "
                                       "(model.fit, national_coef): three House cycles cannot identify it")
    for h in model.HORIZONS:
        coef = coef_for = None
        if borrow:
            b_rows, b_gen = borrow
            coef = model.fit(b_rows, b_gen, h)["beta"][3]

            def coef_for(year, h=h):
                return model.fit([r for r in b_rows if r["year"] != year],
                                 {y: v for y, v in b_gen.items() if int(y) != year}, h)["beta"][3]
        out["fits"][str(h)] = model.fit(rows, generic, h, coef)
        out["evaluation"][str(h)] = model.leave_one_cycle_out(rows, generic, h, coef_for)
        ev = out["evaluation"][str(h)]
        keys = ("races", "polledRaces", "all", "polled", "coverage80", "coverage95", "meanAbsError")
        print(f"{path.name} horizon {h}: {json.dumps({k: ev[k] for k in keys})}")
        for b in ev["calibration"]:
            print("   ", b)
    path.write_text(json.dumps(at_write_precision(out), indent=1) + "\n")
    return out


# Fitted numbers are written to 10 significant digits: far beyond anything
# the map shows, and short of the last bits a different BLAS changes in a
# least-squares solve, so the file is the same on every platform. The test
# compares a refit with it within that precision.
WRITE_DIGITS = 10


def at_write_precision(value):
    if isinstance(value, float):
        return float(f"{value:.{WRITE_DIGITS}g}")
    if isinstance(value, dict):
        return {k: at_write_precision(v) for k, v in value.items()}
    if isinstance(value, list):
        return [at_write_precision(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-forecast")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    pres = presidential_counts(fetch(PRESIDENT_URL, args.cache / "president.csv").decode())
    races = senate_races(fetch(SENATE_URL, args.cache / "senate.csv").decode())
    inc = incumbents(args.cache)
    race_polls, generic, house_polls = polls(fetch(POLLS_URL, args.cache / "raw_polls.csv").decode())
    rows = []
    for race in races:
        key = (race["year"], race["state"], race["special"])
        rows.append({"year": race["year"], "state": race["state"], "special": race["special"],
                     "margin": race["margin"], "lean": round(lean_before(pres, race["year"], race["state"]), 3),
                     "inc": incumbent_code(race, inc), "polls": race_polls.get(key, [])})
    unmatched = sorted(k for k in race_polls if k not in {(r["year"], r["state"], r["special"]) for r in rows})
    per_year = {y: sum(1 for r in rows if r["year"] == y) for y in YEARS}
    print(f"{len(rows)} races, {sum(1 for r in rows if r['polls'])} polled; per year {per_year}")
    print(f"poll races with no contested-race row (not D-vs-R top two): {unmatched}")
    if any(v < 25 for v in per_year.values()):
        raise SystemExit("a cycle has fewer than 25 contested races: the results file did not parse")
    training = {
        "_source": SOURCE, "_regenerate": REGENERATE,
        "_license": "Poll rows derived from FiveThirtyEight's raw_polls.csv, CC BY 4.0 "
                    "(https://github.com/fivethirtyeight/data); results and lean inputs CC0 / public domain.",
        "_columns": {"polls": "[pollster, days before election day, sample size, R-D margin, partisan sponsor]"},
        "races": rows, "generic": dict(sorted(generic.items())),
    }
    TRAINING.write_text(json.dumps(training, separators=(",", ":")) + "\n")
    write_model(MODEL, SOURCE, rows, training["generic"])
    fit_house(args.cache, house_polls, generic, (rows, training["generic"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
