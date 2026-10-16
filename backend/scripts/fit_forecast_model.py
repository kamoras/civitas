"""Fit the election map's poll-informed ranges on official historical results.

Writes four files (AGENTS.md section 3a: calculated values are generated
data, never hand-typed). app/data_refresh.py reruns this monthly, and
whenever the training polls change, into the data volume (/data/<name>);
the checked-in app/data/ copies are the fallback before the first run.

  forecast_training.json: every contested U.S. Senate general election
      that has training polls, as the method sees it: the result, the
      state's lean before the election, the incumbent, and every poll the
      training file holds for it, plus each year's generic-ballot polls.
      The fit can be re-run from this file alone (tests/
      test_forecast_model.py does). `_coverage` names the cycles each
      input reaches, which is what the staleness alert reads.
  forecast_model.json: the fitted parameters at each horizon
      (app.forecast.model.HORIZONS), the leave-one-cycle-out evaluation
      (calibration table, interval coverage, Brier score and log loss for
      the combined method against fundamentals-only and polls-only), and
      the forward check: the newest cycle predicted by a fit on the cycles
      before it only.
  forecast_house_training.json, forecast_house_model.json: the same for
      the House.

SOURCES (every one public domain or open, nothing behind a sign-in):

  * Results: MIT Election Data and Science Lab, "U.S. Senate statewide
    1976-2024" (Harvard Dataverse doi:10.7910/DVN/PEJ5QU, CC0 1.0), its
    newest version read through the Dataverse API.
  * Lean: MEDSL, "U.S. President 1976-2024" (doi:10.7910/DVN/42MVDX, CC0
    1.0). A race's lean is Cook's formula over the two presidential
    elections BEFORE it (so a presidential year's race never sees its own
    year's presidential vote), the same formula and weights as
    app/data/state_pvi.json, which is the coming races' lean.
  * Incumbency: the Federal Election Commission's candidate master files
    (fec.gov bulk downloads cnYY.zip, public domain): a nominee is the
    incumbent when the FEC codes their candidacy "I" for that election.
  * Polls: Silver Bulletin's raw polls (app/forecast/training_polls.py),
    the copy in use of app/data/forecast_polls.json: each race's final
    weeks of polls with the certified result, and the generic
    congressional ballot. Free of charge for any purpose, attributed to
    Silver Bulletin as its terms ask.

WHICH RACES. A November general with a Democrat and a Republican as the
top two finishers (MEDSL's party_simplified); the margin is the
Republican's two-party share minus the Democrat's, in points. Runoffs
(Georgia, Louisiana's December round) are excluded: the general's count
is the one the method describes. A race whose result Silver Bulletin
states differently from MEDSL's count (training_polls.RESULT_TOLERANCE)
is left out and listed under `_excluded`: one of the two is wrong, and
the fit can't tell which (MEDSL files Georgia's 2008 runoff as its
general). The cycles are every even year the training polls and MEDSL's
results both reach.

Run from the repo (network on the first run, cached):

    python3 backend/scripts/fit_forecast_model.py [--cache DIR] [--polls FILE]
        [--senate-training OUT --senate-model OUT --house-training OUT --house-model OUT]
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
from app.atomic_write import runtime_data_path  # noqa: E402
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.forecast import model  # noqa: E402
from app.forecast import training_polls as tp  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows, xlsx_sheet_as_first  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
TRAINING = DATA / "forecast_training.json"
MODEL = DATA / "forecast_model.json"
HOUSE_TRAINING = DATA / "forecast_house_training.json"
HOUSE_MODEL = DATA / "forecast_house_model.json"
HOUSE_LEAN = DATA / "forecast_house_lean.json"
REGENERATE = "backend/scripts/fit_forecast_model.py"

DATAVERSE = "https://dataverse.harvard.edu"
SENATE_DOI = "doi:10.7910/DVN/PEJ5QU"
PRESIDENT_DOI = "doi:10.7910/DVN/42MVDX"
FEC_CN_URL = "https://www.fec.gov/files/bulk-downloads/{year}/cn{yy}.zip"
# Each Federal Elections compilation has its own page, linked from this
# index; a year with no link has not been published.
FEC_RESULTS_INDEX = "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/"
FEC_HOUSE_SHEET = "US House Results by State"


def fetch(url: str, dest: Path) -> bytes:
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
    return dest.read_bytes()


def medsl_file(doi: str) -> tuple[str, str]:
    """(download URL, file name) of the data file in the newest version of a
    MEDSL Dataverse dataset: the one named for its year span
    ("1976-2024-senate-state"), in its original format."""
    req = urllib.request.Request(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={doi}",
                                 headers={"User-Agent": BOT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        version = json.loads(resp.read())["data"]["latestVersion"]
    for f in version["files"]:
        df = f["dataFile"]
        name = df.get("originalFileName") or df["filename"]
        if re.match(r"\d{4}-\d{4}-", name) and name.endswith((".csv", ".tab")):
            original = "?format=original" if df.get("originalFileName") else ""
            return f"{DATAVERSE}/api/access/datafile/{df['id']}{original}", name
    raise SystemExit(f"{doi}: no year-span data file in version {version.get('versionNumber')}")


def fetch_medsl(doi: str, cache: Path) -> tuple[str, str]:
    """(text, file name) of a MEDSL dataset's newest data file, cached by name."""
    url, name = medsl_file(doi)
    return fetch(url, cache / f"medsl-{name}").decode(), name


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
    older, newer = [y for y in sorted(pres) if y < year][-2:]
    return model.two_party_lean(pres[older][state], pres[newer][state], pres[older]["US"], pres[newer]["US"])


