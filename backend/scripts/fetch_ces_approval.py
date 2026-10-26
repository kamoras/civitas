"""Regenerate app/data/ces_member_approval.json: how each member's own
constituents rated them, split by the respondent's party, from the
Cooperative Election Study (CES) Common Content.

Every CES respondent is asked to rate their own House member and both
senators, named for them ("Do you approve of the way each is doing their
job?", CC24_312f/g/h for the House member and senators 1 and 2), and states
their party identification (pid3). That is the data the About page named as
missing for Constituent Alignment: approval of a member among their own
state's or district's Democrats, Republicans and independents, which the
roll-call record cannot supply. Informational, not scored.

Estimates:
  - Approval share among respondents who gave an opinion (strongly or
    somewhat approve, over those two plus the two disapprove options; "not
    sure" is counted separately), weighted by the survey's `commonweight`.
  - `n` is respondents; `n_eff` the Kish effective sample size of the
    weights, (sum w)^2 / sum w^2, which is what the sampling error follows.
  - House districts hold ~100 respondents, and a member's out-party
    respondents can number a dozen. Each (chamber, member's party,
    respondent's party) cell is therefore shrunk toward that cell's typical
    member by beta-binomial empirical Bayes: the prior mean is the n_eff-
    weighted mean approval, and its strength k (pseudo-respondents) comes
    from the method of moments, k = mu(1-mu)/tau^2 - 1, where tau^2 is the
    spread of true approval between members: the observed spread less the
    average sampling variance. The shrunk value is
    (rate*n_eff + mu*k) / (n_eff + k), and n_eff / (n_eff + k) is written
    as own_weight: how much of the figure is the member's own respondents.
    tau^2 is the weighted random-effects method-of-moments estimate (see
    priors()). Both the raw rate and the shrunk one
    are written, with mu and k, so any figure can be re-derived.

Members are matched to the survey by the respondent's state, district
(cdid118, the district the member served when the survey was fielded) and
the name the survey showed them (CurrentHouseName / CurrentSen1Name /
CurrentSen2Name); app/services/constituent_survey.py joins that to a member
record.

Data, by rule (each survey, odd years' smaller ones included): the newest
dataset on Harvard Dataverse named "... Common Content, <year>" (CC0), its
data file (the largest .csv or .tab), and its questionnaire (the document
naming the most question ids, CC24_312f and the like). The question ids
change every survey, so each new one is mapped to the survey in use by its
wording: for every question id, the grid's question plus the row the id
labels, as the questionnaire prints them; scored by the mean of difflib's
SequenceMatcher ratio and the all-MiniLM-L6-v2 cosine, and taken only when
each of the three approval items' best match is one-to-one and mutual
(the new item's best match among every question of the old survey is the
old item). The new survey's answers must then look like approval answers
(codes 1-5 on nearly every answered row, approval among opinions within
the bounds below, and as many members rated as the survey in use, give or
take a quarter). Anything short of that keeps the survey in use. The
district column is the one for the Congress in office when the survey was
fielded (cdid118 in 2024), from its own start times.

The 2024 Common Content (doi:10.7910/DVN/X11EP6, pre-election wave,
October-November 2024) was the first survey read; app/data_refresh.py
checks monthly for a newer one.

    cd backend && python3 scripts/fetch_ces_approval.py --in-use IN_USE --output OUT --cache DIR
    cd backend && python3 scripts/fetch_ces_approval.py --csv path/to/CCES24.csv   (the 2024 ids, offline)
"""

import argparse
import csv
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import numpy as np
import pdfplumber

csv.field_size_limit(sys.maxsize)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fetch_state_leg_crosswalk import _FIPS  # noqa: E402  (one state table)

from app.contact import USER_AGENT  # noqa: E402
from app.pipeline.vector_store import get_similarity_model  # noqa: E402

# Dataverse challenges browser-style User-Agents; USER_AGENT names Civitas,
# not a browser, and is let through (as scripts/fetch_peer_gdp.py's).

DATAVERSE = "https://dataverse.harvard.edu"
CES_DOI = "doi:10.7910/DVN/X11EP6"
CES_DATAFILE = 12050325  # CCES24_Common_OUTPUT_vv_topost_final.csv
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "ces_member_approval.json"

STATE_OF_FIPS = {int(fips): state for state, fips in _FIPS.items()}

