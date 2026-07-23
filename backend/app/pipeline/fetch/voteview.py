"""DW-NOMINATE member ideal points — per-run ingestion from Voteview.

Feeds Constituent Alignment's position-congruence component
(score_calculator v6.11): each member's roll-call ideal point scored
against what a same-party member of a comparably-leaning seat typically
holds. Fully automated, like every other data source on this platform —
each chamber's pipeline refreshes its own section of
/data/member_ideal_points.json (the persistent writable volume) every
run, the same read-merge-write, never-abort-the-run pattern as
party_ideology_bounds.json. There is no offline generation step: the
component is inert only until the first successful ingest, and a
fetch/gate failure on a later run keeps the last good data rather than
degrading scores (missing/stale data is never punitive).

Source: Voteview / Lewis et al., "Voteview: Congressional Roll-Call
Votes Database" (voteview.com), per-congress member-ideology exports —
the canonical academic source for DW-NOMINATE estimates, updated weekly
while a congress sits. Two small CSVs per run (~15KB Senate, ~60KB
House).

Construct (Canes-Wrone, Brady & Cogan 2002, "Out of Step, Out of
Office," APSR 96:1 — district-relative ideological extremity):

    For each chamber and each major party, fit an ordinary-least-squares
    regression of nominate_dim1 on the seat's Cook PVI (the platform's
    own state_pvi.json / district_pvi.json, positive = R lean):

        expected_dim1(seat) = a_party + b_party * seat_pvi

    A member's extremity is their signed residual, oriented so positive
    = toward their party's flank (more liberal than expected for a D,
    more conservative than expected for an R). Per-party fits — rather
    than one pooled fit — deliberately avoid the "leapfrog" bimodality
    problem (Bafumi & Herron 2010): a pooled line predicts a near-center
    position for swing seats that real members of either party never
    occupy, which would penalize every swing-seat member structurally.

    extremity_p90 (per chamber, across both parties) is the saturation
    scale: the most out-of-step ~decile spans the scoring component's
    full range. Data-derived, recomputed on every ingest.

Independents (party_code 328) are included in the per-member positions
(score_calculator scores them against the fit of the party they caucus
with) but excluded from the regressions.

Ingestion gates (same guard-the-ingestion role as fetch_state_pvi.py's):
a swapped column or sign flip here would silently mis-score every
member's position congruence, so a gated failure keeps the previous
run's data and flags the run rather than writing bad numbers.
"""

import csv
import io
import logging
import statistics

import httpx

from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

MEMBERS_URL = "https://voteview.com/static/data/out/members/{letter}{congress}_members.csv"

SOURCE_DESC = (
    "Voteview (Lewis et al., voteview.com) per-congress member-ideology "
    "exports, nominate_dim1; seat lean from state_pvi.json / "
    "district_pvi.json. Refreshed automatically each pipeline run by "
    "app/pipeline/fetch/voteview.py."
)
METHOD_DESC = (
    "Per chamber, per major party: OLS nominate_dim1 = a + b*seat_pvi "
    "(seat_pvi positive = R lean; state PVI for senators, district PVI "
    "for House). extremity = residual signed toward the party flank "
    "(-residual for D, +residual for R); extremity_p90 = 90th percentile "
    "of |extremity| across the chamber's D+R members. Construct: "
    "Canes-Wrone, Brady & Cogan 2002 district-relative extremity; "
    "per-party fits avoid Bafumi & Herron 2010 leapfrog bimodality."
)

# Voteview party_code -> this codebase's party letter (majors only; the
# regressions use majors, everyone with a bioguide+dim1 lands in members).
PARTY_CODES = {100: "D", 200: "R"}

_CHAMBER_LETTER = {"senate": "S", "house": "H"}

# Voteview is a small academic site; one request per chamber per nightly
# run needs no aggressive pacing, but the shared limiter keeps retries
# polite if the site is struggling.
_rate_limiter = RateLimiter(rps=2.0)


async def fetch_member_rows(
    chamber: str, congress: int, client: httpx.AsyncClient | None = None,
) -> list[dict] | None:
    """Fetch and parse one chamber's member-ideology CSV for one congress.
    Returns parsed rows, or None on fetch failure (caller keeps last good
    data). President rows (Voteview includes them) are dropped."""
    url = MEMBERS_URL.format(letter=_CHAMBER_LETTER[chamber], congress=congress)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=True)
    try:
        resp = await fetch_with_retry(
            client, _rate_limiter, "GET", url,
            retry_on_4xx=False, log_label=f"voteview {chamber} members",
        )
        if resp is None:
            return None
        return [
            row for row in csv.DictReader(io.StringIO(resp.text))
            if row.get("chamber") != "President"
        ]
    except Exception:
        logger.warning("Voteview %s fetch failed", chamber, exc_info=True)
        return None
    finally:
        if own_client:
            await client.aclose()


def _seat_pvi_for(row: dict, chamber: str,
                  state_pvi: dict[str, int], district_pvi: dict[str, int]) -> int | None:
    st = (row.get("state_abbrev") or "").strip().upper()
    if chamber == "senate":
        return state_pvi.get(st)
    try:
        d = int(float(row.get("district_code") or 0))
    except ValueError:
        return None
    # district_pvi.json keys at-large seats "ST-0"; Voteview uses 1 for
    # some at-large states — try the literal key first, then the
    # at-large fallback.
    return district_pvi.get(f"{st}-{d}", district_pvi.get(f"{st}-0"))


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Closed-form simple OLS: returns (a, b, r_squared) for y = a + b*x."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0
    return a, b, r2


