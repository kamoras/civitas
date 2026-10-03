"""Calibrate how much a congress-specific roll-call position can be trusted.

Regenerates app/data/position_confidence.json, read by score_calculator's
_position_half_weight_votes(chamber): each chamber's n0 in the reliability
weight n / (n + n0)
that Constituent Alignment's position-congruence component applies to a
member whose Nokken-Poole position rests on n roll calls (v6.27).

The weight is the slope of the score a member would get from a full
record on the score their n votes give: the factor that best predicts the
first from the second, so 50 + weight * (score - 50) is the best linear
estimate of where the member really sits. It is measured, not assumed:

1. Sampling error per vote count. For every member of Congresses
   CONGRESSES with at least RELIABLE_VOTES scalable votes, re-estimate
   their first-dimension position from random subsets of n of those votes
   (n in VOTE_COUNTS) and record the error against the estimate from all
   of them. Positions are maximum-likelihood under a logit per roll call,
   P(yea) = 1 / (1 + exp(-(a + b * x))), fitted on that Congress's
   Nokken-Poole positions, so the error is in Nokken-Poole units. The
   errors are heavy-tailed (a handful of party-line votes cannot place a
   member within their party at all), which is why no variance formula
   stands in for this step.
2. The population the score is applied to. Every member of the sitting
   Congress with at least RELIABLE_VOTES votes, read through the
   pipeline's own fit (fetch/voteview.build_chamber_ideal_points) and
   score (position_congruence_score at the chamber's saturation).
3. For each chamber and n, draw a member's true extremity from (2) and an
   error from (1), score both, and take the regression slope of the
   true score on the observed one. Each chamber's n0 is the least-squares
   fit of n / (n + n0) to its slopes; they differ (the House's heavier
   party-line voting says less about a member's place within their party),
   so the scorer reads each chamber's own.

Run from the repo (network required; Voteview's vote files are large, so
pass a cache directory to keep them):
    python3 backend/scripts/calibrate_position_confidence.py [--cache DIR] [--out FILE]
"""

import argparse
import asyncio
import csv
import datetime
import io
import json
import pathlib
import sys
import urllib.request

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import BOT_USER_AGENT  # noqa: E402

VOTEVIEW = "https://voteview.com/static/data/out/{kind}/{chamber}{congress}_{kind}.csv"
CONGRESSES = range(101, 119)  # completed Congresses: a sitting one's records are still growing
VOTE_COUNTS = (10, 20, 40, 80, 160)
DRAWS = 4  # subsamples per member per vote count
# A member with this many votes is located well enough to serve as the
# reference in (1) and as a true extremity in (2): the measured weight at
# 160 votes is already about 0.9, and it keeps rising.
RELIABLE_VOTES = 200
MINORITY = 0.025  # Voteview's scaling drops roll calls with a smaller minority
YEA, NAY = (1, 2, 3), (4, 5, 6)
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "position_confidence.json"


def read_csv(chamber: str, congress: int, kind: str, cache: pathlib.Path | None) -> list[dict]:
    name = f"{chamber}{congress}_{kind}.csv"
    if cache is not None and (cache / name).exists():
        text = (cache / name).read_text()
    else:
        url = VOTEVIEW.format(kind=kind, chamber=chamber, congress=congress)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})) as resp:
            text = resp.read().decode()
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / name).write_text(text)
    return list(csv.DictReader(io.StringIO(text)))


def is_placeholder(row: dict) -> bool:
    """Voteview writes 0, 0 for a member it could not scale (a handful of
    votes); that is no estimate, not a centrist."""
    return row.get("nokken_poole_dim1") in ("0", "0.0") and row.get("nokken_poole_dim2") in ("0", "0.0")


