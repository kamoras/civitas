"""Calibrate commemorative-bill detection against Volden & Wiseman's coding.

Writes app/data/commemorative_calibration.json: the margin threshold
analyze/commemorative.py classifies with, the hash of the prototypes it was
fitted to, and the measurements below. Rerun when the prototypes change
(tests/test_commemorative.py fails until you do).

Ground truth. The Center for Effective Lawmaking publishes, per member and
congress, how many commemorative bills each member sponsored (V&W code this
from bill content). GovTrack publishes every bill's titles, sponsor and
status. Joined on the member (ICPSR id, else state/district/surname via the
congress-legislators crosswalk) for the 118th Congress:

  - members whose bill totals agree exactly between the two sources have
    the same bill set, so a member V&W credit with 0 commemorative bills
    labels every one of their bills non-commemorative (false-positive test);
  - for every such member, the classifier's commemorative count should equal
    V&W's (count error).

The threshold is fitted on the House and checked on the Senate, which it
never saw. Finally, the downstream question: rebuilding each member's
stage-normalized LES from GovTrack statuses, does weighting predicted
commemorative bills 1x instead of 5x bring the ranking closer to V&W's
published LES?

Research-only dependencies (requirements-research.txt):
    pip install -r requirements.txt -r requirements-research.txt

Usage (network required; on the Pi the model loads normally):
    python backend/scripts/calibrate_commemorative.py [--cache DIR] [--onnx DIR]

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
import statistics
import sys
import time
import unicodedata
import urllib.request

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.pipeline.analyze.commemorative import (  # noqa: E402
    commemorative_margins,
    prototype_hash,
)

CEL = {
    "house": "https://thelawmakers.org/wp-content/uploads/2025/06/CELHouse93to118-REVISED-06.26.2025.xlsx",
    "senate": "https://thelawmakers.org/wp-content/uploads/2025/03/CELSenate93to118.xls",
}
LEGISLATORS = "https://raw.githubusercontent.com/unitedstates/congress-legislators/d5af3d2d2490f8c6532c9490b1f47a76cacca699/{}"
GOVTRACK = ("https://www.govtrack.us/api/v2/bill?congress=118&bill_type={bt}&limit=500&order_by=number"
            "&number__gt={last}&fields=number,titles,sponsor__bioguideid,current_status")
BILL_TYPE = {"house": "house_bill", "senate": "senate_bill"}
OUT = ROOT / "app" / "data" / "commemorative_calibration.json"

# GovTrack's latest status -> Civitas's four LES stages (bill_stage.py).
_STATUS_STAGE = {"introduced": 1, "referred": 1, "reported": 2}


def _stage(status: str) -> int:
    if status in _STATUS_STAGE:
        return _STATUS_STAGE[status]
    if status.startswith("enacted"):
        return 4
    if status.startswith(("pass_", "passed_", "conference_", "vetoed", "override_")):
        return 3
    return 2  # failed on the floor, or killed after reaching it


def _get(url: str, path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        print(f"downloading {url}", file=sys.stderr)
        urllib.request.urlretrieve(url, path)
    return path


def _govtrack(chamber: str, cache: pathlib.Path) -> list[dict]:
    path = cache / f"govtrack_118_{chamber}.json"
    if path.exists():
        return json.loads(path.read_text())
    bills, last = [], 0
    while True:
        for attempt in range(6):
            try:
                with urllib.request.urlopen(GOVTRACK.format(bt=BILL_TYPE[chamber], last=last), timeout=120) as r:
                    page = json.load(r)["objects"]
                break
            except Exception as exc:  # noqa: BLE001 — retried, then raised below
                print(f"retry after {last}: {exc}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        else:
            raise SystemExit("GovTrack download failed")
        for o in page:
            titles = o.get("titles") or []
            bills.append({
                "sponsor": (o.get("sponsor") or {}).get("bioguideid"),
                "title": next((t[2] for t in titles if t[0] == "display"), ""),
                "stage": _stage(o.get("current_status") or "introduced"),
            })
        if len(page) < 500:
            break
        last = page[-1]["number"]
        time.sleep(1)
    path.write_text(json.dumps(bills))
    return bills


def _norm(x) -> str:
    return unicodedata.normalize("NFD", str(x)).encode("ascii", "ignore").decode().lower().strip()


def _members(chamber: str, cache: pathlib.Path) -> pd.DataFrame:
    """CEL's 118th-Congress rows with bioguide ids and commemorative counts."""
    import yaml

    leg = []
    for name in ("legislators-current.yaml", "legislators-historical.yaml"):
        leg += yaml.safe_load(_get(LEGISLATORS.format(name), cache / name).read_text())
    by_icpsr = {str(entry["id"]["icpsr"]): entry["id"]["bioguide"] for entry in leg if entry["id"].get("icpsr")}
    by_seat = collections.defaultdict(list)
    for entry in leg:
        for t in entry.get("terms", []):
            if t["start"] < "2025-01-03" and t["end"] > "2023-01-03":
                by_seat[(t["type"], t["state"], t.get("district"))].append(
                    (_norm(entry["name"]["last"]), entry["id"]["bioguide"]))

    url = CEL[chamber]
    df = pd.read_excel(_get(url, cache / url.rsplit("/", 1)[1]))
    cols = list(df.columns)
    start = next(i for i, c in enumerate(cols) if str(c).startswith("Number of commemorative bills sponsored"))
    congress = next(c for c in cols if str(c).lower().startswith("congress number"))
    icpsr = next(c for c in cols if "icpsr" in str(c).lower())
    majority = next(c for c in cols if "majority party member" in str(c).lower() or "in majority party" in str(c).lower())
    les = cols[start + 15]
    rows = []
    for _, r in df[df[congress] == 118].iterrows():
        bio = by_icpsr.get(str(int(r[icpsr]))) if pd.notna(r[icpsr]) else None
        if bio is None:
            if chamber == "house":
                d = r["Congressional district number"]
                key, last = ("rep", r["Two-letter state code"], int(d) if pd.notna(d) else None), str(r[cols[1]]).split(",")[0]
            else:
                key, last = ("sen", r["two letter state abbreviation"], None), r["last name"]
            hits = [b for ln, b in by_seat.get(key, []) if ln == _norm(last) or _norm(last).endswith(ln)]
            bio = hits[0] if len(hits) == 1 else None
        rows.append({
            "bio": bio, "majority": r[majority], "les": r[les],
            "C": int(r[cols[start]] or 0),
            "total": sum(int(r[cols[start + k]] or 0) for k in (0, 5, 10)),
        })
    return pd.DataFrame([row for row in rows if row["bio"]])


