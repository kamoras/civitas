"""Calibrate commemorative-bill detection against Volden & Wiseman's coding.

Writes app/data/commemorative_calibration.json: the margin threshold
analyze/commemorative.py classifies with, the hash of the prototypes it was
fitted to, and the measurements below. Rerun when the prototypes change
(tests/test_commemorative.py fails until you do).

Ground truth. The Center for Effective Lawmaking publishes, per member and
congress, how many commemorative bills each member sponsored (V&W code this
from bill content). GovInfo's BILLSTATUS bulk data gives every bill's
title, sponsor and actions, staged by the pipeline's own reading of them
(GovTrack's API, read until 2026-10, now answers automated requests with a
Cloudflare challenge, which is a wall, not a source). Joined on the member (ICPSR id, else state/district/surname via the
congress-legislators crosswalk) for the newest Congress CEL has coded in
both chambers (found by rule: the CEL<chamber>93to<N> spreadsheets its
data page links; 118 when this was first run):

  - members whose bill totals agree exactly between the two sources have
    the same bill set, so a member V&W credit with 0 commemorative bills
    labels every one of their bills non-commemorative (false-positive test);
  - for every such member, the classifier's commemorative count should equal
    V&W's (count error).

The threshold is fitted on the House and checked on the Senate, which it
never saw. Finally, the downstream question: rebuilding each member's
stage-normalized LES from those stages, does weighting predicted
commemorative bills 1x instead of 5x bring the ranking closer to V&W's
published LES?

With --in-use (app/data_refresh.py, monthly), a Congress already measured
on the prototypes in use writes the copy in use back unchanged. A refit is
measured against the copy in use on the Senate, which neither threshold was
fitted on: per majority group, the Spearman correlation of the rebuilt LES
with V&W's, refit minus in use, with a bootstrap standard error over
members, written as `_comparison` for the acceptance rule (app/acceptance.py).

Usage (network required; runs in the backend image: spreadsheets through
xlrd and the standard-library xlsx reader, no pandas):
    python backend/scripts/calibrate_commemorative.py [--cache DIR] [--onnx DIR] [--in-use FILE --output FILE]

--onnx runs all-MiniLM-L6-v2 from an ONNX export (mean pooling), for
environments that can't reach Hugging Face; the ONNX and PyTorch models give
the same embeddings.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import re
import shutil
import statistics
import sys
import unicodedata
import urllib.request
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import xlrd
import yaml
from scipy.stats import rankdata

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.contact import USER_AGENT  # noqa: E402
from app.pipeline.analyze.bill_stage import classify_bill_stage_from_actions  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import xlsx_sheet_rows  # noqa: E402
from app.pipeline.analyze.commemorative import (  # noqa: E402
    commemorative_margins,
    prototype_hash,
)
from app.pipeline.vector_store import get_similarity_model  # noqa: E402


def cel_sources(page: str) -> tuple[int, dict[str, str]]:
    """(the newest Congress CEL has coded in both chambers, {chamber: its
    spreadsheet}) from the data page's links (the "Reduced" Stata files
    left out)."""
    newest: dict[str, tuple[int, str]] = {}
    for url, chamber, upto in CEL_FILE.findall(page):
        chamber = chamber.lower()
        if int(upto) > newest.get(chamber, (0, ""))[0]:
            newest[chamber] = (int(upto), url)
    if set(newest) != {"house", "senate"}:
        raise SystemExit(f"{CEL_PAGE} links no CEL spreadsheet for {sorted({'house', 'senate'} - set(newest))}")
    congress = min(n for n, _ in newest.values())
    if any(n != congress for n, _ in newest.values()):
        raise SystemExit(f"CEL's chambers end at different Congresses: {newest}")
    return congress, {c: u for c, (_, u) in newest.items()}

CEL_PAGE = "https://thelawmakers.org/data-download"
CEL_FILE = re.compile(r'href="([^"]*/CEL(House|Senate)93to(\d+)(?![^"]*Reduced)[^"/]*\.xlsx?)"', re.I)
LEGISLATORS = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/{}"
BILLSTATUS = "https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{kind}/BILLSTATUS-{congress}-{kind}.zip"
BILL_KIND = {"house": "hr", "senate": "s"}
# Bootstrap draws for the comparison's standard error.
DRAWS = 200
OUT = ROOT / "app" / "data" / "commemorative_calibration.json"

# The pipeline's bill stages (bill_stage.BillStage) -> the four stages this
# calibration counts in: introduced (or only referred, or with committee
# action short of a report), reported or on the floor, passed a chamber
# (whatever happened after, short of law), and enacted.
STAGE_OF = {
    "INTRODUCED": 1, "REFERRED": 1, "IN_COMMITTEE": 1, "REPORTED": 2, "ON_FLOOR": 2,
    "PASSED_CHAMBER": 3, "IN_OTHER_CHAMBER": 3, "PASSED_BOTH": 3, "TO_PRESIDENT": 3, "VETOED": 3, "ENACTED": 4,
}


def _get(url: str, path: pathlib.Path) -> pathlib.Path:
    """`url` downloaded to `path` (streamed), once."""
    if not path.exists():
        print(f"downloading {url}", file=sys.stderr)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=600) as resp, open(path, "wb") as out:
            shutil.copyfileobj(resp, out, 1 << 20)
    return path


def _bills(chamber: str, cache: pathlib.Path, congress: int) -> list[dict]:
    """Every bill of the chamber's own kind (H.R., S.) in the Congress, from
    GovInfo's BILLSTATUS bulk data: its sponsor, display title and stage
    (classify_bill_stage_from_actions, the pipeline's own reading of the
    actions)."""
    kind = BILL_KIND[chamber]
    path = _get(BILLSTATUS.format(congress=congress, kind=kind), cache / f"BILLSTATUS-{congress}-{kind}.zip")
    out = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.endswith(".xml"):
                continue
            bill = ET.fromstring(zf.read(name)).find("bill")
            if bill is None:
                continue
            actions = [{"actionCode": a.findtext("actionCode"), "type": a.findtext("type"), "text": a.findtext("text")}
                       for a in bill.findall("actions/item")]
            stage = classify_bill_stage_from_actions(actions, is_law=bill.find("laws/item") is not None,
                                                     bill_type=kind)
            out.append({"sponsor": bill.findtext("sponsors/item/bioguideId"), "title": bill.findtext("title") or "",
                        "stage": STAGE_OF[getattr(stage, "value", stage)]})
    return out


def _norm(x) -> str:
    return unicodedata.normalize("NFD", str(x)).encode("ascii", "ignore").decode().lower().strip()


def _sheet(path: pathlib.Path) -> list[list]:
    """A CEL spreadsheet's first sheet as rows (.xls through xlrd, .xlsx
    through the standard-library reader), header first."""
    if path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(file_contents=path.read_bytes())
        sheet = book.sheet_by_index(0)
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    return xlsx_sheet_rows(path.read_bytes()) or []


def _num(value) -> float | None:
    """A cell as a number, or None for an empty or non-numeric one."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _members(chamber: str, cache: pathlib.Path, congress: int, url: str) -> list[dict]:
    """CEL's rows for `congress` with bioguide ids and commemorative counts."""
    first = 1789 + 2 * (congress - 1)
    start, end = f"{first}-01-03", f"{first + 2}-01-03"
    leg = []
    for name in ("legislators-current.yaml", "legislators-historical.yaml"):
        leg += yaml.safe_load(_get(LEGISLATORS.format(name), cache / name).read_text())
    by_icpsr = {str(entry["id"]["icpsr"]): entry["id"]["bioguide"] for entry in leg if entry["id"].get("icpsr")}
    by_seat = collections.defaultdict(list)
    for entry in leg:
        for t in entry.get("terms", []):
            if t["start"] < end and t["end"] > start:
                by_seat[(t["type"], t["state"], t.get("district"))].append(
                    (_norm(entry["name"]["last"]), entry["id"]["bioguide"]))

    rows = _sheet(_get(url, cache / url.rsplit("/", 1)[1]))
    cols = [str(c) for c in rows[0]]
    at = {c: i for i, c in reversed(list(enumerate(cols)))}  # a name's first column
    first_count = next(i for i, c in enumerate(cols) if c.startswith("Number of commemorative bills sponsored"))
    column = next(i for i, c in enumerate(cols) if c.lower().startswith("congress number"))
    icpsr = next(i for i, c in enumerate(cols) if "icpsr" in c.lower())
    majority = next(i for i, c in enumerate(cols)
                    if "majority party member" in c.lower() or "in majority party" in c.lower())
    les = first_count + 15
    out = []
    for raw in rows[1:]:
        r = list(raw) + [""] * (len(cols) - len(raw))
        if _num(r[column]) != congress:
            continue
        number = _num(r[icpsr])
        bio = by_icpsr.get(str(int(number))) if number is not None else None
        if bio is None:
            if chamber == "house":
                d = _num(r[at["Congressional district number"]])
                key = ("rep", r[at["Two-letter state code"]], int(d) if d is not None else None)
                last = str(r[1]).split(",")[0]
            else:
                key, last = ("sen", r[at["two letter state abbreviation"]], None), r[at["last name"]]
            hits = [b for ln, b in by_seat.get(key, []) if ln == _norm(last) or _norm(last).endswith(ln)]
            bio = hits[0] if len(hits) == 1 else None
        if bio:
            out.append({
                "bio": bio, "majority": _num(r[majority]), "les": _num(r[les]),
                "C": int(_num(r[first_count]) or 0),
                "total": sum(int(_num(r[first_count + k]) or 0) for k in (0, 5, 10)),
            })
    return out