def senate_races(text: str) -> list[dict]:
    """Contested November generals of every even year in MEDSL's file: the
    top two are a D and an R."""
    votes: dict[tuple, dict[str, list]] = defaultdict(dict)
    for r in csv.DictReader(io.StringIO(text)):
        y = int(r["year"])
        if y % 2 or r["stage"].lower() != "gen" or r["mode"].lower() != "total":
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


def incumbents(cache: Path, years) -> dict[tuple[int, str], list[tuple[str, str]]]:
    """{(year, state): [(party D/R, surname)]} of Senate candidacies the FEC
    codes as incumbent for that election."""
    out: dict = defaultdict(list)
    for y in years:
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


_PARTISAN = {"D": "D", "DEM": "D", "R": "R", "REP": "R"}


def polls(doc: dict) -> tuple[dict, dict, dict, dict]:
    """From the training-polls file: ({(year, state, special): [Senate poll
    rows]}, {year: [generic poll rows]}, {(year, "ST-N"): [House poll
    rows]}, {(year, state, special): Silver Bulletin's stated R margin}).
    Silver Bulletin numbers districts from 1, so an at-large seat is "AK-1"
    there and "AK-0" here. A sponsor marked as both parties is no party's."""
    races: dict = defaultdict(list)
    generic: dict = defaultdict(list)
    house: dict = defaultdict(list)
    stated: dict = {}
    for r in tp.as_dicts(doc):
        year = tp.cycle_of(r)
        if year is None:
            continue
        pct = {r["cand1_party"]: float(r["cand1_pct"]), r["cand2_party"]: float(r["cand2_pct"])}
        if set(pct) != {"DEM", "REP"}:
            continue
        n = int(float(r["samplesize"])) if r["samplesize"] not in ("", "NA") else None
        row = model.Poll(r["pollster"], tp.days_out(r), n, round(pct["REP"] - pct["DEM"], 2),
                         _PARTISAN.get(r["partisan"], "")).as_row()
        kind = r["type_detail"]
        if kind == "House-G" and r["location"] == tp.GENERIC_LOCATION:
            generic[str(year)].append(row)
        elif kind == "House-G":
            house[(year, r["location"])].append(row)
        else:
            key = (year, r["location"], kind == "Sen-GS")
            races[key].append(row)
            if tp.stated_margin(r) is not None:
                stated[key] = tp.stated_margin(r)
    return races, generic, house, stated


def polls_file(path: Path | None) -> tuple[dict, str]:
    """The training-polls file and where it was read: the given one, else
    the copy in use (the data volume's, then the checked-in one)."""
    for p in ([path] if path else [Path(runtime_data_path(tp.FILE_NAME)), DATA / tp.FILE_NAME]):
        if p.exists():
            return json.loads(p.read_text()), str(p)
    raise SystemExit("no training-polls file (app/data/forecast_polls.json)")