# (approval item, name shown, party shown, chamber) per rated member: the
# 2024 survey's ids, which later surveys are mapped to by wording.
ITEMS = (
    ("CC24_312f", "CurrentHouseName", "CurrentHouseParty", "house"),
    ("CC24_312g", "CurrentSen1Name", "CurrentSen1Party", "senate"),
    ("CC24_312h", "CurrentSen2Name", "CurrentSen2Party", "senate"),
)
SLOTS = ("house", "senate1", "senate2")  # ITEMS in order
# Bounds on a mapped survey's answers. Approval of one's own members has run
# about 40-60% among opinions in every Common Content; outside 15-85% the
# column read is not approval. Nearly every answered row of an approval item
# is a code 1-5 (the rest skipped or not asked).
APPROVAL_BOUNDS = (0.15, 0.85)
CODED_SHARE = 0.95
MEMBERS_KEPT = 0.75
# The questionnaire's codes (CES 2024 guide): 1-2 approve, 3-4 disapprove,
# 5 not sure; pid3 1 Democrat, 2 Republican, anything else independent/other.
APPROVE, DISAPPROVE, NOT_SURE = {"1", "2"}, {"3", "4"}, {"5"}
RESPONDENT_PARTY = {"1": "D", "2": "R"}
MEMBER_PARTY = {"Democratic": "D", "Republican": "R"}


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=300) as r:
        return r.read()


def _api(path: str, **params) -> dict:
    return json.loads(_get(f"{DATAVERSE}/api/{path}?{urllib.parse.urlencode(params)}"))["data"]


def survey_year(name: str) -> int | None:
    """The year of a dataset named as a single Common Content ("CES Common
    Content, 2025", "2017 CCES Common Content"), else None (cumulative
    files, panels, anything else)."""
    if "common content" not in name.lower() or re.search(r"cumulative|panel", name, re.I):
        return None
    years = re.findall(r"\b(?:19|20)\d{2}\b", name)
    return int(years[0]) if len(years) == 1 else None


def newest_survey(items: list[dict]) -> tuple[int, str, str]:
    """(year, DOI, name) of the newest Common Content."""
    found = [(y, i["global_id"], i["name"]) for i in items if (y := survey_year(i.get("name", "")))]
    if not found:
        raise SystemExit("Dataverse lists no Common Content")
    return max(found)


def survey_files(doi: str) -> tuple[dict, list[dict]]:
    """(the data file, the documents) of a survey's latest published version."""
    files = [f["dataFile"] for f in _api("datasets/:persistentId/versions/:latest-published/files",
                                         persistentId=doi)]
    data = [f for f in files if f.get("filename", "").lower().endswith((".csv", ".tab"))]
    if not data:
        raise SystemExit(f"{doi} has no .csv or .tab data file")
    docs = [f for f in files if f.get("filename", "").lower().endswith((".pdf", ".docx"))]
    return max(data, key=lambda f: f.get("filesize") or 0), docs


def document_lines(name: str, body: bytes) -> list[str]:
    """A questionnaire's text, one line per paragraph (docx) or line (pdf)."""
    if name.lower().endswith(".docx"):
        xml = zipfile.ZipFile(io.BytesIO(body)).read("word/document.xml").decode("utf-8", "replace")
        lines = [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)]
    else:
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            lines = [line for page in pdf.pages for line in (page.extract_text() or "").splitlines()]
    return [line.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip() for line in lines if line.strip()]


_QID = re.compile(r"\b(CC\d{2}_\w+?)(?=-|\s|$)")


def wordings(lines: list[str]) -> dict[str, str]:
    """{question id: its wording}: the question the id's grid or page asks
    (the last line before it ending in "?"), the rest of the id's own line
    and the line after it (a grid row's label), with the survey's year
    taken out of ids it mentions."""
    out, stem = {}, ""
    for i, line in enumerate(lines):
        if line.endswith("?"):
            stem = line
        m = _QID.match(line)
        if m and m[1] not in out:
            rest = line[m.end():] + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            out[m[1]] = re.sub(r"\s+", " ", re.sub(r"CC\d{2}_", "CC_", f"{stem} {rest}")).strip().lower()
    return out


def questionnaire(docs: list[dict]) -> dict[str, str]:
    """Every question id the survey's documents name, with its wording: from
    the document naming the most ids where several name it (a codebook can
    list a grid's id without its rows; the questionnaire names each row)."""
    found = [wordings(document_lines(d["filename"], _get(f"{DATAVERSE}/api/access/datafile/{d['id']}")))
             for d in docs]
    merged: dict[str, str] = {}
    for words in sorted(found, key=len, reverse=True):
        merged.update({q: w for q, w in words.items() if q not in merged})
    if not merged:
        raise SystemExit("no document of the survey names its question ids")
    return merged


