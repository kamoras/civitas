"""Loyalty to the appointing president: the justice score (justice v2).

Epstein & Posner (2016, "Supreme Court Justices' Loyalty to the
President", J. Legal Studies 45) measure a justice's independence from the
president who appointed them: in cases where the president's government is
a party, does the justice side with it more often while that president is
in office than under other presidents? Comparing a justice with themselves,
it cancels a justice's ideology and how often they side with any
government, which bloc-agreement measures could not (they ranked justices
by distance from the Court's median, Spearman -0.75 to -0.82 on the 2024
term: docs/research/justice-scores.md).

Each justice's effect is a least-squares fit of voting for the government
on the appointing president being in office, with whether the government
was petitioner or respondent held fixed (the Court reverses more often than
it affirms), and its heteroskedasticity-robust (HC1) standard error. One
justice's effect rests on a few hundred votes, so each is shrunk toward the
mean of every justice's by its own noise (DerSimonian & Laird 1986 random
effects): the shrunk effect is the justice's loyalty, in points. The score
is 100 at no favoritism either way and falls linearly to 0 at twice the
spread between justices' true effects (the random-effects sd), a design
choice recorded in docs/research/justice-scores.md.

Every input is data: the Supreme Court Database's votes and the Federal
Judicial Center's appointments (fetch/justice_records.py), and the
presidents' terms stored for the president scorecard.
"""

import math
from dataclasses import dataclass

import numpy as np

# A justice's effect is estimated only with this many votes both under the
# appointing president and under others; fewer can't separate the two.
MIN_VOTES_EACH_SIDE = 10
# Where the score reaches 0, in random-effects sds of favoritism either way.
ZERO_AT_SDS = 2.0


@dataclass(frozen=True)
class Vote:
    justice: str
    date: str  # YYYY-MM-DD
    government_petitioner: bool
    for_government: bool


@dataclass(frozen=True)
class Estimate:
    raw: float  # points as a share, e.g. 0.195
    se: float
    votes_in: int  # under the appointing president
    votes_out: int
    rate_in: float  # share of votes for the government under the appointer
    rate_out: float


@dataclass(frozen=True)
class Loyalty:
    loyalty: float  # shrunk effect, share
    se: float
    score: float
    estimate: Estimate


def fit(votes: list[tuple[int, int, int]]) -> Estimate | None:
    """One justice's effect from (for_government, appointer_in_office,
    government_petitioner) triples: OLS with an intercept, HC1 errors."""
    y = np.array([v[0] for v in votes], dtype=float)
    x_in = np.array([v[1] for v in votes], dtype=float)
    x_pet = np.array([v[2] for v in votes], dtype=float)
    n_in, n_out = int(x_in.sum()), int(len(votes) - x_in.sum())
    if min(n_in, n_out) < MIN_VOTES_EACH_SIDE:
        return None
    X = np.column_stack([np.ones_like(y), x_in, x_pet])
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta
    n, k = X.shape
    meat = (X * resid[:, None] ** 2).T @ X
    cov = xtx_inv @ meat @ xtx_inv * n / (n - k)
    return Estimate(
        raw=float(beta[1]), se=float(math.sqrt(max(cov[1, 1], 0.0))),
        votes_in=n_in, votes_out=n_out,
        rate_in=float(y[x_in == 1].mean()), rate_out=float(y[x_in == 0].mean()),
    )


def shrink(estimates: dict[str, Estimate]) -> tuple[dict[str, Loyalty], float, float]:
    """({justice: Loyalty}, the mean effect, the between-justice sd): each
    estimate shrunk toward the mean by its own noise (DerSimonian-Laird)."""
    b = np.array([e.raw for e in estimates.values()])
    se2 = np.array([e.se ** 2 for e in estimates.values()])
    w = 1 / se2
    mu_fixed = (w * b).sum() / w.sum()
    q = (w * (b - mu_fixed) ** 2).sum()
    tau2 = max(0.0, (q - (len(b) - 1)) / (w.sum() - (w ** 2).sum() / w.sum()))
    w_re = 1 / (se2 + tau2)
    mu = float((w_re * b).sum() / w_re.sum())
    tau = math.sqrt(tau2)
    out = {}
    for name, e in estimates.items():
        s2 = e.se ** 2
        shrunk = (tau2 * e.raw + s2 * mu) / (tau2 + s2) if tau2 + s2 > 0 else mu
        se = math.sqrt(tau2 * s2 / (tau2 + s2)) if tau2 + s2 > 0 else 0.0
        out[name] = Loyalty(loyalty=shrunk, se=se, score=score(shrunk, tau), estimate=e)
    return out, mu, tau


def score(loyalty: float, tau: float) -> float:
    """100 at no favoritism either way, 0 at ZERO_AT_SDS between-justice sds."""
    if tau <= 0:
        return 100.0
    return round(max(0.0, 100.0 * (1 - abs(loyalty) / (ZERO_AT_SDS * tau))), 1)


def president_on(date: str, terms: list[tuple[str, str, str | None]]) -> str | None:
    """The president in office on `date`: `terms` are (person, start, end)."""
    for person, start, end in terms:
        if start <= date and (end is None or date < end):
            return person
    return None


def label(
    votes: list[Vote],
    appointer: dict[str, list[tuple[str, str, str | None]]],
    terms: list[tuple[str, str, str | None]],
) -> dict[str, list[tuple[int, int, int]]]:
    """Each justice's votes as (for_government, appointer_in_office,
    government_petitioner). `appointer`: justice -> [(appointing person,
    from, until)] (a justice appointed twice, Rehnquist, has two); `terms`:
    the presidents' (person, start, end). A vote counts toward a justice
    only under an appointment it falls in."""
    rows: dict[str, list[tuple[int, int, int]]] = {}
    for v in votes:
        spans = appointer.get(v.justice) or []
        appointed_by = next((p for p, a, b in spans if a <= v.date and (b is None or v.date < b)), None)
        in_office = president_on(v.date, terms)
        if appointed_by is None or in_office is None:
            continue
        rows.setdefault(v.justice, []).append(
            (int(v.for_government), int(in_office == appointed_by), int(v.government_petitioner)),
        )
    return rows


def loyalty_by_justice(rows: dict[str, list[tuple[int, int, int]]]) -> tuple[dict[str, Loyalty], float, float]:
    """Every justice's loyalty from their labeled votes (label), shrunk
    across all of them. Empty when fewer than three justices have enough
    votes both ways to be estimated."""
    estimates = {j: e for j, r in rows.items() if (e := fit(r)) is not None}
    if len(estimates) < 3:
        return {}, 0.0, 0.0
    return shrink(estimates)