def poll_source(doc: dict) -> str:
    f = doc.get("file") or {}
    return (f"Polls: {tp.ATTRIBUTION}, pollster-ratings raw polls ({f.get('label', 'Rawpolls')}, retrieved "
            f"{doc.get('retrieved')}, sha256 {str(f.get('sha256', ''))[:12]}), used free of charge as its terms "
            f"allow, attributed to {tp.ATTRIBUTION}.")


# ---------------------------------------------------------------------------
# The House (forecast_house_training.json, forecast_house_model.json)
# ---------------------------------------------------------------------------

_D_PARTIES = {"D", "DEM", "DFL"}
_R_PARTIES = {"R", "REP"}


def fec_house_workbooks(years, cache: Path) -> dict[int, bytes]:
    """{year: workbook} of every Federal Elections compilation the FEC has
    published for `years`: each year's page, as the results index links it,
    and the federalelections<year>.xlsx that page links. A year the index
    doesn't link is unpublished (2024's, as of 2026-10)."""
    index = fetch(FEC_RESULTS_INDEX, cache / "fec-results-index.html").decode("utf-8", "replace")
    out = {}
    for year in years:
        page = re.search(r'href="([^"]*federal-elections-%d/?)"' % year, index)
        if not page:
            continue
        url = page.group(1) if page.group(1).startswith("http") else "https://www.fec.gov" + page.group(1)
        html = fetch(url, cache / f"fec-federal-elections-{year}.html").decode("utf-8", "replace")
        book = re.search(r'href="([^"]*federalelections%d\.xlsx)"' % year, html)
        if not book:
            raise SystemExit(f"FEC Federal Elections {year}: its page links no federalelections{year}.xlsx")
        href = book.group(1) if book.group(1).startswith("http") else "https://www.fec.gov" + book.group(1)
        out[year] = fetch(href, cache / f"federalelections{year}.xlsx")
    return out


def _house_sheet(raw: bytes) -> str:
    """The workbook's House results sheet ("13. US House Results by State")."""
    wb = zipfile.ZipFile(io.BytesIO(raw)).read("xl/workbook.xml").decode()
    names = [n for n in re.findall(r'<sheet\b[^>]*name="([^"]+)"', wb) if n.endswith(FEC_HOUSE_SHEET)]
    if len(names) != 1:
        raise SystemExit(f"FEC workbook: {len(names)} sheets named '... {FEC_HOUSE_SHEET}'")
    return names[0]


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
    rows = _xlsx_rows(xlsx_sheet_as_first(raw, _sheet_file(raw, sheet)))
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


def fit_house(cache: Path, house_polls: dict, generic: dict, senate: tuple, poll_doc: dict,
              training_out: Path, model_out: Path) -> None:
    leans = json.loads(HOUSE_LEAN.read_text())
    workbooks = fec_house_workbooks(sorted(int(y) for y in leans["leans"]), cache)
    rows, missing = [], []
    for year, raw in sorted(workbooks.items()):
        lean = leans["leans"][str(year)]
        lines = leans["lines"][leans["_method"][str(year)].split("th", 1)[0]]
        at_large = {k.split("-")[0] for k in lines if k.endswith("-0")}
        states = {k.split("-")[0] for k in lines}
        for race in fec_house(raw, _house_sheet(raw)):
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
    # A cycle is fitted only with its generic-ballot polls (the national term).
    rows = [r for r in rows if str(r["year"]) in generic]
    per_year = {y: sum(1 for r in rows if r["year"] == y) for y in sorted({r["year"] for r in rows})}
    print(f"House: {len(rows)} races, {sum(1 for r in rows if r['polls'])} polled; per year {per_year}; "
          f"no lean for {missing}")
    if missing or not per_year or any(v < 300 for v in per_year.values()):
        raise SystemExit("House: a results row has no lean, or a cycle parsed too few contested races")
    gen = {y: v for y, v in generic.items() if int(y) in per_year}
    years = sorted(per_year)
    source = (f"Results and incumbency: Federal Election Commission, Federal Elections {_years(years)}, "
              "US House Results by State (certified results, public domain). Lean: app/data/forecast_house_lean.json "
              "(Civitas, from VEST 2016 and 2020 precinct results, CC BY 4.0, and Census blocks and districts), "
              f"each district's lean before the election on the lines it was held on. {poll_source(poll_doc)}")
    training_out.write_text(json.dumps({
        "_source": source, "_regenerate": REGENERATE,
        "_license": f"Poll rows derived from {tp.ATTRIBUTION}'s raw polls (free of charge for any purpose, "
                    f"attributed to {tp.ATTRIBUTION}); leans derived from VEST, CC BY 4.0; results public domain.",
        "_columns": {"polls": "[pollster, days before election day, sample size, R-D margin, partisan sponsor]"},
        # The cycles each input reaches (build.check_training_staleness).
        "_coverage": {"results": sorted(workbooks), "lean": sorted(int(y) for y in leans["leans"]),
                      "polls": sorted(int(y) for y in generic)},
        "races": rows, "generic": dict(sorted(gen.items())),
    }, separators=(",", ":")) + "\n")
    write_model(model_out, source, rows, gen, borrow=senate, poll_doc=poll_doc)