def map_items(old: dict[str, str], new: dict[str, str], old_ids: list[str], encode) -> dict[str, str] | str:
    """{old id: new id} for `old_ids` by wording, or why they can't be
    mapped. Score: the mean of SequenceMatcher's ratio and the embedding
    cosine (`encode` returns unit vectors). Each old item's best new item
    must be one-to-one and mutual."""
    o_ids, n_ids = list(old), list(new)
    o_vec, n_vec = encode([old[i] for i in o_ids]), encode([new[i] for i in n_ids])
    cos = np.asarray(o_vec) @ np.asarray(n_vec).T

    def score(oi: int, ni: int) -> float:
        return (float(cos[oi, ni]) + SequenceMatcher(None, old[o_ids[oi]], new[n_ids[ni]]).ratio()) / 2

    def best_new(oi: int) -> int:
        top = np.argsort(-cos[oi])[:20]  # the ratio only for the nearest by cosine
        return max(top, key=lambda ni: score(oi, ni))

    def best_old(ni: int) -> int:
        top = np.argsort(-cos[:, ni])[:20]
        return max(top, key=lambda oi: score(oi, ni))

    mapping = {}
    for oid in old_ids:
        if oid not in old:
            return f"{oid} is not in the questionnaire of the survey in use"
        oi = o_ids.index(oid)
        ni = best_new(oi)
        if best_old(ni) != oi:
            return f"{oid}'s best match {n_ids[ni]} is closer to {o_ids[best_old(ni)]}"
        mapping[oid] = n_ids[ni]
    if len(set(mapping.values())) != len(mapping):
        return f"not one-to-one: {mapping}"
    return mapping


def plausible(rows, items) -> str | None:
    """Why the mapped columns don't read as approval answers, or None."""
    counts = {item: [0, 0, 0, 0] for item, *_ in items}  # answered, coded, opinion, approve
    for r in rows:
        for item, c in counts.items():
            a = r.get(item, "")
            if a in ("", "__NA__"):
                continue
            c[0] += 1
            c[1] += a in APPROVE | DISAPPROVE | NOT_SURE
            c[2] += a in APPROVE | DISAPPROVE
            c[3] += a in APPROVE
    for item, (answered, coded, opinion, approve) in counts.items():
        if not answered:
            return f"{item} has no answers"
        if coded < CODED_SHARE * answered:
            return f"{item}: only {coded} of {answered} answers are codes 1-5"
        share = approve / max(opinion, 1)
        if not APPROVAL_BOUNDS[0] <= share <= APPROVAL_BOUNDS[1]:
            return f"{item}: {share:.0%} approve among opinions"
    return None


def fielded(rows) -> tuple[datetime | None, datetime | None]:
    """The first and last start time (ISO text, or Stata's milliseconds
    since 1960, as an ingested .tab carries them)."""
    first = last = None
    for r in rows:
        t = (r.get("starttime") or "").strip()
        try:
            when = (datetime(1960, 1, 1) + timedelta(milliseconds=float(t)) if re.fullmatch(r"[\d.eE+]+", t)
                    else datetime.fromisoformat(t[:19]))
        except (ValueError, OverflowError):
            continue
        first, last = min(first or when, when), max(last or when, when)
    return first, last


def district_column(header: list[str], start: datetime | None) -> str:
    """cdid<N> for the Congress in office when the survey was fielded (the
    newest the file carries at or before it)."""
    congress = (start.year - 1789) // 2 + 1 if start else None
    have = sorted(int(m[1]) for c in header if (m := re.fullmatch(r"cdid(\d+)", c)))
    usable = [n for n in have if congress is None or n <= congress]
    if not usable:
        raise SystemExit(f"no district column (have {have})")
    return f"cdid{usable[-1]}"


def tally(rows, items=ITEMS, district_col: str = "cdid118") -> dict:
    """{(state, chamber, district, name, member_party): {respondent_party:
    [sum w approve, sum w opinion, sum w^2 opinion, n opinion, n not sure]}}"""
    cells: dict = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0, 0]))
    for row in rows:
        state = STATE_OF_FIPS.get(int(row["inputstate"])) if row.get("inputstate", "").isdigit() else None
        try:
            w = float(row.get("commonweight") or 0)
        except ValueError:
            continue
        if not state or w <= 0:
            continue
        rparty = RESPONDENT_PARTY.get(row.get("pid3", ""), "I")
        for item, name_col, party_col, chamber in items:
            name = (row.get(name_col) or "").strip()
            answer = row.get(item, "")
            if not name or answer not in APPROVE | DISAPPROVE | NOT_SURE:
                continue
            district = row.get(district_col, "") if chamber == "house" else ""
            key = (state, chamber, district, name, MEMBER_PARTY.get(row.get(party_col, ""), "I"))
            acc = cells[key][rparty]
            if answer in NOT_SURE:
                acc[4] += 1
                continue
            acc[0] += w if answer in APPROVE else 0.0
            acc[1] += w
            acc[2] += w * w
            acc[3] += 1
    return cells


