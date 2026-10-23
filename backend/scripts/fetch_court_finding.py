"""The Court-level appointer finding (app/data/court_finding.json), recomputed
on the Supreme Court Database's newest release: a numpy port of section 8's
finding in scripts/research_justice_loyalty.py, for app/data_refresh.py.

The same panel and the same arithmetic, from what the pipeline already reads:

- through the 2014 term, Epstein & Posner's hand-coded votes as the pipeline
  bundles them (app/data/justice_president_votes_1937_2014.csv.gz, written
  by the research script's --write-bundle);
- from 2015, the Database's newest release (found by rule,
  justice_records.fetch_scdb: the newest release its archive lists), each
  vote's appointer and sitting president from the FJC's appointments and the
  presidents table (justice_pipeline._appointers, justice_loyalty.president_on),
  never a typed list, so a new justice or president needs no edit.

The finding is A's pooled appointer effect (vote for the government on the
appointer indicator, the government-petitioner control and a fixed effect
per justice) less the same fit on its first placebo window, with a 95%
bootstrap interval over justices (200 draws, numpy's default_rng(0), the
justices drawn in name order as the research script draws them). A fixed
effect per justice is fitted by demeaning within each justice (Frisch-Waugh-
Lovell: the same coefficients as statsmodels' dummies), so this needs no
pandas or statsmodels. tests/test_court_finding.py checks it against the
research script on the same panel.

    python scripts/fetch_court_finding.py OUT
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.http_client import make_async_client  # noqa: E402
from app.models import President  # noqa: E402
from app.pipeline.analyze.justice_loyalty import president_on  # noqa: E402
from app.pipeline.fetch.justice_records import fetch_fjc, fetch_scdb  # noqa: E402
from app.pipeline.justice_pipeline import _BUNDLE, _BUNDLE_LAST_TERM, _appointers  # noqa: E402

DRAWS = 200

# A panel row: (justice, term, vote for the government, appointer in office,
# government petitioner).
Row = tuple[str, int, int, int, int]


def bundled_rows(path: pathlib.Path = _BUNDLE) -> list[Row]:
    with gzip.open(path, "rt", newline="") as f:
        return [(r["justice"], int(r["term"]), int(r["for_government"]), int(r["appointer_in_office"]),
                 int(r["government_petitioner"])) for r in csv.DictReader(f)]


def recent_rows(votes: list[list], appointments: list[dict], terms: list[tuple]) -> list[Row]:
    """The Database's votes after the bundle's last term, each labelled by
    its appointer and the president on its date (votes outside any
    appointment or presidency left out, as the research script does)."""
    votes = [v for v in votes if v[4] > _BUNDLE_LAST_TERM]
    spans = _appointers({v[0] for v in votes}, appointments, terms)
    out = []
    for justice, date, gov_pet, for_gov, term in votes:
        appointed = next((p for p, a, b in spans.get(justice) or [] if a <= date and (b is None or date < b)), None)
        sitting = president_on(date, terms)
        if appointed is None or sitting is None:
            continue
        out.append((justice, int(term), int(for_gov), int(sitting == appointed), int(gov_pet)))
    return out


def _fe_coef(groups: list, y: np.ndarray, x: np.ndarray, pet: np.ndarray) -> float:
    """The coefficient on x in y ~ x + pet + a fixed effect per group."""
    _, idx = np.unique(np.asarray(groups, dtype=object).astype(str), return_inverse=True)
    counts = np.bincount(idx)

    def demean(v):
        return v - (np.bincount(idx, weights=v) / counts)[idx]

    design = np.column_stack([demean(x.astype(float)), demean(pet.astype(float))])
    coef, *_ = np.linalg.lstsq(design, demean(y.astype(float)), rcond=None)
    return float(coef[0])


def first_placebo(rows: list[Row]) -> list[tuple[str, int, int, int, int]] | None:
    """The research script's first placebo window (_placebo_windows, k=0):
    each justice's votes not under the appointer, `fake` marking the first
    terms of them, as many as the justice served under the appointer; None
    with fewer than three justices."""
    length: dict[str, set[int]] = {}
    for j, term, _, inside, _ in rows:
        if inside:
            length.setdefault(j, set()).add(term)
    rest: dict[str, list[Row]] = {}
    for r in rows:
        if not r[3]:
            rest.setdefault(r[0], []).append(r)
    out, parts = [], 0
    for j in sorted(rest):
        terms = sorted({r[1] for r in rest[j]})
        if j in length and len(length[j]) < len(terms):
            window = set(terms[:len(length[j])])
            out += [(j, r[1], r[2], int(r[1] in window), r[4]) for r in rest[j]]
            parts += 1
    return out if parts >= 3 else None


def _net(rows: list[tuple], d0: list[tuple]) -> float:
    def fit(data):
        g = [r[0] for r in data]
        a = np.array([r[2:] for r in data], dtype=float)
        return _fe_coef(g, a[:, 0], a[:, 1], a[:, 2])
    return fit(rows) - fit(d0)


def court_finding(rows: list[Row]) -> dict:
    d0 = first_placebo(rows)
    if d0 is None:
        raise SystemExit("fewer than three justices have a placebo window")
    by_p: dict[str, list[Row]] = {}
    by_d: dict[str, list[tuple]] = {}
    for r in rows:
        by_p.setdefault(r[0], []).append(r)
    for r in d0:
        by_d.setdefault(r[0], []).append(r)
    names = np.array(sorted(by_p))
    rng, draws = np.random.default_rng(0), []
    for _ in range(DRAWS):
        pick = rng.choice(names, len(names))
        p = [(f"{j}#{i}", *r[1:]) for i, j in enumerate(pick) for r in by_p[j]]
        d = [(f"{j}#{i}", *r[1:]) for i, j in enumerate(pick) if j in by_d for r in by_d[j]]
        draws.append(_net(p, d))
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"estimate": round(_net(rows, d0), 4), "low": round(float(lo), 4), "high": round(float(hi), 4),
            "draws": len(draws), "firstTerm": min(r[1] for r in rows), "lastTerm": max(r[1] for r in rows),
            "justices": len(by_p)}


async def _sources() -> tuple[dict, list[dict], list[tuple]]:
    db = SessionLocal()
    try:
        async with make_async_client() as client:
            scdb, fjc = await fetch_scdb(client, db), await fetch_fjc(client, db)
        terms = [(p.name, p.term_start, p.term_end) for p in db.query(President).order_by(President.term_start)]
    finally:
        db.close()
    missing = [name for name, gone in (("the Supreme Court Database", scdb is None), ("the FJC judges file", fjc is None),
                                       ("the presidents table", not terms)) if gone]
    if missing:
        raise SystemExit(" and ".join(missing) + " could not be read")
    return scdb, fjc, terms


def main() -> None:
    out = pathlib.Path(sys.argv[1])
    scdb, fjc, terms = asyncio.run(_sources())
    rows = bundled_rows() + recent_rows(scdb["votes"], fjc, terms)
    finding = court_finding(rows)
    year, release = scdb["release"].split(" Release ")
    out.write_text(json.dumps({
        "_source": (
            "backend/scripts/fetch_court_finding.py (the numpy port of research_justice_loyalty.py's section 8): "
            "the appointer effect pooled over every justice (Epstein & Posner's hand-coded votes through 2014, the "
            f"Supreme Court Database {year} Release {release} after), less the placebo window at the same point in "
            "a career, with a 95% bootstrap interval over justices (docs/research/justice-scores.md)."
        ),
        "scdbRelease": f"{year}_{release}",
        **finding,
        "excludesZero": finding["low"] > 0 or finding["high"] < 0,
    }, indent=1) + "\n")
    print(f"wrote {out}: {finding}")


if __name__ == "__main__":
    main()
