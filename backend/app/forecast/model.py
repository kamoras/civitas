"""The arithmetic behind the poll-informed vote-margin ranges.

One module for both the fit (scripts/fit_forecast_model.py, which runs
it over official historical results and 538's final-weeks polls) and
the live build (app.forecast.build, which runs it over this year's
inputs with the fitted parameters), so the two cannot drift apart.

Every margin is the Republican-minus-Democratic share of the two-party
vote, in points: +6 is "R+6", -6 is "D+6". A lean is Cook's measure of
the seat (its two-party Republican share minus the nation's, in points,
positive = R), computed by Civitas from official returns.

THE METHOD, in the order it runs:

1. Structural prior (the "fundamentals"). An ordinary least-squares line
   through every contested race of the training cycles:
       margin = b0 + b1 * lean + b2 * incumbent + b3 * national
   incumbent is +1 when the Republican nominee is the incumbent, -1 when
   the Democrat is, 0 for an open seat (the FEC's own incumbent code);
   national is the generic-ballot poll average, the same average as step
   2 over national House polls, which moves every seat together. Its
   residual standard deviation is the prior's spread.
   (Abramowitz 2008, "Forecasting the 2008 Presidential Election with the
   Time-for-Change Model"; Bafumi, Erikson & Wlezien 2010, "Forecasting
   House Seats from Generic Congressional Polls".)

2. Poll average. Each pollster's most recent poll in the window, weighted
   by recency (a half-life) and the square root of its sample size, after
   subtracting the pollster's measured house effect — its average
   difference from the other pollsters in the same races, shrunk toward
   zero — or, for a pollster with no history, the measured lean of polls
   sponsored by a party, for one its source marks as partisan.
   (Silver 2014, "How FiveThirtyEight Calculates Pollster Ratings";
   Jackman 2005, "Pooling the Polls Over an Election Campaign".)
   Its error spread is a + b / (effective number of polls), fitted to how
   far historical averages landed from the result.

3. Combination by inverse variance: the prior and the poll average are
   two noisy readings of the same margin, each weighted by one over its
   variance. A seat with no poll keeps its prior.

4. Calibration. One scale factor on the combined spread, the maximum-
   likelihood fit of the training results' distances from the combined
   mean, so an 80% range holds the result about 80% of the time; then
   checked on cycles the fit never saw (leave one cycle out), for the
   ranges' coverage and for the chances they imply. The map shows ranges
   (an interval of margins), never a chance; the chances exist only to
   measure calibration.

Every fitted number is written by the fit script to
app/data/forecast_model.json; nothing here is a hand-typed estimate. The
constants below are the method's design choices (grids searched, the
window looked back over), each one stated.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

# How far back a poll may be and still count, in days before the as-of
# date. 538's final-weeks file reaches 61 days out at most, so the fit
# cannot speak to older polls.
WINDOW_DAYS = 45
# Sample sizes enter as sqrt(n / N_REF), with n clipped to [N_MIN, N_CAP]
# so one enormous online panel cannot drown out every other poll, and a
# poll that states no sample counts as a typical one (N_REF).
N_REF = 600
N_MIN = 100
N_CAP = 3000
# House effects shrink toward zero as if each pollster had this many
# extra polls exactly matching the others (a simple, stated prior; with
# fewer polls than this a pollster keeps less than half its raw effect).
HOUSE_SHRINK = 10
# Grids the fit searches (by maximum likelihood on the training cycles).
HALF_LIFE_GRID = (4.0, 7.0, 14.0, 28.0)
FLOOR_GRID = tuple(round(0.5 + 0.25 * i, 2) for i in range(47))  # 0.5 .. 12.0 points
SPREAD_GRID = tuple(round(0.5 * i, 1) for i in range(41))  # 0 .. 20 points
# The as-of horizons the method is fitted at, in days before election
# day: a build uses the largest one that is not later than its own.
HORIZONS = (0, 7, 14)
# The interval the map shows, and the wider one the Difference view
# uses for its double mark: the central 80% and 95% of the calibrated
# normal spread.
Z80 = 1.2815515655446004
Z95 = 1.959963984540054
# Calibration-table bins over the Democratic candidate's implied chance.
# 0.35-0.65 is the band the page calls near even.
CALIBRATION_EDGES = (0.0, 0.05, 0.15, 0.35, 0.65, 0.85, 0.95, 1.0)


@dataclass(frozen=True)
class Poll:
    pollster: str
    days_out: int  # days before election day the fieldwork ended
    n: int | None
    margin: float  # R - D, points
    partisan: str = ""  # "R" / "D" when the source marks the poll as partisan

    @classmethod
    def from_row(cls, row: list) -> "Poll":
        return cls(row[0], int(row[1]), row[2], float(row[3]), row[4] or "")

    def as_row(self) -> list:
        return [self.pollster, self.days_out, self.n, self.margin, self.partisan]


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def pollster_key(name: str) -> str:
    """A pollster's name reduced to lowercase letters and digits, so the
    same firm matches across 538's file and Wikipedia's tables."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_pollster(name: str, known: dict) -> str | None:
    """The key in `known` for this pollster: exact after pollster_key,
    else the closest by Ratcliff-Obershelp similarity at 0.9 or above."""
    key = pollster_key(name)
    if key in known:
        return key
    best = difflib.get_close_matches(key, list(known), n=1, cutoff=0.9)
    return best[0] if best else None


