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
  * House results: the FEC's Federal Elections workbooks (US House Results
    by State, public domain), found through the FEC's results index; for a
    year the FEC has not compiled, the Clerk of the House's Statistics of
    the Presidential and Congressional Election (public domain), found on
    the Clerk's Election Information page, with incumbency from the FEC's
    list of general-election candidates (clerk_house, clerk_results).
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
import asyncio
import csv
import io
import json
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pdfplumber
from pdfplumber.utils.text import chars_to_textmap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.atomic_write import runtime_data_path  # noqa: E402
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.forecast import model  # noqa: E402
from app.forecast import training_polls as tp  # noqa: E402
from app.http_client import make_async_client  # noqa: E402
from app.pipeline.fetch.election_results import ranked_choice_states  # noqa: E402
from app.pipeline.fetch.http_utils import DEFAULT_MAX_RETRIES, fetch_with_retry  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows, xlsx_sheet_as_first  # noqa: E402
from app.pipeline.rate_limiter import RateLimiter  # noqa: E402
from app.state_names import STATE_NAMES  # noqa: E402

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
# The FEC's list of each general election's congressional candidates
# ("<year>congressgecands.xlsx", with its incumbent mark), linked from this
# page soon after the election: the Clerk's results carry no incumbency.
FEC_ELECTION_INFO = "https://www.fec.gov/introduction-campaign-finance/election-and-voting-information/"
# The Clerk of the House's Election Information page, which links the newest
# "Statistics of the Presidential and Congressional Election" (official
# results, public domain): the House's results for a year the FEC has not
# compiled yet.
CLERK = "https://clerk.house.gov"
CLERK_ELECTION_INFO = CLERK + "/Members/ViewElectionInformation"


# Every download goes through the backend's retrying fetch: a dropped
# connection (the FEC's bulk files did it twice in one refit, 2026-10-17)
# is retried with backoff instead of failing the whole refit.
_LIMITER = RateLimiter(2.0, name="fit_forecast_model")
DOWNLOAD_TIMEOUT_S = 120.0


def download(url: str) -> bytes:
    """The body at `url`, retried with backoff (http_utils.fetch_with_retry);
    stops the run once the retries are spent, never returning an empty body."""
    async def get() -> bytes | None:
        async with make_async_client(headers={"User-Agent": BOT_USER_AGENT}) as client:
            resp = await fetch_with_retry(client, _LIMITER, "GET", url, timeout=DOWNLOAD_TIMEOUT_S)
            return None if resp is None else resp.content
    body = asyncio.run(get())
    if body is None:
        raise SystemExit(f"{url}: no answer after {DEFAULT_MAX_RETRIES} attempts")
    return body


def fetch(url: str, dest: Path) -> bytes:
    if not dest.exists():
        dest.write_bytes(download(url))
    return dest.read_bytes()


