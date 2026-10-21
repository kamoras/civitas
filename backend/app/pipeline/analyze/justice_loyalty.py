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
from statistics import NormalDist

import numpy as np

# A justice's effect is stated only where it is measured: no vote fits
# itself (a leverage of 1, a side of one vote), and the 95% interval is
# narrower than the effect's whole range, -1 to 1 (a difference of two
# shares). No count of votes is typed (it was 10 a side). The standard
# error is never below the two shares' own binomial uncertainty
# (Agresti & Caffo 2000: a success and a failure added to each side):
# HC1 collapses toward zero when a side's votes all go one way, which made
# a five-vote record look exact. On every justice in the bundled coding the
# two agree to within 7%.
Z95 = NormalDist().inv_cdf(0.975)
EFFECT_RANGE = 2.0


def measurable(se: float, max_leverage: float) -> bool:
    """Whether an estimate with standard error `se`, from a fit whose
    largest leverage is `max_leverage`, says anything (module comment)."""
    return max_leverage < 1 - 1e-9 and math.isfinite(se) and 2 * Z95 * se < EFFECT_RANGE


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


def _binomial_se(y: np.ndarray, x_in: np.ndarray) -> float:
    """The standard error of the difference of the two sides' shares with a
    success and a failure added to each (Agresti & Caffo 2000)."""
    out = 0.0
    for side in (x_in == 1, x_in == 0):
        n, p = int(side.sum()), (float(y[side].sum()) + 1) / (int(side.sum()) + 2)
        out += p * (1 - p) / (n + 2)
    return math.sqrt(out)


def fit(votes: list[tuple[int, int, int]]) -> Estimate | None:
    """One justice's effect from (for_government, appointer_in_office,
    government_petitioner) triples: OLS with an intercept, HC1 errors."""
    y = np.array([v[0] for v in votes], dtype=float)
    x_in = np.array([v[1] for v in votes], dtype=float)
    x_pet = np.array([v[2] for v in votes], dtype=float)
    n_in, n_out = int(x_in.sum()), int(len(votes) - x_in.sum())
    if not n_in or not n_out:
        return None
    X = np.column_stack([np.ones_like(y), x_in, x_pet])
    xtx_inv = np.linalg.pinv(X.T @ X)
    leverage = float(np.max(np.einsum("ij,jk,ik->i", X, xtx_inv, X)))
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta
    n, k = X.shape
    meat = (X * resid[:, None] ** 2).T @ X
    if n <= k:
        return None
    cov = xtx_inv @ meat @ xtx_inv * n / (n - k)
    se = max(float(math.sqrt(max(cov[1, 1], 0.0))), _binomial_se(y, x_in))
    if not measurable(se, leverage):
        return None
    return Estimate(
        raw=float(beta[1]), se=se,
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
    justice whose estimate isn't measurable (fit) has none."""
    return {j: e for j, r in rows.items() if (e := fit(r)) is not None}