def _years(years: list[int]) -> str:
    return ", ".join(str(y) for y in years[:-1]) + f" and {years[-1]}" if len(years) > 1 else str(years[0])


def write_model(path: Path, source: str, rows: list[dict], generic: dict, borrow: tuple | None = None,
                poll_doc: dict | None = None) -> dict:
    """Fit and evaluate one chamber. `borrow` is (rows, generic) of the
    chamber whose national coefficient this one takes (model.fit): the
    full fit's for the published parameters, for each held-out cycle a fit
    without that cycle, and for the forward check a fit on the cycles
    before it only."""
    out = {"_source": source, "_regenerate": REGENERATE, "_method": "app/forecast/model.py (module docstring)",
           "fits": {}, "evaluation": {}}
    if poll_doc is not None:
        # Which training-polls file this fit read: app.data_refresh refits
        # when the copy in use differs.
        f = poll_doc.get("file") or {}
        out["_polls"] = {"sha256": f.get("sha256"), "label": f.get("label"), "retrieved": poll_doc.get("retrieved")}
    if borrow:
        out["_nationalCoefficient"] = ("the Senate fit's generic-ballot coefficient at the same horizon "
                                       "(model.fit, national_coef): a few House cycles cannot identify it")
    for h in model.HORIZONS:
        coef = coef_for = coef_before = None
        if borrow:
            b_rows, b_gen = borrow
            coef = model.fit(b_rows, b_gen, h)["beta"][3]

            def coef_for(year, h=h):
                return model.fit([r for r in b_rows if r["year"] != year],
                                 {y: v for y, v in b_gen.items() if int(y) != year}, h)["beta"][3]

            def coef_before(year, h=h):
                return model.fit([r for r in b_rows if r["year"] < year],
                                 {y: v for y, v in b_gen.items() if int(y) < year}, h)["beta"][3]
        out["fits"][str(h)] = model.fit(rows, generic, h, coef)
        ev = model.leave_one_cycle_out(rows, generic, h, coef_for)
        ev["forward"] = model.forward(rows, generic, h, coef_before)
        out["evaluation"][str(h)] = ev
        keys = ("races", "polledRaces", "all", "polled", "coverage80", "coverage95", "meanAbsError")
        print(f"{path.name} horizon {h}: {json.dumps({k: ev[k] for k in keys})}")
        if ev["forward"]:
            fw = ev["forward"]
            print(f"   forward {fw['cycle']} (fitted on {fw['trainedOn']}): races {fw['races']}, coverage80 "
                  f"{fw['coverage80']} (band {fw['coverage80Band']}), coverage95 {fw['coverage95']}, "
                  f"polled {json.dumps(fw['polled'])}")
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