def _encoder(onnx_dir: str | None):
    if onnx_dir:
        from evaluate_embedding_models import OnnxEncoder  # noqa: PLC0415  (research-only: onnxruntime)
        return OnnxEncoder(onnx_dir, "mean")
    return get_similarity_model()  # all-MiniLM-L6-v2, as the pipeline loads it


def _count_error(members: list[dict], pred: dict[str, int]) -> tuple[float, float]:
    p = np.array([pred.get(m["bio"], 0) for m in members])
    c = np.array([m["C"] for m in members])
    return float(np.abs(p - c).mean()), float((p == c).mean())


def _les(members: list[dict], bills_by: dict[str, list[dict]], commem: dict[int, bool] | None) -> list[float]:
    """Stage-normalized LES over four stages."""
    counts = []
    for m in members:
        c = [0.0] * 4
        for b in bills_by.get(m["bio"], []):
            w = 1.0 if commem and commem.get(id(b)) else 5.0
            for k in range(b["stage"]):
                c[k] += w
        counts.append(c)
    totals = [sum(c[k] for c in counts) for k in range(4)]
    return [sum(x / t for x, t in zip(c, totals) if t) for c in counts]


def _groups(members: list[dict]) -> list[list[dict]]:
    """Members by majority-party status, in key order (as pandas' groupby)."""
    keys = sorted({m["majority"] for m in members if m["majority"] is not None})
    return [[m for m in members if m["majority"] == k] for k in keys]


