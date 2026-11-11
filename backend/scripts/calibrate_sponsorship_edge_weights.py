"""Fit the cosponsorship edge weights Legislative Leadership's PageRank uses.

Writes app/data/sponsorship_edge_weights.json, read by
sponsorship_analysis._cosponsorship_edge_weight: how much one cosponsorship
counts toward the sponsor's leadership, by how far the bill got (enacted;
advanced, its latest action passing a chamber, agreed to or reported; else
stalled). Enacted is the unit (1.0), since only the ratios and their size
against the matrix's identity diagonal matter.

What the weights should predict. Leadership is read as "a member whose bills
attract cosponsors from influential members", and the weights exist to tell
a productive collaboration from a message bill's cosponsor list. So a
member's leadership in Congress t, computed with the weights from t's
bills, is scored against what the member's own legislation does in t+1:
how many of the bills and joint resolutions they sponsor there advance or
become law (simple and concurrent resolutions are left out of the outcome:
most "agreed to" ones are commemorative). The measure is Spearman's rank
correlation over the members serving in the chamber in both Congresses,
within each party (of at least MIN_PARTY members) and averaged by the
party's size, one figure per chamber and transition. Within party, because
the majority moves legislation: across a change of majority (the House,
117th to 118th) a ranking over the whole chamber reads mostly which party
held it, and came out negative for every weight. Ranks only: the pipeline's
log rescale is monotone, so it can't change them.

Data: GovInfo's bulk Bill Status XML (www.govinfo.gov/bulkdata/BILLSTATUS),
every bill type of each Congress from FIRST_CONGRESS to the last one
complete by the clock, the same sponsor, cosponsor, law and latest-action
fields Congress.gov's API gives the pipeline. Cached in --cache.

Fit, per chamber (each chamber's leadership is its own network). A grid
over advanced and stalled (stalled never above advanced, advanced never
above enacted), the mean Spearman over the chamber's transitions. Each
chamber is tested forward in time against its weights in use (--in-use),
as the weights are always applied to a Congress the fit hasn't seen: each
transition from the chamber's third Congress on is predicted by the grid's
best on the earlier ones alone, and paired with the weights in use on the
same transition. A chamber takes the grid's best on all its transitions
only when that forward difference is more than one standard error above
zero; otherwise it keeps its weights. The adopted chambers' forward
differences are the file's _comparison, which
app/acceptance.held_out_beats_in_use decides the install on; a refit that
adopts nothing is reported unchanged.

A single set for both chambers was measured first (2026-11-10, Congresses
113-118): forward it gained 0.016 within party (SE 0.013), the Senate's
gain (0.042, SE 0.010) and the House's loss (0.010, SE 0.004) pulling
apart, which is what moved the fit to one per chamber.

    python scripts/calibrate_sponsorship_edge_weights.py --cache DIR [--in-use PATH] --out PATH
"""

import argparse
import io
import json
import pathlib
import sys
import time
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree

import numpy as np

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.pipeline.analyze.score_calculator import _ADVANCEMENT_ACTION_KEYWORDS  # noqa: E402
from app.pipeline.analyze.sponsorship_analysis import pagerank  # noqa: E402

# The first Congress GovInfo's Bill Status files cover in full.
FIRST_CONGRESS = 113
BULK = "https://www.govinfo.gov/bulkdata/BILLSTATUS/{c}/{t}/BILLSTATUS-{c}-{t}.zip"
CHAMBER_TYPES = {"house": ("hr", "hjres", "hres", "hconres"), "senate": ("s", "sjres", "sres", "sconres")}
# The outcome counts legislation only: bills and joint resolutions.
OUTCOME_TYPES = {"hr", "hjres", "s", "sjres"}
GRID = [round(0.05 * i, 2) for i in range(21)]
# The weights before the first fit (1.0, 0.6, 0.3: chosen by hand in
# 2026-07, their measurement since lost), the starting point a chamber
# keeps until a refit beats them; and flat weights, reported beside them.
STATED = (1.0, 0.6, 0.3)
FLAT = (1.0, 1.0, 1.0)
ENACTED, ADVANCED, STALLED = 0, 1, 2


def _download(cache: pathlib.Path, congress: int, bill_type: str) -> pathlib.Path:
    from app.http_client import source_request

    path = cache / f"BILLSTATUS-{congress}-{bill_type}.zip"
    if path.exists() and path.stat().st_size > 0:
        return path
    url = BULK.format(c=congress, t=bill_type)
    resp = source_request("GET", url, timeout=600)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    time.sleep(1)
    return path


def _text(el, *paths) -> str:
    for p in paths:
        found = el.find(p)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _tier(bill) -> int:
    laws = bill.find("laws")
    if laws is not None and len(laws):
        return ENACTED
    action = _text(bill, "latestAction/text").lower()
    return ADVANCED if any(k in action for k in _ADVANCEMENT_ACTION_KEYWORDS) else STALLED