def _encoder(onnx_dir: str | None):
    if onnx_dir:
        from evaluate_embedding_models import OnnxEncoder
        return OnnxEncoder(onnx_dir, "mean")
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


def _count_error(members: pd.DataFrame, pred: dict[str, int]) -> tuple[float, float]:
    p = members.bio.map(lambda b: pred.get(b, 0))
    return float((p - members.C).abs().mean()), float((p == members.C).mean())


def _les(members: pd.DataFrame, bills_by: dict[str, list[dict]], commem: dict[int, bool] | None) -> pd.Series:
    """Stage-normalized LES over four stages from GovTrack statuses."""
    counts = []
    for bio in members.bio:
        c = [0.0] * 4
        for b in bills_by.get(bio, []):
            w = 1.0 if commem and commem.get(id(b)) else 5.0
            for k in range(b["stage"]):
                c[k] += w
        counts.append(c)
    totals = [sum(c[k] for c in counts) for k in range(4)]
    return pd.Series([sum(x / t for x, t in zip(c, totals) if t) for c in counts], index=members.index)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".research-cache/commemorative", type=pathlib.Path)
    ap.add_argument("--onnx", default=None, help="directory of an all-MiniLM-L6-v2 ONNX export")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    model = _encoder(args.onnx)

    data = {}
    for chamber in ("house", "senate"):
        members = _members(chamber, args.cache)
        bills = [b for b in _govtrack(chamber, args.cache) if b["sponsor"] and b["title"]]
        margins = commemorative_margins([b["title"] for b in bills], model)
        by = collections.defaultdict(list)
        for b, m in zip(bills, margins):
            b["margin"] = float(m)
            by[b["sponsor"]].append(b)
        members["gt_total"] = members.bio.map(lambda x: len(by.get(x, [])))
        data[chamber] = (members, by)

    def evaluate(chamber: str, threshold: float) -> dict:
        members, by = data[chamber]
        exact = members[members.total == members.gt_total]
        pred = {bio: sum(b["margin"] > threshold for b in by.get(bio, [])) for bio in exact.bio}
        neg = [b for bio in exact[exact.C == 0].bio for b in by.get(bio, [])]
        mae, share = _count_error(exact, pred)
        return {
            "members": int(len(exact)), "negative_bills": len(neg),
            "false_positive_rate": round(float(np.mean([b["margin"] > threshold for b in neg])), 4),
            "count_mae": round(mae, 3), "exact_count_share": round(share, 3),
            "all_zero_count_mae": round(float(exact.C.mean()), 3),
            "all_zero_exact_share": round(float((exact.C == 0).mean()), 3),
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
        flags = {id(b): b["margin"] > fit for bs in by.values() for b in bs}
        rho = {"all bills 5x": [], "predicted commemorative 1x": []}
        for _, g in members.groupby("majority"):
            if len(g) < 15:
                continue
            for label, flag in (("all bills 5x", None), ("predicted commemorative 1x", flags)):
                rho[label].append(float(_les(g, by, flag).rank().corr(g["les"].rank())))
        downstream[chamber] = {k: round(statistics.mean(v), 3) for k, v in rho.items()}

    print(json.dumps({"threshold": float(fit), "classification": result, "les_spearman": downstream}, indent=1))
    if args.no_write:
        return
    OUT.write_text(json.dumps({
        "_source": (
            "Generated by backend/scripts/calibrate_commemorative.py: threshold on the commemorative-minus-"
            "substantive prototype margin (all-MiniLM-L6-v2), fitted to minimise the error in each House member's "
            "commemorative-bill count against Volden & Wiseman's coding for the 118th Congress (Center for "
            "Effective Lawmaking), checked on the Senate. Bills from GovTrack; members joined via "
            "unitedstates/congress-legislators@d5af3d2."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "prototype_hash": prototype_hash(),
        "threshold": float(fit),
        "classification": result,
        "les_spearman_118th": downstream,
    }, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