def medsl_file(doi: str) -> tuple[str, str]:
    """(download URL, file name) of the data file in the newest version of a
    MEDSL Dataverse dataset: the one named for its year span
    ("1976-2024-senate-state"), in its original format."""
    version = json.loads(download(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={doi}"))["data"]["latestVersion"]
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

# FEC party codes. A cell can join several ("D/IP": a fusion nominee;
# "W(R)/R": nominated by primary write-ins) and carry a mark ("R*").
_D_PARTIES = {"D", "DEM", "DFL", "DNL"}
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
    every line summed: keyed by FEC ID and surname together, since the
    workbook sometimes repeats one candidate's ID on an opponent's row
    (2020 TN-7, 2022 IL-3). A district holding a special election the same
    day lists its general as "NN-Full Term"."""
    rows = _xlsx_rows(xlsx_sheet_as_first(raw, _sheet_file(raw, sheet)))
    vcol = next(k for k in rows[0] if k.strip() == "GENERAL VOTES")
    icol = next(k for k in rows[0] if k.startswith("(I)"))
    races: dict = defaultdict(dict)
    for r in rows:
        st = r.get("STATE ABBREVIATION", "").strip()
        full = re.fullmatch(r"(\d\d)(?:\s*-\s*full term)?", r.get("DISTRICT", "").strip(), re.I)
        if not full or not st:
            continue  # totals, headers, unexpired-term specials ("05 - UNEXPIRED TERM")
        dist = full.group(1)
        if not (r.get("CANDIDATE NAME") or "").strip() or (r.get("TOTAL VOTES") or "").strip():
            continue  # "Party Votes:" / "District Votes:" total rows
        # A ranked-choice count (Maine, Alaska from 2022) is its last round
        # where the workbook gives the rounds; 2018's gives only the first.
        rounds = [r[k] for k in sorted(k for k in r if "ROUND RCV VOTES" in k) if re.fullmatch(r"\d+", (r[k] or "").strip())]
        votes = re.sub(r"[,\s]", "", rounds[-1] if rounds else (r.get(vcol) or ""))
        if not re.fullmatch(r"\d+", votes):
            continue  # "Unopposed", blank, write-in notes
        fec = r.get("FEC ID#") or r.get("FEC ID") or ""
        surname = (r.get("CANDIDATE NAME (Last)") or "").strip().upper()
        name = (fec, surname) if fec not in ("", "n/a") else r.get("CANDIDATE NAME", "").strip()
        cand = races[(st, int(dist))].setdefault(name, {"votes": 0, "parties": set(), "inc": False})
        cand["votes"] += int(votes)
        cand["parties"] |= {p.strip("*") for p in (r.get("PARTY") or "").strip().upper().split("/")}
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


def clerk_statistics(years, cache: Path) -> dict[int, bytes]:
    """{year: PDF} of each of `years` whose "Statistics of the Presidential
    and Congressional Election" the Clerk's Election Information page links
    (statistics<year>.pdf). The page links the newest edition only; a year
    it doesn't link is not read from the Clerk."""
    page = fetch(CLERK_ELECTION_INFO, cache / "clerk-election-information.html").decode("utf-8", "replace")
    out = {}
    for year in years:
        link = re.search(r'href="([^"]*statistics%d\.pdf)"' % year, page)
        if not link:
            continue
        href = link.group(1) if link.group(1).startswith("http") else CLERK + link.group(1)
        raw = fetch(href, cache / f"clerk-statistics{year}.pdf")
        if not raw.startswith(b"%PDF"):
            raise SystemExit(f"Clerk's Statistics {year}: {href} is not a PDF")
        out[year] = raw
    return out


def _letters(name: str) -> str:
    """A name's letters alone, accents folded, for matching across sources
    ("D’Esposito", "D'Esposito")."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", folded.upper())


def fec_general_candidates(year: int, cache: Path) -> dict[tuple[str, int], list[tuple[str, str, bool]]]:
    """{(state, district): [(party D/R/other, surname letters, incumbent)]}
    from the FEC's list of the year's congressional general-election
    candidates. Stops when the FEC's page doesn't link it: without it the
    Clerk's results have no incumbency."""
    page = fetch(FEC_ELECTION_INFO, cache / "fec-election-information.html").decode("utf-8", "replace")
    link = re.search(r'href="([^"]*/%dcongressgecands\.xlsx)"' % year, page)
    if not link:
        raise SystemExit(f"FEC: {FEC_ELECTION_INFO} links no {year}congressgecands.xlsx (candidates and incumbents)")
    href = link.group(1) if link.group(1).startswith("http") else "https://www.fec.gov" + link.group(1)
    raw = fetch(href, cache / f"{year}congressgecands.xlsx")
    sheets = sorted(re.findall(r"xl/worksheets/(sheet\d+)\.xml", " ".join(zipfile.ZipFile(io.BytesIO(raw)).namelist())))
    out: dict = defaultdict(list)
    # One sheet per state.
    for r in (row for sheet in sheets for row in _xlsx_rows(xlsx_sheet_as_first(raw, sheet))):
        st, dist = (r.get("STATE ABBREVIATION") or "").strip(), (r.get("DISTRICT") or "").strip()
        if not st or not re.fullmatch(r"\d\d", dist):
            continue  # blank separators; Senate rows ("S")
        codes = {p.strip("*") for p in (r.get("PARTY") or "").strip().upper().split("/")}
        side = "D" if codes & _D_PARTIES else "R" if codes & _R_PARTIES else "X"
        out[(st, int(dist))].append((side, _letters(r.get("CANDIDATE NAME (Last)") or ""),
                                     (r.get("(I)") or "").strip() == "(I)"))
    if not out:
        raise SystemExit(f"FEC {year}congressgecands.xlsx: no House candidates read")
    return out


def _upright(char: dict, page) -> dict:
    """A character of a page set sideways (the wide recapitulation tables),
    placed as it reads with the page turned upright."""
    if char["matrix"][1] > 0:  # reads bottom to top
        return dict(char, x0=page.height - char["bottom"], x1=page.height - char["top"],
                    top=char["x0"], bottom=char["x1"], upright=True)
    return dict(char, x0=char["top"], x1=char["bottom"], top=page.width - char["x1"],
                bottom=page.width - char["x0"], upright=True)


def clerk_lines(raw: bytes) -> list[tuple[float, str, bool]]:
    """(left edge, text, noted) of every line of a Statistics PDF, in
    reading order. Footnote marks (set at half the text's size) are taken
    out of the text, since one printed against a count reads as its leading
    digit ("1146,142"), and `noted` says the line had one."""
    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            upright = [c for c in page.chars if c["upright"]]
            turned = [_upright(c, page) for c in page.chars if not c["upright"]]
            for chars in (upright, turned):
                if not chars:
                    continue
                heights = [round(c["bottom"] - c["top"], 1) for c in chars]
                body = max(set(heights), key=heights.count)
                marks = [c for c in chars if c["bottom"] - c["top"] <= 0.75 * body]
                text = [c for c in chars if c["bottom"] - c["top"] > 0.75 * body]
                for ln in chars_to_textmap(text).extract_text_lines(return_chars=False):
                    noted = any(ln["top"] - 3 <= m["top"] and m["bottom"] <= ln["bottom"] + 3 for m in marks)
                    out.append((ln["x0"], ln["text"], noted))
    return out


# "1. Barry Moore, Republican ........ 258,619"; a nominee unopposed and not
# on the ballot has no count (a footnote mark in its place).
_CLERK_ENTRY = re.compile(r"^(?:(\d+)\.\s+)?(\S.*?)\s*\.{2,}\s*(\d{1,3}(?:,\d{3})*)?$")
# A recapitulation row: "1st district .... 258,619 70,929 .... 306 329,854",
# its last figure the district's total.
_CLERK_RECAP = re.compile(r"^(?:(\d+)(?:st|nd|d|rd|th) district|At large)\s*\.+(?:\s.*?)?\s([\d,]+)$")
_CLERK_D = {"Democrat", "Democratic", "Democratic-Farmer-Labor", "Democratic-Nonpartisan League"}
_CLERK_R = {"Republican"}
_STATE_BY_NAME = {name.upper(): st for st, name in STATE_NAMES.items()}


def clerk_house(lines: list[tuple[float, str, bool]], ranked_choice=frozenset()) -> tuple[list[dict], set, list[str]]:
    """(contested D-vs-R races, every district listed, races left out) from
    a Statistics PDF's lines (clerk_lines): the same race shape as
    fec_house, with the nominees' names for the incumbency match (the
    Clerk doesn't mark incumbents).

    A candidate's indented lines beneath their name are the other parties
    that nominated them (New York's and Connecticut's fusion lines) and
    count toward them; a line at the name's indent with no party ("Blank",
    "Write-in", "Exhausted Ballots") is a tally of no candidate. Every
    district's lines must add up to the total the Clerk's own
    recapitulation table gives it, or the run stops: a misread line would
    otherwise move a margin silently. A special election for an unexpired
    term is not the general. A ranked-choice state's race in which nobody
    has a majority is left out unless the Clerk prints its last round (the
    "Exhausted Ballots" tally): its first choices are not the result."""
    state = section = block = None
    special = False
    table = 0
    blocks: dict = defaultdict(list)
    recap: dict = {}
    for x, text, noted in lines:
        head = re.sub(r"\s*—\s*Continued$", "", text.strip())
        if head in _STATE_BY_NAME:
            state, section, special = _STATE_BY_NAME[head], None, False
            continue
        if head.startswith("Recapitulation of Votes Cast in "):
            state = _STATE_BY_NAME.get(head.removeprefix("Recapitulation of Votes Cast in ").upper())
            section, special = "recap", False
            continue
        if head.startswith("FOR "):
            section, special = ("house" if "UNITED STATES REPRESENTATIVE" in head else "other"), False
            continue
        if head.startswith("(For unexpired term"):
            special = True
            continue
        if head.startswith("(For full term"):
            special = False
            continue
        if section == "recap" and state:
            # A wide table continues in a second one below it (its own
            # "Title of candidate" header); the total is the last one's.
            table += head.startswith("Title of")
            m = _CLERK_RECAP.match(head)
            if m and not special:
                key = (state, int(m.group(1) or 0))
                if recap.get(key, (None,))[0] == table:
                    raise SystemExit(f"Clerk's Statistics: two recapitulation rows for {key}")
                recap[key] = (table, int(m.group(2).replace(",", "")))
            continue
        if section != "house" or state is None:
            continue
        if head == "AT LARGE":
            block = {"key": (state, 0), "special": special, "entries": []}
            blocks[block["key"]].append(block)
            continue
        m = _CLERK_ENTRY.match(head)
        if not m:
            continue
        number, label, figure = m.groups()
        if number:
            block = {"key": (state, int(number)), "special": special, "entries": []}
            blocks[block["key"]].append(block)
        if block is None or block["key"][0] != state:
            raise SystemExit(f"Clerk's Statistics: {text!r} belongs to no district")
        block["entries"].append((round(x), bool(number), label, int(figure.replace(",", "")) if figure else 0, noted))
    # A name's indent: the commonest left edge among unnumbered entries. A
    # line well right of it continues the name above.
    entry_x = [e[0] for bs in blocks.values() for b in bs for e in b["entries"] if not e[1]]
    name_x = max(set(entry_x), key=entry_x.count) if entry_x else 0
    out, left_out = [], []
    for key, listed in blocks.items():
        general = [b for b in listed if not b["special"]]
        if len(general) != 1:
            raise SystemExit(f"Clerk's Statistics: {len(general)} general-election listings for {key}")
        b = {"cands": [], "tally": 0, "rounds": False, "noted": False}
        for x, numbered, label, votes, noted in general[0]["entries"]:
            name, *parties = [p.strip() for p in label.split(",")]
            if not numbered and x > name_x + 5 and b["cands"]:
                b["cands"][-1]["votes"] += votes
                b["cands"][-1]["parties"] |= {name, *parties}
                b["noted"] |= noted
            elif parties:
                b["cands"].append({"name": name, "votes": votes, "parties": set(parties)})
                b["noted"] |= noted
            else:
                b["tally"] += votes
                b["rounds"] |= name.startswith("Exhausted Ballot")
        counted = sum(c["votes"] for c in b["cands"]) + b["tally"]
        total = recap.get(key, (None, 0))[1]
        if counted != total:
            raise SystemExit(f"Clerk's Statistics: {key[0]}-{key[1]}'s lines add up to {counted:,}; its "
                             f"recapitulation row says {total:,}")
        top = sorted(b["cands"], key=lambda c: -c["votes"])[:2]
        if len(top) < 2 or not top[1]["votes"]:
            continue
        side = {("D" if c["parties"] & _CLERK_D else "R" if c["parties"] & _CLERK_R else "X"): c for c in top}
        if set(side) != {"D", "R"}:
            continue
        if b["noted"] and not b["rounds"]:
            left_out.append(f"{key[0]}-{key[1]}: a count carries a footnote (a runoff's count, a recount)")
            continue
        if (key[0] in ranked_choice and not b["rounds"]
                and 2 * top[0]["votes"] <= sum(c["votes"] for c in b["cands"])):
            left_out.append(f"{key[0]}-{key[1]}: ranked-choice with no majority, and only first choices printed")
            continue
        d, rep = side["D"], side["R"]
        out.append({"state": key[0], "district": key[1],
                    "margin": round(100 * (rep["votes"] - d["votes"]) / (rep["votes"] + d["votes"]), 3),
                    "names": {"D": d["name"], "R": rep["name"]}})
    return out, set(blocks), left_out


def fit_house(cache: Path, house_polls: dict, generic: dict, senate: tuple, poll_doc: dict,
              training_out: Path, model_out: Path) -> None:
    leans = json.loads(HOUSE_LEAN.read_text())
    years = sorted(int(y) for y in leans["leans"])
    workbooks = fec_house_workbooks(years, cache)
    results = {year: fec_house(raw, _house_sheet(raw)) for year, raw in workbooks.items()}
    left_out = {}
    for year, raw in clerk_statistics([y for y in years if y not in workbooks], cache).items():
        results[year], left_out[str(year)] = clerk_results(year, raw, _lines(leans, year), cache)
        print(f"House {year}: the Clerk's Statistics, {len(results[year])} contested races; left out {left_out[str(year)]}")
    rows, missing = [], []
    for year, races in sorted(results.items()):
        lean = leans["leans"][str(year)]
        lines = _lines(leans, year)
        at_large = {k.split("-")[0] for k in lines if k.endswith("-0")}
        states = {k.split("-")[0] for k in lines}
        for race in races:
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
    fec_years = [y for y in years if y in workbooks]
    clerk_years = [y for y in years if y not in workbooks]
    source = (f"Results and incumbency: Federal Election Commission, Federal Elections {_years(fec_years)}, "
              "US House Results by State (certified results, public domain)"
              + (f"; for {_years(clerk_years)}, which the FEC has not compiled yet, the Clerk of the House's "
                 f"Statistics of the Presidential and Congressional Election (official results, public domain), "
                 f"with incumbency from the FEC's list of general-election candidates" if clerk_years else "")
              + ". Lean: app/data/forecast_house_lean.json "
              "(Civitas, from VEST 2016 and 2020 precinct results, CC BY 4.0, and Census blocks and districts), "
              f"each district's lean before the election on the lines it was held on. {poll_source(poll_doc)}")
    training_out.write_text(json.dumps({
        "_source": source, "_regenerate": REGENERATE,
        "_license": f"Poll rows derived from {tp.ATTRIBUTION}'s raw polls (free of charge for any purpose, "
                    f"attributed to {tp.ATTRIBUTION}); leans derived from VEST, CC BY 4.0; results public domain.",
        "_columns": {"polls": "[pollster, days before election day, sample size, R-D margin, partisan sponsor]"},
        # The cycles each input reaches (build.check_training_staleness).
        "_coverage": {"results": sorted(results), "lean": sorted(int(y) for y in leans["leans"]),
                      "polls": sorted(int(y) for y in generic)},
        # Contested races the Clerk's Statistics print but the fit leaves out (clerk_house).
        "_excluded": left_out,
        "races": rows, "generic": dict(sorted(gen.items())),
    }, separators=(",", ":")) + "\n")
    write_model(model_out, source, rows, gen, borrow=senate, poll_doc=poll_doc)


def _lines(leans: dict, year: int) -> dict:
    """The seats of the lines `year`'s House election was fought on."""
    return leans["lines"][leans["_method"][str(year)].split("th", 1)[0]]


def _same_name(surname: str, name: str) -> bool:
    """The FEC's surname for a candidate the Clerk names in full: contained
    in it, or within a letter or two of its last word (the FEC's
    "Petterson" for Pettersen, ratio 0.89; Ratcliff & Obershelp 1988)."""
    letters = _letters(name)
    last = _letters(name.split()[-1]) if name.split() else ""
    return bool(surname) and (surname in letters or SequenceMatcher(None, surname, last).ratio() >= 0.8)


def clerk_results(year: int, raw: bytes, lines: dict, cache: Path) -> tuple[list[dict], list[str]]:
    """(contested races with incumbency, races left out) from one edition of
    the Clerk's Statistics, gated: the edition lists every seat of the
    lines the election was fought on exactly once (clerk_house stops on a
    second listing), and every incumbent the FEC lists on a contested
    district's general ballot is its party's nominee. A misread
    district or a shifted line breaks the second at once; a nominee the
    FEC misspells or omits does not (2024: 2 of 794)."""
    races, listed, left_out = clerk_house(clerk_lines(raw), ranked_choice_states())
    states = {k.split("-")[0] for k in lines}
    seats = {(k.split("-")[0], int(k.split("-")[1])) for k in lines}
    listed = {k for k in listed if k[0] in states}  # not the delegates
    if listed != seats:
        raise SystemExit(f"Clerk's Statistics {year}: seats not listed {sorted(seats - listed)}, listed but not "
                         f"seats {sorted(listed - seats)}")
    candidates = fec_general_candidates(year, cache)
    unmatched, wrong = [], []
    for race in races:
        on_ballot = candidates.get((race["state"], race["district"]), [])
        names = race.pop("names")
        unmatched += [f"{race['state']}-{race['district']} {names[side]}" for side in "DR"
                      if not any(c[0] == side and _same_name(c[1], names[side]) for c in on_ballot)]
        race["inc"] = 0
        for side, surname, incumbent in on_ballot:
            if not incumbent:
                continue
            if side in names and _same_name(surname, names[side]):
                race["inc"] = 1 if side == "R" else -1
            else:
                wrong.append(f"{race['state']}-{race['district']} incumbent {surname} ({side}) vs {names}")
    print(f"House {year}: nominees not on the FEC's candidate list for their party: {unmatched}")
    if wrong:
        raise SystemExit(f"Clerk's Statistics {year}: the FEC's incumbent is neither nominee in {wrong}")
    return races, left_out


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