def fit_rollcalls(x: np.ndarray, y: np.ndarray, mask: np.ndarray, iters: int = 25, ridge: float = 1e-2):
    """Per roll call (column), a and b of a logit on member position x by
    Newton's method; the small ridge on b keeps a party-line vote finite."""
    a, b = np.zeros(y.shape[1]), np.zeros(y.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(a[None] + b[None] * x[:, None])))
        w = p * (1 - p) * mask
        r = (y - p) * mask
        g_a, g_b = r.sum(0), (r * x[:, None]).sum(0) - ridge * b
        h_aa = w.sum(0) + 1e-9
        h_ab = (w * x[:, None]).sum(0)
        h_bb = (w * x[:, None] ** 2).sum(0) + ridge
        det = h_aa * h_bb - h_ab ** 2
        a += (h_bb * g_a - h_ab * g_b) / det
        b += (h_aa * g_b - h_ab * g_a) / det
    return a, b


def locate(a: np.ndarray, b: np.ndarray, y: np.ndarray, x0: float, iters: int = 40) -> float:
    """A member's maximum-likelihood position given fixed roll-call logits,
    bounded to Nokken-Poole's range."""
    x = x0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(a + b * x)))
        x = float(np.clip(x + (b * (y - p)).sum() / ((b * b * p * (1 - p)).sum() + 1e-9), -1.5, 1.5))
    return x


def sampling_errors(chamber: str, congress: int, cache, rng) -> dict[int, list[float]]:
    """n -> signed errors of positions from n votes against the member's
    position from all their votes, over one Congress."""
    members = {
        r["icpsr"]: float(r["nokken_poole_dim1"]) for r in read_csv(chamber, congress, "members", cache)
        if r.get("chamber") != "President" and r.get("nokken_poole_dim1") and not is_placeholder(r)
    }
    cast = [(r["icpsr"], r["rollnumber"], int(float(r["cast_code"])))
            for r in read_csv(chamber, congress, "votes", cache) if r["icpsr"] in members]
    cast = [(i, rc, 1.0 if c in YEA else 0.0) for i, rc, c in cast if c in YEA + NAY]
    share: dict[str, list[float]] = {}
    for _, rc, y in cast:
        share.setdefault(rc, []).append(y)
    scalable = sorted(rc for rc, ys in share.items() if MINORITY <= sum(ys) / len(ys) <= 1 - MINORITY)
    ids = sorted({i for i, _, _ in cast})
    row, col = {i: k for k, i in enumerate(ids)}, {rc: k for k, rc in enumerate(scalable)}
    y, mask = np.zeros((len(ids), len(scalable))), np.zeros((len(ids), len(scalable)))
    for i, rc, v in cast:
        if rc in col:
            y[row[i], col[rc]], mask[row[i], col[rc]] = v, 1.0
    x = np.array([members[i] for i in ids])
    a, b = fit_rollcalls(x, y, mask)
    errors: dict[int, list[float]] = {n: [] for n in VOTE_COUNTS}
    for k in range(len(ids)):
        votes = np.flatnonzero(mask[k])
        if len(votes) < RELIABLE_VOTES:
            continue
        full = locate(a[votes], b[votes], y[k, votes], x[k])
        for n in VOTE_COUNTS:
            for _ in range(DRAWS):
                s = rng.choice(votes, n, replace=False)
                errors[n].append(locate(a[s], b[s], y[k, s], 0.0) - full)
    return errors


def sitting_extremities(chamber: str) -> tuple[np.ndarray, float]:
    """Extremities of the sitting Congress's reliable members, through the
    pipeline's own build, and the chamber's saturation."""
    from app.config import settings
    from app.pipeline.analyze.score_calculator import _district_pvi, _state_pvi
    from app.pipeline.fetch.voteview import PARTY_CODES, _seat_pvi_for, build_chamber_ideal_points, fetch_member_rows

    rows = asyncio.run(fetch_member_rows(chamber, settings.CURRENT_CONGRESS))
    data, failures = build_chamber_ideal_points(rows, chamber, _state_pvi(), _district_pvi(), half_weight_votes=0)
    if failures or not data["extremity_p90"]:
        sys.exit(f"{chamber}: sitting Congress did not build: {failures}")
    out = []
    for r in rows:
        bio = (r.get("bioguide_id") or "").strip()
        party = PARTY_CODES.get(int(float(r.get("party_code") or 0)))
        pvi = _seat_pvi_for(r, chamber, _state_pvi(), _district_pvi())
        if party not in data["fit"] or bio not in data["members"] or pvi is None:
            continue
        if data["votes"].get(bio, 0) < RELIABLE_VOTES:
            continue
        fit = data["fit"][party]
        residual = data["members"][bio] - (fit["a"] + fit["b"] * pvi)
        out.append(-residual if party == "D" else residual)
    return np.array(out), float(data["extremity_p90"])