def senate_rows(cache: Path, poll_doc: dict) -> tuple[list[dict], dict, dict, dict]:
    """(training rows, generic polls, House polls, coverage) for the Senate."""
    pres_text, _ = fetch_medsl(PRESIDENT_DOI, cache)
    pres = presidential_counts(pres_text)
    senate_text, _ = fetch_medsl(SENATE_DOI, cache)
    results = senate_races(senate_text)
    race_polls, generic, house_polls, stated = polls(poll_doc)
    poll_years = {y for (y, _, _) in race_polls} & {int(y) for y in generic}
    years = sorted(poll_years & {r["year"] for r in results})
    inc = incumbents(cache, years)
    rows, excluded = [], []
    for race in results:
        key = (race["year"], race["state"], race["special"])
        if race["year"] not in years:
            continue
        if key in stated and abs(stated[key] - race["margin"]) > tp.RESULT_TOLERANCE:
            excluded.append({"year": race["year"], "state": race["state"], "special": race["special"],
                             "medsl": race["margin"], "silverBulletin": round(stated[key], 3)})
            continue
        rows.append({"year": race["year"], "state": race["state"], "special": race["special"],
                     "margin": race["margin"], "lean": round(lean_before(pres, race["year"], race["state"]), 3),
                     "inc": incumbent_code(race, inc), "polls": race_polls.get(key, [])})
    unmatched = sorted(k for k in race_polls if k[0] in years
                       and k not in {(r["year"], r["state"], r["special"]) for r in rows})
    per_year = {y: sum(1 for r in rows if r["year"] == y) for y in years}
    print(f"{len(rows)} races, {sum(1 for r in rows if r['polls'])} polled; per year {per_year}")
    print(f"poll races with no contested-race row (not D-vs-R top two, or excluded): {unmatched}")
    print(f"excluded, Silver Bulletin's stated result and MEDSL's count disagree: {excluded}")
    if not years or any(v < 25 for v in per_year.values()):
        raise SystemExit("a cycle has fewer than 25 contested races: the results file did not parse")
    coverage = {"results": sorted({r["year"] for r in results}), "polls": sorted(poll_years)}
    gen = {y: v for y, v in generic.items() if int(y) in years}
    return rows, dict(sorted(gen.items())), {"house": house_polls, "generic": generic, "excluded": excluded}, coverage


def run(cache: Path, polls_path: Path | None = None, senate_training: Path = TRAINING, senate_model: Path = MODEL,
        house_training: Path = HOUSE_TRAINING, house_model: Path = HOUSE_MODEL) -> None:
    poll_doc, read_from = polls_file(polls_path)
    print(f"training polls: {read_from}")
    rows, generic, extra, coverage = senate_rows(cache, poll_doc)
    years = sorted({r["year"] for r in rows})
    source = (f"Results: MIT Election Data and Science Lab, U.S. Senate statewide (doi:10.7910/DVN/PEJ5QU, CC0), "
              f"{years[0]}-{years[-1]}. Lean: MEDSL U.S. President (doi:10.7910/DVN/42MVDX, CC0), Cook's formula "
              f"over the two presidential elections before each race. Incumbency: FEC candidate master files "
              f"(public domain). {poll_source(poll_doc)}")
    training = {
        "_source": source, "_regenerate": REGENERATE,
        "_license": f"Poll rows derived from {tp.ATTRIBUTION}'s raw polls (free of charge for any purpose, "
                    f"attributed to {tp.ATTRIBUTION}); results and lean inputs CC0 / public domain.",
        "_columns": {"polls": "[pollster, days before election day, sample size, R-D margin, partisan sponsor]"},
        "_coverage": coverage,
        "_excluded": extra["excluded"],
        "races": rows, "generic": generic,
    }
    senate_training.write_text(json.dumps(training, separators=(",", ":")) + "\n")
    write_model(senate_model, source, rows, generic, poll_doc=poll_doc)
    fit_house(cache, extra["house"], extra["generic"], (rows, generic), poll_doc, house_training, house_model)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-forecast")
    ap.add_argument("--polls", type=Path, default=None, help="training-polls file (default: the copy in use)")
    ap.add_argument("--senate-training", type=Path, default=TRAINING)
    ap.add_argument("--senate-model", type=Path, default=MODEL)
    ap.add_argument("--house-training", type=Path, default=HOUSE_TRAINING)
    ap.add_argument("--house-model", type=Path, default=HOUSE_MODEL)
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    run(args.cache, args.polls, args.senate_training, args.senate_model, args.house_training, args.house_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