# ---------------------------------------------------------------------------
# House effects
# ---------------------------------------------------------------------------


def house_effects(races: list[list[Poll]]) -> dict:
    """{"pollsters": {key: effect}, "partisan": lean of sponsored polls}.

    A pollster's effect is the mean of (its poll - the mean of the other
    pollsters' polls in the same race), shrunk by HOUSE_SHRINK. The
    partisan figure is the same difference for polls a source marks as
    partisan, signed toward the sponsor (positive = they lean the
    sponsor's way), used only for a pollster with no history."""
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    partisan: list[float] = []
    for polls in races:
        by = defaultdict(list)
        for p in polls:
            by[pollster_key(p.pollster)].append(p)
        if len(by) < 2:
            continue
        means = {k: float(np.mean([p.margin for p in v])) for k, v in by.items()}
        total = sum(means.values())
        for k, v in by.items():
            others = (total - means[k]) / (len(means) - 1)
            for p in v:
                d = p.margin - others
                sums[k] += d
                counts[k] += 1
                if p.partisan in ("R", "D"):
                    partisan.append(d if p.partisan == "R" else -d)
    effects = {k: round(sums[k] / (counts[k] + HOUSE_SHRINK), 3) for k in sorted(sums)}
    return {"pollsters": effects, "partisan": round(float(np.mean(partisan)), 3) if partisan else 0.0}


def adjusted_margin(p: Poll, effects: dict) -> float:
    key = match_pollster(p.pollster, effects["pollsters"])
    if key is not None:
        return p.margin - effects["pollsters"][key]
    if p.partisan == "R":
        return p.margin - effects["partisan"]
    if p.partisan == "D":
        return p.margin + effects["partisan"]
    return p.margin


# ---------------------------------------------------------------------------
# Poll average
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PollAverage:
    margin: float
    k_eff: float
    used: tuple[Poll, ...]


def eligible(polls: list[Poll], horizon: int) -> list[Poll]:
    """Each pollster's most recent poll fielded by the as-of date (days_out
    at or beyond `horizon`) and within WINDOW_DAYS of it. A pollster with
    two questions on its latest date (likely and registered voters, say)
    keeps both, averaged by the weights."""
    latest: dict[str, int] = {}
    for p in polls:
        if horizon <= p.days_out <= horizon + WINDOW_DAYS:
            k = pollster_key(p.pollster)
            latest[k] = min(latest.get(k, p.days_out), p.days_out)
    return [p for p in polls if horizon <= p.days_out <= horizon + WINDOW_DAYS
            and latest.get(pollster_key(p.pollster)) == p.days_out]


def poll_weight(p: Poll, horizon: int, half_life: float) -> float:
    n = min(max(p.n or N_REF, N_MIN), N_CAP)
    return 0.5 ** ((p.days_out - horizon) / half_life) * math.sqrt(n / N_REF)