def reliability(extremities: np.ndarray, saturation: float, errors: list[float], rng, draws: int = 200_000) -> float:
    """Slope of the true score on the observed score (both centered at 50)."""
    from app.pipeline.analyze.score_calculator import position_congruence_score

    def score(e):
        return np.array([position_congruence_score(v, saturation) for v in e]) - 50.0
    true = rng.choice(extremities, draws)
    observed = true + rng.choice(np.asarray(errors), draws)
    s_true, s_obs = score(true), score(observed)
    return float(np.cov(s_true, s_obs)[0, 1] / s_obs.var())


def fit_half_weight(points: list[tuple[int, float]]) -> float:
    """Least-squares n0 of weight = n / (n + n0) over (n, weight) points."""
    grid = np.arange(1.0, 200.0, 0.1)
    loss = [sum((w - n / (n + g)) ** 2 for n, w in points) for g in grid]
    return float(grid[int(np.argmin(loss))])


def calibrate(cache: pathlib.Path | None = None, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    chambers = {}
    for chamber, letter in (("senate", "S"), ("house", "H")):
        errors: dict[int, list[float]] = {n: [] for n in VOTE_COUNTS}
        for congress in CONGRESSES:
            for n, e in sampling_errors(letter, congress, cache, rng).items():
                errors[n] += e
            print(f"{letter}{congress} measured", file=sys.stderr, flush=True)
        extremities, saturation = sitting_extremities(chamber)
        weights = {n: reliability(extremities, saturation, errors[n], rng) for n in VOTE_COUNTS}
        chambers[chamber] = {
            "weights": {str(n): round(w, 3) for n, w in weights.items()},
            "median_abs_error": {str(n): round(float(np.median(np.abs(errors[n]))), 4) for n in VOTE_COUNTS},
            "errors_measured": {str(n): len(errors[n]) for n in VOTE_COUNTS},
            "members_in_population": int(len(extremities)),
            "half_weight_votes": round(fit_half_weight(list(weights.items())), 1),
        }
    pooled = [(n, w) for c in chambers.values() for n, w in ((int(k), v) for k, v in c["weights"].items())]
    return {"chambers": chambers, "half_weight_votes": round(fit_half_weight(pooled), 1)}


def main() -> None:
    from app.config import settings

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args()
    result = calibrate(args.cache)
    data = {
        "_source": (
            f"Voteview (voteview.com, Lewis et al.) member and vote exports, Senate and House, "
            f"Congresses {CONGRESSES.start}-{CONGRESSES.stop - 1} (sampling error) and "
            f"{settings.CURRENT_CONGRESS} (the scored population), retrieved "
            f"{datetime.date.today().isoformat()}; regenerate with "
            "backend/scripts/calibrate_position_confidence.py"
        ),
        "_method": (
            "Weight = slope of the full-record score on the score from n votes, with sampling "
            "errors measured by re-estimating members' positions from random subsets of their "
            "votes and true extremities from the sitting Congress; each chamber's half_weight_votes "
            "is the least-squares n0 of n / (n + n0) over its weights (the scorer reads these); "
            "the top-level value pools both chambers, for reference"
        ),
        **result,
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out}: half_weight_votes {data['half_weight_votes']} "
          f"(senate {result['chambers']['senate']['half_weight_votes']}, "
          f"house {result['chambers']['house']['half_weight_votes']})")


if __name__ == "__main__":
    main()