def _estimate(acc: list) -> dict | None:
    approve_w, opinion_w, sq_w, n, not_sure = acc
    if n == 0 or opinion_w <= 0:
        return None
    return {
        "rate": approve_w / opinion_w,
        "n": n,
        "n_eff": opinion_w * opinion_w / sq_w,
        "not_sure": not_sure,
    }


def priors(members: list[dict]) -> dict:
    """Beta-binomial method-of-moments prior per (chamber, member party,
    respondent party): mean mu and strength k in pseudo-respondents."""
    groups: dict = defaultdict(list)
    for m in members:
        for rparty, est in m["by_party"].items():
            groups[(m["chamber"], m["member_party"], rparty)].append(est)
            # Chamber-wide, whatever the member's party: the prior for a
            # member whose own party has too few members for one (the
            # Senate's independents).
            groups[(m["chamber"], "*", rparty)].append(est)
    out = {}
    for (chamber, mparty, rparty), ests in groups.items():
        if len(ests) < 10:
            continue
        total = sum(e["n_eff"] for e in ests)
        mu = sum(e["rate"] * e["n_eff"] for e in ests) / total
        # Between-member variance tau^2 by the method of moments for an
        # n_eff-weighted spread around an estimated mean (the weighted
        # one-way random-effects ANOVA): with S = sum n_i (r_i - mu)^2,
        # N = sum n_i and Q = sum n_i^2 / N,
        #   E[S] = tau^2 (N - Q) + (m - 1)(mu(1-mu) - tau^2),
        # since mu is estimated from the same m members and a member's own
        # binomial variance p_i(1-p_i) averages mu(1-mu) - tau^2. Simpler
        # noise terms (an unweighted mean of mu(1-mu)/n_i, or m mu(1-mu)/N)
        # understate tau^2 and so overstate k; checked by simulation from
        # Beta(mu, k) draws at each cell's real sample sizes.
        m = len(ests)
        S = sum(e["n_eff"] * (e["rate"] - mu) ** 2 for e in ests)
        Q = sum(e["n_eff"] ** 2 for e in ests) / total
        denom = total - Q - (m - 1)
        tau2 = (S - (m - 1) * mu * (1 - mu)) / denom if denom > 0 else 0.0
        # No spread between members beyond sampling noise: the survey can't
        # tell one member's approval in this cell from another's, so the
        # cell is not measurable per member (k None). No cell of the 2024
        # data is like that, but a thinner survey could be.
        k = max(mu * (1 - mu) / tau2 - 1, 0.0) if tau2 > 0 else None
        out[f"{chamber}/{mparty}/{rparty}"] = {
            "mu": round(mu, 4), "k": None if k is None else round(k, 1), "members": len(ests),
        }
    return out


def build(cells: dict) -> dict:
    members = []
    for (state, chamber, district, name, mparty), by_rparty in cells.items():
        by_party = {rp: e for rp, acc in by_rparty.items() if (e := _estimate(acc))}
        if by_party:
            members.append({
                "state": state, "chamber": chamber, "district": district or None,
                "name": name, "member_party": mparty, "by_party": by_party,
            })
    prior = priors(members)
    for m in members:
        for rparty, est in m["by_party"].items():
            p = prior.get(f"{m['chamber']}/{m['member_party']}/{rparty}") or prior.get(f"{m['chamber']}/*/{rparty}")
            if p is None or p["k"] is None:
                # Too few members in the cell to estimate a prior, or no
                # measurable difference between members: no per-member value.
                est["shrunk"] = None
            else:
                est["shrunk"] = round((est["rate"] * est["n_eff"] + p["mu"] * p["k"]) / (est["n_eff"] + p["k"]), 4)
                # How much of the figure is this member's own respondents
                # rather than the typical member's (the rest).
                est["own_weight"] = round(est["n_eff"] / (est["n_eff"] + p["k"]), 3)
            est["rate"] = round(est["rate"], 4)
            est["n_eff"] = round(est["n_eff"], 1)
    members.sort(key=lambda m: (m["chamber"], m["state"], m["district"] or "", m["name"]))
    return {"priors": prior, "members": members}