def poll_average(polls: list[Poll], horizon: int, half_life: float, effects: dict) -> PollAverage | None:
    used = eligible(polls, horizon)
    if not used:
        return None
    # A poll printed as several questions (likely and registered voters,
    # with and without leaners) counts once: its questions share its weight.
    per = defaultdict(int)
    for p in used:
        per[pollster_key(p.pollster)] += 1
    w = np.array([poll_weight(p, horizon, half_life) / per[pollster_key(p.pollster)] for p in used])
    m = np.array([adjusted_margin(p, effects) for p in used])
    return PollAverage(float(w @ m / w.sum()), float(w.sum() ** 2 / (w ** 2).sum()), tuple(used))


def poll_sd(k_eff: float, floor: float, spread: float) -> float:
    return math.sqrt(floor ** 2 + spread ** 2 / k_eff)


# ---------------------------------------------------------------------------
# Fitting (training rows: dicts with margin, lean, inc, polls, year)
# ---------------------------------------------------------------------------


def national(generic: dict, year: int, horizon: int, half_life: float, effects: dict) -> float | None:
    avg = poll_average(generic.get(str(year), []), horizon, half_life, effects)
    return None if avg is None else avg.margin


def fit_fundamentals(x: np.ndarray, y: np.ndarray) -> tuple[list[float], float]:
    """OLS coefficients [b0, lean, incumbent, national] and the residual
    standard deviation (degrees of freedom n - 4)."""
    design = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    return [float(b) for b in beta], float(math.sqrt(resid @ resid / (len(y) - design.shape[1])))


def fit_poll_error(errors: np.ndarray, k_eff: np.ndarray) -> tuple[float, float, float]:
    """(floor, spread, log-likelihood): the a, b in sd = sqrt(a^2 + b^2/k)
    that make the historical average-minus-result errors most likely."""
    a = np.array(FLOOR_GRID)[:, None, None]
    b = np.array(SPREAD_GRID)[None, :, None]
    var = a ** 2 + b ** 2 / k_eff[None, None, :]
    ll = (-0.5 * (np.log(2 * np.pi * var) + errors[None, None, :] ** 2 / var)).sum(-1)
    i, j = np.unravel_index(int(np.argmax(ll)), ll.shape)
    return FLOOR_GRID[i], SPREAD_GRID[j], float(ll[i, j])


def fit_scale(mean: np.ndarray, sd: np.ndarray, actual: np.ndarray) -> float:
    """The factor on the spread that makes the results most likely under
    normal(mean, s * sd): the maximum-likelihood s, sqrt(mean(z^2))."""
    z = (actual - mean) / sd
    return round(float(math.sqrt(np.mean(z ** 2))), 4)


def combine(prior: float, prior_sd: float, avg: float | None, avg_sd: float | None) -> tuple[float, float]:
    """Inverse-variance mean and sd of the prior and the poll average."""
    if avg is None:
        return prior, prior_sd
    wp, wa = 1 / prior_sd ** 2, 1 / avg_sd ** 2
    return (prior * wp + avg * wa) / (wp + wa), math.sqrt(1 / (wp + wa))


def fit(rows: list[dict], generic: dict, horizon: int) -> dict:
    """Every parameter of the method at one horizon, from training rows."""
    races = [[Poll.from_row(r) for r in row["polls"]] for row in rows]
    gen = {y: [Poll.from_row(r) for r in v] for y, v in generic.items()}
    effects = house_effects(races + list(gen.values()))
    best = None
    for hl in HALF_LIFE_GRID:
        avgs = [poll_average(p, horizon, hl, effects) for p in races]
        idx = [i for i, a in enumerate(avgs) if a is not None]
        err = np.array([avgs[i].margin - rows[i]["margin"] for i in idx])
        k = np.array([avgs[i].k_eff for i in idx])
        floor, spread, ll = fit_poll_error(err, k)
        if best is None or ll > best[3]:
            best = (hl, floor, spread, ll)
    half_life, floor, spread, _ = best
    nat = {int(y): national(gen, int(y), horizon, half_life, effects) for y in gen}
    usable = [i for i, row in enumerate(rows) if nat.get(row["year"]) is not None]
    x = np.array([[rows[i]["lean"], rows[i]["inc"], nat[rows[i]["year"]]] for i in usable])
    y = np.array([rows[i]["margin"] for i in usable])
    beta, prior_sd = fit_fundamentals(x, y)
    params = {"horizon": horizon, "halfLife": half_life, "pollFloor": floor, "pollSpread": spread,
              "beta": beta, "priorSd": prior_sd, "houseEffects": effects}
    means, sds, prior_m, avg_m, actual = [], [], [], [], []
    for i in usable:
        r = predict_raw(rows[i], races[i], nat[rows[i]["year"]], params)
        means.append(r["mean"])
        sds.append(r["sd"])
        actual.append(rows[i]["margin"])
        prior_m.append(r["prior"])
        if r["pollAverage"] is not None:
            avg_m.append((r["pollAverage"], r["pollSd"], actual[-1]))
    params["scale"] = fit_scale(np.array(means), np.array(sds), np.array(actual))
    params["priorScale"] = fit_scale(np.array(prior_m), np.full(len(prior_m), prior_sd), np.array(actual))
    params["pollScale"] = fit_scale(np.array([a[0] for a in avg_m]), np.array([a[1] for a in avg_m]),
                                    np.array([a[2] for a in avg_m]))
    return params