def read_bills(cache: pathlib.Path, congress: int, chamber: str) -> list[dict]:
    """Every bill of the chamber's types in `congress`: its type, sponsor,
    cosponsors and tier."""
    bills = []
    for bill_type in CHAMBER_TYPES[chamber]:
        with zipfile.ZipFile(_download(cache, congress, bill_type)) as z:
            for name in z.namelist():
                if not name.endswith(".xml"):
                    continue
                bill = ElementTree.parse(io.BytesIO(z.read(name))).getroot().find("bill")
                if bill is None:
                    continue
                sponsor = _text(bill, "sponsors/item/bioguideId")
                if not sponsor:
                    continue
                cosponsors = {b.text.strip() for b in bill.findall("cosponsors/item/bioguideId") if b.text}
                parties = {_text(item, "bioguideId"): _text(item, "party")
                           for item in [*bill.findall("sponsors/item"), *bill.findall("cosponsors/item")]}
                bills.append({"type": bill_type, "sponsor": sponsor, "cosponsors": cosponsors - {sponsor},
                              "tier": _tier(bill), "parties": parties})
    return bills


def tier_counts(bills: list[dict]) -> tuple[dict[str, int], np.ndarray]:
    """Member index and a (3, n, n) array: cosponsorships of each sponsor
    (row) by each cosponsor (column), per tier."""
    index: dict[str, int] = {}
    for b in bills:
        for m in (b["sponsor"], *b["cosponsors"]):
            index.setdefault(m, len(index))
    counts = np.zeros((3, len(index), len(index)))
    for b in bills:
        i = index[b["sponsor"]]
        for c in b["cosponsors"]:
            counts[b["tier"], i, index[c]] += 1
    return index, counts


def leadership(counts: np.ndarray, weights: tuple[float, float, float]) -> np.ndarray:
    """The pipeline's matrix (sponsorship_analysis._build_cosponsorship_matrix:
    identity plus weighted cosponsorships, square-rooted) and PageRank."""
    n = counts.shape[1]
    P = np.identity(n) + sum(w * counts[t] for t, w in enumerate(weights))
    return pagerank(np.sqrt(P))


def outcomes(bills: list[dict]) -> dict[str, int]:
    """Each sponsor's bills and joint resolutions that advanced or became law."""
    got: dict[str, int] = {}
    for b in bills:
        if b["type"] in OUTCOME_TYPES:
            got[b["sponsor"]] = got.get(b["sponsor"], 0) + (b["tier"] != STALLED)
    return got


def _members(bills: list[dict]) -> set[str]:
    return {m for b in bills for m in (b["sponsor"], *b["cosponsors"])}


def _parties(bills: list[dict]) -> dict[str, str]:
    got: dict[str, str] = {}
    for b in bills:
        got.update({m: p for m, p in b["parties"].items() if m and p})
    return got


# A party's members are ranked against each other only when there are at
# least this many of them in the chamber (an independent or two can't be).
MIN_PARTY = 10