def write(out_path: pathlib.Path, path: pathlib.Path, items, district_col: str, year: int, doi: str,
          datafile: dict, wording: dict[str, str]) -> dict:
    first, last = fielded(_rows(path))
    result = build(tally(_rows(path), items, district_col))
    ids = "/".join(i[0] for i in items)
    months = f"{first:%Y-%m}/{last:%Y-%m}" if first else str(year)
    out = {
        "_source": (
            f"CES {year} Common Content, Harvard Dataverse {doi} (CC0), datafile {datafile['id']} "
            f"({datafile.get('filename')}). Approval of the respondent's own House member and senators ({ids}) "
            "by the respondent's party (pid3), weighted by commonweight, among respondents giving an opinion; "
            "shrunk per (chamber, member party, respondent party) by beta-binomial empirical Bayes (see the "
            "script). Generated by backend/scripts/fetch_ces_approval.py."
        ),
        "survey": f"CES {year} Common Content (fielded {months})",
        "fielded": months,
        "fielded_year": year,
        "doi": doi,
        "items": {slot: {"id": item[0], "wording": wording.get(item[0], "")} for slot, item in zip(SLOTS, items)},
        **result,
    }
    out_path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out_path}: {len(result['members'])} members; priors {result['priors']}")
    return out


def _rows(path: pathlib.Path):
    """The data file's rows, streamed (.tab or .csv, told by its first line)."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        first = f.readline()
        f.seek(0)
        yield from csv.DictReader(f, delimiter="\t" if "\t" in first else ",")


def _header(path: pathlib.Path) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline().rstrip("\r\n")
    return [c.strip('"') for c in first.split("\t" if "\t" in first else ",")]


def refresh(in_use: pathlib.Path, out: pathlib.Path, cache: pathlib.Path) -> int:
    """The newest survey, mapped to the one in use by wording (module docstring)."""
    current = json.loads(in_use.read_text())
    year, doi, name = newest_survey(_api("search", q='"Common Content"', type="dataset", per_page=100)["items"])
    if doi == current.get("doi", CES_DOI):
        current.pop("_refreshed", None)
        out.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
        print(f"{name} is the survey in use")
        return 0
    old_items = current.get("items") or {}
    old_ids = [(old_items.get(slot) or {}).get("id") or item[0] for slot, item in zip(SLOTS, ITEMS)]
    old_wording = questionnaire(survey_files(current.get("doi", CES_DOI))[1])
    datafile, docs = survey_files(doi)
    new_wording = questionnaire(docs)
    model = get_similarity_model()
    mapping = map_items(old_wording, new_wording, old_ids, lambda texts: model.encode(
        texts, normalize_embeddings=True, show_progress_bar=False))
    if isinstance(mapping, str):
        raise SystemExit(f"{name}: its approval items can't be mapped to the survey in use: {mapping}")
    items = tuple((mapping[oid], *item[1:]) for oid, item in zip(old_ids, ITEMS))
    path = cache / datafile["filename"]
    with urllib.request.urlopen(urllib.request.Request(f"{DATAVERSE}/api/access/datafile/{datafile['id']}",
                                                       headers={"User-Agent": USER_AGENT}), timeout=600) as r, \
            open(path, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)  # streamed: the 2024 file is 184 MB
    if why := plausible(_rows(path), items):
        raise SystemExit(f"{name}: the mapped items don't read as approval answers: {why}")
    first, _ = fielded(_rows(path))
    written = write(out, path, items, district_column(_header(path), first), year, doi, datafile, new_wording)
    path.unlink()
    before = len(current.get("members") or [])
    if before and len(written["members"]) < MEMBERS_KEPT * before:
        raise SystemExit(f"{name}: {len(written['members'])} members rated where the survey in use rates {before}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=pathlib.Path, help="a local copy of the 2024 Common Content CSV")
    parser.add_argument("--in-use", type=pathlib.Path, help="the survey file in use (the refresh's)")
    parser.add_argument("--output", type=pathlib.Path, default=OUT)
    parser.add_argument("--cache", type=pathlib.Path, default=pathlib.Path(tempfile.gettempdir()))
    args = parser.parse_args()
    if args.in_use:
        return refresh(args.in_use, args.output, args.cache)
    if not args.csv:
        parser.error("--csv (the 2024 file) or --in-use")
    write(args.output, args.csv, ITEMS, "cdid118", 2024, CES_DOI, {"id": CES_DATAFILE, "filename": args.csv.name}, {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
