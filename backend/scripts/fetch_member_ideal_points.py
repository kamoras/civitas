"""Ingest DW-NOMINATE member ideal points and fit seat-conditional norms.

Writes app/data/member_ideal_points.json, which score_calculator reads
(via _member_ideal_points()) for Constituent Alignment's position-
congruence component (v6.11): each member's roll-call ideal point scored
against what a same-party member of a comparably-leaning seat typically
holds. Same generated-data convention as state_pvi.json /
small_donor_baseline.json — every number the scoring formula consumes
(per-member positions, per-party regression coefficients, saturation
scale) is computed here from real data and committed as JSON, never
hand-typed into Python.

Construct (Canes-Wrone, Brady & Cogan 2002, "Out of Step, Out of
Office," APSR 96:1 — district-relative ideological extremity):

    For each chamber and each major party, fit an ordinary-least-squares
    regression of nominate_dim1 on the seat's Cook PVI (this repo's own
    state_pvi.json / district_pvi.json, positive = R lean):

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
    full range. Data-derived, recomputed on every regeneration.

Data source (network required to regenerate):
    Voteview / Lewis et al., "Voteview: Congressional Roll-Call Votes
    Database" (voteview.com), per-congress member-ideology exports —
    the canonical academic source for DW-NOMINATE estimates, updated
    weekly while a congress sits. NOTE: some outbound proxies block
    voteview.com; run this script from an unrestricted network (it is
    an offline, run-once-per-congress generator, not pipeline code).

Independents (party_code 328) are included in the per-member positions
(score_calculator scores them against the fit of the party they caucus
with) but excluded from the regressions.

Run from the repo:
    python3 backend/scripts/fetch_member_ideal_points.py [congress] [output.json]

Exits 1 if any ingestion/fidelity gate fails.
"""

import csv
import io
import json
import pathlib
import statistics
import sys
import urllib.request

DEFAULT_CONGRESS = 119

MEMBERS_URL = "https://voteview.com/static/data/out/members/{chamber}{congress}_members.csv"

SOURCE_DESC = (
    "Voteview (Lewis et al., voteview.com) per-congress member-ideology "
    "exports, nominate_dim1; seat lean from this repo's state_pvi.json / "
    "district_pvi.json. Regenerate with "
    "backend/scripts/fetch_member_ideal_points.py."
)

UA = {"User-Agent": "CivitasCivicPlatform/1.0 (ideal-point ingestion; contact: mack.ryanm@gmail.com)"}

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "data"
DEFAULT_OUTPUT = DATA_DIR / "member_ideal_points.json"

# Voteview party_code -> this codebase's party letter (majors only; the
# regressions use majors, everyone with a bioguide+dim1 lands in members).
PARTY_CODES = {100: "D", 200: "R"}


def fetch_members(chamber_letter: str, congress: int) -> list[dict]:
    url = MEMBERS_URL.format(chamber=chamber_letter, congress=congress)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        text = r.read().decode("utf-8")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("chamber") == "President":
            continue
        rows.append(row)
    return rows


def load_seat_pvi() -> tuple[dict[str, int], dict[str, int]]:
    state = json.loads((DATA_DIR / "state_pvi.json").read_text())["states"]
    district = json.loads((DATA_DIR / "district_pvi.json").read_text())["districts"]
    return ({k: int(v) for k, v in state.items()},
            {k: int(v) for k, v in district.items()})


def seat_pvi_for(row: dict, chamber: str,
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


def ols(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
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


def build_chamber(rows: list[dict], chamber: str,
                  state_pvi: dict[str, int], district_pvi: dict[str, int]) -> tuple[dict, list[str]]:
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
        pvi = seat_pvi_for(row, chamber, state_pvi, district_pvi)
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
        a, b, r2 = ols(xs, ys)
        fit[party] = {"a": round(a, 5), "b": round(b, 6), "n": len(pairs), "r2": round(r2, 3)}
        for pvi, dim1 in pairs:
            residual = dim1 - (a + b * pvi)
            extremities.append(abs(-residual if party == "D" else residual))

    extremity_p90 = round(statistics.quantiles(extremities, n=10)[8], 4) if len(extremities) >= 40 else None

    out = {"members": members, "fit": fit, "extremity_p90": extremity_p90}
    if unresolved_seats:
        print(f"{chamber}: {unresolved_seats} members with no resolvable seat PVI (excluded from fit only)")
    return out, failures


def ingestion_gates(chamber: str, data: dict) -> list[str]:
    """Structural + fidelity checks — a swapped column or sign flip here
    would silently mis-score every member's position congruence."""
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


def main() -> int:
    congress = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONGRESS
    output = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    state_pvi, district_pvi = load_seat_pvi()
    result: dict = {
        "_source": SOURCE_DESC,
        "_method": (
            "Per chamber, per major party: OLS nominate_dim1 = a + b*seat_pvi "
            "(seat_pvi positive = R lean; state PVI for senators, district PVI "
            "for House). extremity = residual signed toward the party flank "
            "(-residual for D, +residual for R); extremity_p90 = 90th "
            "percentile of |extremity| across the chamber's D+R members. "
            "Construct: Canes-Wrone, Brady & Cogan 2002 district-relative "
            "extremity; per-party fits avoid Bafumi & Herron 2010 leapfrog "
            "bimodality."
        ),
        "congress": congress,
    }
    all_failures: list[str] = []
    for chamber, letter in (("senate", "S"), ("house", "H")):
        rows = fetch_members(letter, congress)
        data, build_failures = build_chamber(rows, chamber, state_pvi, district_pvi)
        gate_failures = ingestion_gates(chamber, data)
        all_failures += build_failures + gate_failures
        result[chamber] = data
        fits = ", ".join(
            f"{p}: a={f['a']:+.3f} b={f['b']:+.5f} r2={f['r2']:.2f} n={f['n']}"
            for p, f in data["fit"].items()
        )
        print(f"{chamber}: {len(data['members'])} members, p90 |extremity| "
              f"{data['extremity_p90']}, fits [{fits}]")

    for f in all_failures:
        print("GATE FAILED:", f)

    output.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(f"wrote {output}")
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