def predict_raw(row: dict, polls: list[Poll], nat: float, params: dict) -> dict:
    b = params["beta"]
    prior = b[0] + b[1] * row["lean"] + b[2] * row["inc"] + b[3] * nat
    avg = poll_average(polls, params["horizon"], params["halfLife"], params["houseEffects"])
    avg_sd = poll_sd(avg.k_eff, params["pollFloor"], params["pollSpread"]) if avg else None
    mean, sd = combine(prior, params["priorSd"], avg.margin if avg else None, avg_sd)
    return {"prior": prior, "pollAverage": avg.margin if avg else None, "pollSd": avg_sd,
            # Polls, not question rows: a poll printed as two questions is one.
            "pollsUsed": len({pollster_key(p.pollster) for p in avg.used}) if avg else 0, "mean": mean, "sd": sd, "average": avg}


def predict(row: dict, polls: list[Poll], nat: float, params: dict) -> dict:
    """The calibrated range and (for calibration only) chances for one seat."""
    r = predict_raw(row, polls, nat, params)
    s = params["scale"] * r["sd"]
    r.update({
        "low80": r["mean"] - Z80 * s, "high80": r["mean"] + Z80 * s,
        "low95": r["mean"] - Z95 * s, "high95": r["mean"] + Z95 * s,
        "pDem": 1 - normal_cdf(r["mean"] / s),
        "pDemPrior": 1 - normal_cdf(r["prior"] / (params["priorScale"] * params["priorSd"])),
        "pDemPolls": (1 - normal_cdf(r["pollAverage"] / (params["pollScale"] * r["pollSd"])))
        if r["pollAverage"] is not None else None,
    })
    return r


# ---------------------------------------------------------------------------
# Held-out evaluation
# ---------------------------------------------------------------------------


def brier(p: list[float], dem_won: list[float]) -> float:
    return float(np.mean((np.array(p) - np.array(dem_won)) ** 2))


def log_loss(p: list[float], dem_won: list[float]) -> float:
    q = np.clip(np.array(p), 1e-6, 1 - 1e-6)
    y = np.array(dem_won)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def calibration_table(p: list[float], dem_won: list[float], years: list[int]) -> list[dict]:
    rows = []
    edges = CALIBRATION_EDGES
    for lo, hi in zip(edges, edges[1:]):
        idx = [i for i, x in enumerate(p) if lo <= x < hi or (hi == 1.0 and x == 1.0)]
        if not idx:
            rows.append({"low": lo, "high": hi, "n": 0})
            continue
        rows.append({
            "low": lo, "high": hi, "n": len(idx),
            "meanChance": round(float(np.mean([p[i] for i in idx])), 3),
            "demShare": round(float(np.mean([dem_won[i] for i in idx])), 3),
            "demCarried": int(sum(dem_won[i] for i in idx)),
            "years": [min(years[i] for i in idx), max(years[i] for i in idx)],
        })
    return rows