def build_chamber_ideal_points(
    rows: list[dict], chamber: str,
    state_pvi: dict[str, int], district_pvi: dict[str, int],
) -> tuple[dict, list[str]]:
    """One chamber's {members, fit, extremity_p90} section from parsed
    Voteview rows, plus build-stage failure strings (empty = clean)."""
    members: dict[str, float] = {}
    by_party: dict[str, list[tuple[float, float]]] = {"D": [], "R": []}
    unresolved_seats = 0

    for row in rows:
        bio = (row.get("bioguide_id") or "").strip()
        raw_dim1 = (row.get("nominate_dim1") or "").strip()
        if not bio or not raw_dim1:
            continue  # no estimate yet (e.g. a freshman pre-first-scaling)
        try:
            dim1 = float(raw_dim1)
        except ValueError:
            continue
        members[bio] = round(dim1, 4)
        try:
            party = PARTY_CODES.get(int(row.get("party_code") or 0))
        except ValueError:
            party = None
        pvi = _seat_pvi_for(row, chamber, state_pvi, district_pvi)
        if pvi is None:
            unresolved_seats += 1
            continue
        if party:
            by_party[party].append((float(pvi), dim1))

    fit: dict[str, dict[str, float]] = {}
    extremities: list[float] = []
    failures: list[str] = []
    for party, pairs in by_party.items():
        if len(pairs) < 20:
            failures.append(f"{chamber}/{party}: only {len(pairs)} members with seat+dim1 — parse drift?")
            continue
        xs = [p for p, _ in pairs]
        ys = [d for _, d in pairs]
        a, b, r2 = _ols(xs, ys)
        fit[party] = {"a": round(a, 5), "b": round(b, 6), "n": len(pairs), "r2": round(r2, 3)}
        for pvi, dim1 in pairs:
            residual = dim1 - (a + b * pvi)
            extremities.append(abs(-residual if party == "D" else residual))

    extremity_p90 = round(statistics.quantiles(extremities, n=10)[8], 4) if len(extremities) >= 40 else None

    if unresolved_seats:
        logger.info("voteview %s: %d members with no resolvable seat PVI (excluded from fit only)",
                    chamber, unresolved_seats)
    return {"members": members, "fit": fit, "extremity_p90": extremity_p90}, failures


def ingestion_gates(chamber: str, data: dict) -> list[str]:
    """Structural + fidelity checks — guard the ingestion, not the scores."""
    failures = []
    members = data["members"]
    lo, hi = (90, 105) if chamber == "senate" else (380, 450)
    if not (lo <= len(members) <= hi):
        failures.append(f"{chamber}: {len(members)} members with dim1, expected {lo}-{hi}")
    if not all(-1.2 <= v <= 1.2 for v in members.values()):
        failures.append(f"{chamber}: nominate_dim1 outside [-1.2, 1.2] — column drift?")
    for party in ("D", "R"):
        f = data["fit"].get(party)
        if not f:
            failures.append(f"{chamber}/{party}: no regression fit produced")
            continue
        # Within BOTH parties, redder seats elect more conservative
        # members (the whole premise of a seat-conditional norm — and the
        # robust empirical pattern in every modern congress). b <= 0
        # means a sign flip or swapped join.
        if f["b"] <= 0:
            failures.append(f"{chamber}/{party}: fit slope b={f['b']} <= 0 — sign flip or bad join")
    d_fit, r_fit = data["fit"].get("D"), data["fit"].get("R")
    if d_fit and r_fit and not (d_fit["a"] < r_fit["a"]):
        failures.append(f"{chamber}: D intercept {d_fit['a']} not left of R intercept {r_fit['a']} — party columns swapped?")
    if not data.get("extremity_p90"):
        failures.append(f"{chamber}: no extremity_p90 computed")
    return failures


async def refresh_member_ideal_points(
    chamber: str, congress: int, client: httpx.AsyncClient | None = None,
) -> bool:
    """Fetch, build, gate, and persist one chamber's ideal-point section.

    Returns True on a successful write, False otherwise. NEVER raises and
    never writes gated-bad data: any failure keeps the previous run's
    section on the volume (the scoring loader's stale-data posture), logs
    why, and lets the pipeline run continue — best-effort side artifact,
    same contract as write_party_ideology_bounds.
    """
    from app.pipeline.analyze.score_calculator import (
        _district_pvi, _state_pvi, write_member_ideal_points,
    )
    try:
        rows = await fetch_member_rows(chamber, congress, client=client)
        if rows is None:
            logger.warning(
                "Voteview %s unreachable — keeping previous member_ideal_points data", chamber,
            )
            return False
        data, failures = build_chamber_ideal_points(rows, chamber, _state_pvi(), _district_pvi())
        failures += ingestion_gates(chamber, data)
        if failures:
            for f in failures:
                logger.warning("Voteview %s ingestion gate failed: %s", chamber, f)
            return False
        write_member_ideal_points(chamber, data)
        fits = ", ".join(
            f"{p}: a={f['a']:+.3f} b={f['b']:+.5f} r2={f['r2']:.2f} n={f['n']}"
            for p, f in data["fit"].items()
        )
        logger.info(
            "Voteview %s ideal points refreshed: %d members, p90 |extremity| %s, fits [%s]",
            chamber, len(data["members"]), data["extremity_p90"], fits,
        )
        return True
    except Exception:
        logger.warning(
            "Voteview %s ideal-point refresh failed — keeping previous data; run continues",
            chamber, exc_info=True,
        )
        return False