def _ranks(values) -> np.ndarray:
    """Average ranks of the values present, NaN where one is missing (as
    pandas' Series.rank)."""
    v = np.array([np.nan if x is None else x for x in values], dtype=float)
    out = np.full(len(v), np.nan)
    out[~np.isnan(v)] = rankdata(v[~np.isnan(v)])
    return out


def _rank_corr(members: list[dict], les: list[float]) -> float:
    """Spearman correlation of the rebuilt LES with V&W's published LES: each
    ranked over its own values, then correlated over the members both have
    (as the research script's pandas `.rank().corr()`)."""
    a, b = _ranks(les), _ranks([m["les"] for m in members])
    both = ~(np.isnan(a) | np.isnan(b))
    return float(np.corrcoef(a[both], b[both])[0, 1])


def _spearman_by_group(members: list[dict], by: dict, threshold: float) -> dict:
    """{majority group: Spearman of the rebuilt LES with V&W's}, predicted
    commemorative bills weighted 1x at `threshold`; groups under 15 members
    left out."""
    flags = {id(b): b["margin"] > threshold for bs in by.values() for b in bs}
    return {str(g[0]["majority"]): _rank_corr(g, _les(g, by, flags)) for g in _groups(members) if len(g) >= 15}


def held_out_comparison(members: list[dict], by: dict, refit: float, in_use: float) -> dict:
    """The refit's threshold against the one in use on held-out members: the
    mean over majority groups of the Spearman difference, and each group's,
    with bootstrap standard errors over members (DRAWS resamples, paired)."""
    def diffs(m: list[dict]) -> dict:
        new, old = _spearman_by_group(m, by, refit), _spearman_by_group(m, by, in_use)
        return {g: new[g] - old[g] for g in new if g in old}

    point = diffs(members)
    rng, draws = np.random.default_rng(0), []
    for _ in range(DRAWS):
        draws.append(diffs([members[i] for i in rng.integers(0, len(members), len(members))]))
    groups = sorted(point)

    def se(values):
        return round(float(np.std(values, ddof=1)), 6) if len(values) > 1 else 0.0

    return {"metric": "held-out Senate LES Spearman with V&W's, refit minus in use",
            "delta": round(float(np.mean([point[g] for g in groups])), 6) if groups else 0.0,
            "se": se([np.mean([d[g] for g in groups if g in d]) for d in draws if any(g in d for g in groups)]),
            "subgroups": {f"majority {g}": {"delta": round(point[g], 6), "se": se([d[g] for d in draws if g in d])}
                          for g in groups}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".research-cache/commemorative", type=pathlib.Path)
    ap.add_argument("--onnx", default=None, help="directory of an all-MiniLM-L6-v2 ONNX export")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--in-use", type=pathlib.Path, help="the calibration in use, to measure the refit against")
    ap.add_argument("--output", type=pathlib.Path, default=OUT)
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(CEL_PAGE, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        congress, cel = cel_sources(resp.read().decode("utf-8", "replace"))
    current = json.loads(args.in_use.read_text()) if args.in_use else {}
    if current and current.get("prototype_hash") == prototype_hash() and f"les_spearman_{congress}th" in current:
        current.pop("_refreshed", None)
        args.output.write_text(json.dumps(current, indent=1) + "\n")
        print(f"the {congress}th Congress is the one in use")
        return
    model = _encoder(args.onnx)

    data = {}
    for chamber in ("house", "senate"):
        members = _members(chamber, args.cache, congress, cel[chamber])
        bills = [b for b in _bills(chamber, args.cache, congress) if b["sponsor"] and b["title"]]
        margins = commemorative_margins([b["title"] for b in bills], model)
        by = collections.defaultdict(list)
        for b, m in zip(bills, margins):
            b["margin"] = float(m)
            by[b["sponsor"]].append(b)
        for m in members:
            m["gt_total"] = len(by.get(m["bio"], []))
        data[chamber] = (members, by)

    def evaluate(chamber: str, threshold: float) -> dict:
        members, by = data[chamber]
        exact = [m for m in members if m["total"] == m["gt_total"]]
        pred = {m["bio"]: sum(b["margin"] > threshold for b in by.get(m["bio"], [])) for m in exact}
        neg = [b for m in exact if m["C"] == 0 for b in by.get(m["bio"], [])]
        mae, share = _count_error(exact, pred)
        counts = np.array([m["C"] for m in exact])
        return {
            "members": len(exact), "negative_bills": len(neg),
            "false_positive_rate": round(float(np.mean([b["margin"] > threshold for b in neg])), 4),
            "count_mae": round(mae, 3), "exact_count_share": round(share, 3),
            "all_zero_count_mae": round(float(counts.mean()), 3),
            "all_zero_exact_share": round(float((counts == 0).mean()), 3),
        }

    # Fit on the House (the smallest count error; ties to the stricter
    # threshold), check on the Senate.
    grid = np.round(np.arange(0.0, 0.60, 0.005), 3)
    fit = min(grid, key=lambda t: (evaluate("house", t)["count_mae"], -t))
    result = {"house (fitted)": evaluate("house", fit), "senate (held out)": evaluate("senate", fit)}

    # Downstream: does 1x commemorative weighting move LES toward V&W's?
    downstream = {}
    for chamber in ("house", "senate"):
        members, by = data[chamber]
        rho = {"all bills 5x": [], "predicted commemorative 1x": []}
        flags = {id(b): b["margin"] > fit for bs in by.values() for b in bs}
        for g in _groups(members):
            if len(g) < 15:
                continue
            for label, flag in (("all bills 5x", None), ("predicted commemorative 1x", flags)):
                rho[label].append(_rank_corr(g, _les(g, by, flag)))
        downstream[chamber] = {k: round(statistics.mean(v), 3) for k, v in rho.items()}

    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_commemorative.py: threshold on the commemorative-minus-"
            "substantive prototype margin (all-MiniLM-L6-v2), fitted to minimise the error in each House member's "
            f"commemorative-bill count against Volden & Wiseman's coding for the {congress}th Congress (Center for "
            f"Effective Lawmaking, {cel['house'].rsplit('/', 1)[1]} and {cel['senate'].rsplit('/', 1)[1]}), checked "
            "on the Senate. Bills from GovInfo's BILLSTATUS bulk data; members joined via unitedstates/congress-legislators."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "prototype_hash": prototype_hash(),
        "threshold": float(fit),
        "classification": result,
        f"les_spearman_{congress}th": downstream,
    }
    if current.get("threshold") is not None and float(current["threshold"]) == float(fit):
        # The same threshold on the newer Congress: new measurements, no refit.
        out["_comparison"] = {"unchanged": True}
    elif current.get("threshold") is not None:
        members, by = data["senate"]
        out["_comparison"] = held_out_comparison(members, by, float(fit), float(current["threshold"]))
        out["_change"] = f"threshold {current['threshold']} -> {float(fit)} ({congress}th Congress)"
    print(json.dumps({k: v for k, v in out.items() if k != "_source"}, indent=1))
    if args.no_write:
        return
    args.output.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
