"""The appointing president's effect on a justice's votes (justice v3:
shown, not scored).

Epstein & Posner (2016, "Supreme Court Justices' Loyalty to the
President", J. Legal Studies 45) ask whether a justice sides with the
government more often while the president who appointed them is in office
than under other presidents. Each justice's effect here is a least-squares
fit of voting for the government on the appointing president being in
office, with whether the government was petitioner or respondent held fixed
(the Court reverses more often than it affirms), and its
heteroskedasticity-robust (HC1) standard error.

Justice v2 scored this 0-100. Justice v3 does not: a placebo study found
that windows of the same length placed later in each justice's career
reproduce most of the differences between justices, so no method yet
separates loyalty to the appointing president from career timing for an
individual justice (docs/research/justice-scores.md). The raw estimate is
stored and shown with its confidence interval, as information, unranked.

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


def estimates_by_justice(rows: dict[str, list[tuple[int, int, int]]]) -> dict[str, Estimate]:
    """Each justice's own estimate from their labeled votes (label); a
    justice without MIN_VOTES_EACH_SIDE votes both ways has none."""
    return {j: e for j, r in rows.items() if (e := fit(r)) is not None}