def leave_one_cycle_out(rows: list[dict], generic: dict, horizon: int) -> dict:
    """Every race predicted by a fit that never saw its cycle."""
    out = []
    for year in sorted({r["year"] for r in rows}):
        train = [r for r in rows if r["year"] != year]
        test = [r for r in rows if r["year"] == year]
        gen_train = {y: v for y, v in generic.items() if int(y) != year}
        params = fit(train, gen_train, horizon)
        # The national average for the held-out year uses only that year's
        # generic-ballot polls (and the house effects of the other years).
        gen_all = {y: [Poll.from_row(x) for x in v] for y, v in generic.items()}
        nat = national(gen_all, year, horizon, params["halfLife"], params["houseEffects"])
        if nat is None:
            continue
        for r in test:
            pr = predict(r, [Poll.from_row(x) for x in r["polls"]], nat, params)
            out.append({"year": year, "state": r["state"], "special": r["special"], "margin": r["margin"],
                        "mean": pr["mean"], "prior": pr["prior"], "pollAverage": pr["pollAverage"],
                        "low80": pr["low80"], "high80": pr["high80"], "low95": pr["low95"], "high95": pr["high95"],
                        "pDem": pr["pDem"], "pDemPrior": pr["pDemPrior"], "pDemPolls": pr["pDemPolls"]})
    return summarize(out)


def summarize(preds: list[dict]) -> dict:
    dem = [1.0 if p["margin"] < 0 else 0.0 for p in preds]
    years = [p["year"] for p in preds]
    polled = [i for i, p in enumerate(preds) if p["pDemPolls"] is not None]

    def score(key, idx):
        return {"brier": round(brier([preds[i][key] for i in idx], [dem[i] for i in idx]), 4),
                "logLoss": round(log_loss([preds[i][key] for i in idx], [dem[i] for i in idx]), 4)}

    all_idx = list(range(len(preds)))
    return {
        "races": len(preds), "polledRaces": len(polled),
        "cycles": sorted(set(years)),
        "all": {"combined": score("pDem", all_idx), "fundamentals": score("pDemPrior", all_idx)},
        "polled": {"combined": score("pDem", polled), "fundamentals": score("pDemPrior", polled),
                   "pollsOnly": score("pDemPolls", polled)},
        "coverage80": round(float(np.mean([p["low80"] <= p["margin"] <= p["high80"] for p in preds])), 3),
        "coverage95": round(float(np.mean([p["low95"] <= p["margin"] <= p["high95"] for p in preds])), 3),
        "meanAbsError": round(float(np.mean([abs(p["mean"] - p["margin"]) for p in preds])), 2),
        "calibration": calibration_table([p["pDem"] for p in preds], dem, years),
        # Why the map shows no seat total: adding up races whose errors move
        # together (a national swing the polls missed) is not the sum of
        # independent ranges. Per held-out cycle, the seats the implied
        # chances add up to for Democrats against the seats they carried.
        "seatTotals": [
            {"year": y, "expectedDem": round(sum(preds[i]["pDem"] for i in idx), 1),
             "actualDem": int(sum(dem[i] for i in idx)), "races": len(idx),
             # The spread the total would have if the races were independent.
             "independentSd": round(math.sqrt(sum(preds[i]["pDem"] * (1 - preds[i]["pDem"]) for i in idx)), 2)}
            for y in sorted(set(years)) for idx in [[i for i, yy in enumerate(years) if yy == y]]
        ],
    }


# ---------------------------------------------------------------------------
# Structural lean
# ---------------------------------------------------------------------------

# Cook's weighting of the two presidential elections in a lean, older then
# newer (the formula scripts/fetch_state_pvi.py checks against all 51 of
# Cook's published state values).
LEAN_WEIGHTS = (0.25, 0.75)


def two_party_lean(older: dict, newer: dict, nation_older: dict, nation_newer: dict) -> float:
    """Cook's lean from {"D": votes, "R": votes} counts for the seat and the
    nation in two presidential elections, unrounded, positive = R."""
    def dshare(c):
        return c["D"] / (c["D"] + c["R"])
    return -100 * (LEAN_WEIGHTS[0] * (dshare(older) - dshare(nation_older))
                   + LEAN_WEIGHTS[1] * (dshare(newer) - dshare(nation_newer)))