def within_party(scores: np.ndarray, outcome: np.ndarray, groups: list[np.ndarray]) -> float:
    """Spearman within each party, averaged by the party's size: who among
    a party's members gets legislation moving, so a change of majority
    (which moves every member of both parties at once) is not what the
    weights are scored on."""
    total = sum(len(g) for g in groups)
    return sum(len(g) * spearman(scores[g], outcome[g]) for g in groups) / total


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    def ranks(x):
        order = np.argsort(x, kind="mergesort")
        r = np.empty(len(x))
        r[order] = np.arange(len(x))
        # Ties share their mean rank.
        for v in np.unique(x):
            tied = x == v
            r[tied] = r[tied].mean()
        return r

    ra, rb = ranks(a), ranks(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def candidates() -> list[tuple[float, float, float]]:
    return [(1.0, a, s) for a in GRID for s in GRID if s <= a]


def score_grid(transitions: list[dict], weights_list) -> dict:
    """{weights: [Spearman per transition, in order]}."""
    out = {}
    for w in weights_list:
        out[w] = [within_party(leadership(t["counts"], w)[t["rows"]], t["outcome"], t["groups"])
                  for t in transitions]
    return out


def build_transitions(cache: pathlib.Path, first: int, last: int) -> list[dict]:
    bills = {(c, ch): read_bills(cache, c, ch) for c in range(first, last + 1) for ch in CHAMBER_TYPES}
    transitions = []
    for c in range(first, last):
        for ch in CHAMBER_TYPES:
            index, counts = tier_counts(bills[(c, ch)])
            later = bills[(c + 1, ch)]
            serving = _members(later)
            got = outcomes(later)
            party = _parties(later)
            both = sorted(m for m in index if m in serving)
            by_party: dict[str, list[int]] = {}
            for k, m in enumerate(both):
                by_party.setdefault(party.get(m, ""), []).append(k)
            transitions.append({
                "congress": c, "chamber": ch, "counts": counts,
                "rows": np.array([index[m] for m in both]),
                "outcome": np.array([got.get(m, 0) for m in both], dtype=float),
                "groups": [np.array(v) for p, v in sorted(by_party.items()) if p and len(v) >= MIN_PARTY],
                "members": len(both),
            })
    return transitions


def _summary(xs) -> dict:
    xs = np.array(xs, dtype=float)
    se = float(xs.std(ddof=1) / np.sqrt(len(xs))) if len(xs) > 1 else float("inf")
    return {"delta": round(float(xs.mean()), 5), "se": round(se, 5), "n": len(xs)}


def forward_test(transitions: list[dict], scores: dict, in_use: tuple, chamber: str) -> dict:
    """The chamber's transitions from its third Congress on, each predicted
    by the grid's best on the chamber's earlier Congresses alone, against
    the weights in use on the same transition."""
    idx = [i for i, t in enumerate(transitions) if t["chamber"] == chamber]
    congresses = sorted({transitions[i]["congress"] for i in idx})
    deltas, steps = [], []
    for c in congresses[2:]:
        earlier = [i for i in idx if transitions[i]["congress"] < c]
        best = max(scores, key=lambda w: np.mean([scores[w][i] for i in earlier]))
        for i in (i for i in idx if transitions[i]["congress"] == c):
            deltas.append(scores[best][i] - scores[in_use][i])
            steps.append({"congress": c, "weights": list(best), "spearman": round(scores[best][i], 4),
                          "inUse": round(scores[in_use][i], 4)})
    return {**_summary(deltas), "deltas": deltas, "steps": steps}


def _as_dict(w: tuple) -> dict:
    return {"enacted": w[0], "advanced": w[1], "stalled": w[2]}


def _as_tuple(w: dict) -> tuple:
    return float(w["enacted"]), float(w["advanced"]), float(w["stalled"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--in-use")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    cache = pathlib.Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    in_use_doc = json.loads(pathlib.Path(args.in_use).read_text()) if args.in_use else {}
    in_use = {ch: _as_tuple(w) for ch, w in (in_use_doc.get("chambers") or {}).items()}
    for ch in CHAMBER_TYPES:
        in_use.setdefault(ch, STATED)

    last = settings.CURRENT_CONGRESS - 1
    transitions = build_transitions(cache, FIRST_CONGRESS, last)
    grid = candidates()
    for w in [*in_use.values(), FLAT]:
        if w not in grid:
            grid.append(w)
    scores = score_grid(transitions, grid)

    chambers, fits, adopted_deltas, subgroups = {}, {}, [], {}
    for ch in sorted(CHAMBER_TYPES):
        idx = [i for i, t in enumerate(transitions) if t["chamber"] == ch]
        best = max(scores, key=lambda w: np.mean([scores[w][i] for i in idx]))
        forward = forward_test(transitions, scores, in_use[ch], ch)
        # A chamber's refit replaces its weights only when it beats them
        # forward in time by more than one standard error.
        adopt = best != in_use[ch] and forward["delta"] > forward["se"]
        chosen = best if adopt else in_use[ch]
        chambers[ch] = _as_dict(chosen)
        fits[ch] = {
            "best": _as_dict(best), "adopted": adopt,
            "meanSpearman": round(float(np.mean([scores[best][i] for i in idx])), 4),
            "inUseMeanSpearman": round(float(np.mean([scores[in_use[ch]][i] for i in idx])), 4),
            "flatMeanSpearman": round(float(np.mean([scores[FLAT][i] for i in idx])), 4),
            "forward": {k: v for k, v in forward.items() if k != "deltas"},
            "transitions": [{"congress": transitions[i]["congress"], "members": transitions[i]["members"],
                             "spearman": round(scores[best][i], 4), "inUse": round(scores[in_use[ch]][i], 4)}
                            for i in idx],
        }
        if adopt:
            adopted_deltas += forward["deltas"]
            subgroups[ch] = {k: forward[k] for k in ("delta", "se", "n")}

    metric = "forward within-party Spearman of leadership with next-Congress advanced legislation"
    if adopted_deltas:
        comparison = {"metric": metric, **_summary(adopted_deltas), "subgroups": subgroups}
    else:
        comparison = {"unchanged": True, "metric": metric}
    doc = {
        "_source": ("scripts/calibrate_sponsorship_edge_weights.py on GovInfo's Bill Status bulk data, "
                    f"Congresses {FIRST_CONGRESS}-{last}, generated "
                    f"{datetime.now(UTC).strftime('%Y-%m-%d')}"),
        "chambers": chambers,
        "calibrated_through": last,
        "fit": fits,
        "_comparison": comparison,
    }
    pathlib.Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps({"chambers": chambers, "comparison": comparison}, indent=2))
    for ch, f in fits.items():
        print(ch, json.dumps({k: v for k, v in f.items() if k != "transitions"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
