"""Roll-call member ideal points — per-run ingestion from Voteview.

Feeds Constituent Alignment's position-congruence component
(score_calculator v6.11): each member's roll-call ideal point scored
against what a same-party member of a comparably-leaning seat typically
holds. Fully automated, like every other data source on this platform —
each chamber's pipeline refreshes its own section of
/data/member_ideal_points.json (the persistent writable volume) every
run, read-merge-write, never aborting the run. There is no offline
generation step: the component is inert only until the first successful
ingest, and a fetch/gate failure on a later run keeps the last good data
rather than degrading scores (missing/stale data is never punitive). Since
v6.27 that holds within a Congress: each section records its Congress, and
the score reads it only for roll calls of that Congress (principle 6), so
once a new Congress's roll calls are being scored the component is left
out until its Voteview export passes the gates, as when no data exists.

Source: Voteview / Lewis et al., "Voteview: Congressional Roll-Call
Votes Database" (voteview.com), per-congress member-ideology exports —
the canonical academic source for NOMINATE estimates, updated weekly
while a congress sits. Two small CSVs per run (~15KB Senate, ~60KB
House).

Which position (v6.13): the congress-specific Nokken-Poole first
dimension (Nokken & Poole 2004), not career-constrained DW-NOMINATE.
DW-NOMINATE lets a member move only along a linear trend across their
whole career, so a sitting member's "current" position is partly their
record from past congresses — the opposite of AGENTS.md principle 6. And
tested, not assumed: on the 108th House, a congress-specific position
predicted incumbents' 2004 vote share better than DW-NOMINATE and
dominated it when both were entered (docs/research/constituent-
alignment.md). A chamber falls back to nominate_dim1 as a whole — never
mixed within one fit — only when Voteview hasn't published Nokken-Poole
estimates for most of it yet; the chosen column is recorded as
"measure".

Construct (Canes-Wrone, Brady & Cogan 2002, "Out of Step, Out of
Office," APSR 96:1 — district-relative ideological extremity):

    For each chamber and each major party, fit an ordinary-least-squares
    regression of the member's dim1 position on the seat's Cook PVI (the
    platform's own state_pvi.json / district_pvi.json, positive = R lean):

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
    full range. Data-derived, recomputed on every ingest. Pooled, not one
    per party: the two predicted election results equally well and the
    test of which unit voters respond to was inconclusive (research note
    section 14), so the shipped design stands.

    Thin records (v6.27): a Nokken-Poole position estimated from few roll
    calls is mostly noise (research note section 14). Each member's count
    of scaled roll calls is stored under "votes", and the section stores the
    measured reliability (score_calculator._position_reliability, from
    scripts/calibrate_position_confidence.py; position_confidence turns it
    into a weight in [0, 1], 1 for a full record). A row with no count (or
    0) but a career DW-NOMINATE position has no "votes" entry and gets the
    weight measured for such positions; one with neither (just sworn in)
    is recorded as 0 votes and sits at 50. A row whose Nokken-Poole
    coordinates are both exactly 0 is Voteview's placeholder for a member
    it could not scale, not an estimate: it is left out, and the score
    reads that member as having no position (50). The fits are taken over
    every member (a stated choice): no constant offset of a thin record's
    position is detected (position_confidence.json's thin_offset).
    extremity_p90 is taken over full records only; early in a Congress,
    with too few of them, the chamber's last scale is carried
    ("scale_congress" names the Congress it was measured on), so the
    weights pull thin positions toward 50 rather than cancelling against a
    scale shrunk with them. The weight applies to the DW-NOMINATE fallback
    too, as an uncalibrated extension: n0 was measured on Nokken-Poole
    positions, but this Congress's count is the current term's evidence
    either way. A new Congress's section keeps the last Congress's
    positions under "prior" (previous_positions), read only by the flank
    rule in party_line_record, never scored.

Independents (party_code 328) are included in the per-member positions
(score_calculator scores them against the fit of the party they caucus
with) but excluded from the regressions.

Ingestion gates (same guard-the-ingestion role as fetch_state_pvi.py's):
a swapped column or sign flip here would silently mis-score every
member's position congruence, so a gated failure keeps the previous
run's data and flags the run rather than writing bad numbers. Gating is
PER CHAMBER, not per party, deliberately: both parties' fits must pass
together, or neither is written. A platform whose core claim is being
nonpartisan cannot ship a scoring component that only one party can
structurally earn — even when the underlying reason is a real,
measured data fact rather than any political virtue, the asymmetry
itself is the problem. If either party's data doesn't support the
construct, BOTH parties in that chamber fall back to the existing
seat-relative vote-alignment component alone (score_calculator.py) — the
same footing they've always had.

Measured, not assumed: when this gate was written (2026-07), Senate
Republicans' seat-PVI-vs-position slope was not statistically real (OLS
b=-0.00056 p=0.88; Theil-Sen gave an even more negative slope; Spearman
rho=-0.10 p=0.47; excluding the two best-known crossers changed nothing,
r2=0.024 p=0.26), so the slope gate below held the Senate's component
off and its Constituent Alignment rested on the vote part alone. That is
consistent with the congressional-elections literature's distinction
between candidate-centered Senate races and more partisan-lean-tracking
House races (the construct's source paper, Canes-Wrone/Brady/Cogan 2002,
is itself House-focused), and it is NOT something to patch by
substituting a different seat-safety variable just to force a fit
through. The gate re-measures this from scratch on every run: on the
119th Congress's October 2026 export the Senate Republican slope is
positive (b=+0.0018, r2=0.004, Voteview's placeholders left out), both
parties pass, and the Senate's position-congruence component is live, as
the House's is. A run whose slope is not positive fails its gates: within a
Congress the last good section stays, and a new Congress's component waits
for a run that passes.
"""

import asyncio
import csv
import io
import logging
import statistics

import httpx

from app.http_client import make_async_client
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

MEMBERS_URL = "https://voteview.com/static/data/out/members/{letter}{congress}_members.csv"
VOTES_URL = "https://voteview.com/static/data/out/votes/{letter}{congress}_votes.csv"

SOURCE_DESC = (
    "Voteview (Lewis et al., voteview.com) per-congress member-ideology "
    "exports, nokken_poole_dim1 (nominate_dim1 when unpublished); seat lean from state_pvi.json / "
    "district_pvi.json (the sitting Congress's district lines). Refreshed "
    "automatically each pipeline run by "
    "app/pipeline/fetch/voteview.py."
)
METHOD_DESC = (
    "Per chamber, per major party: OLS dim1 = a + b*seat_pvi "
    "(seat_pvi positive = R lean; state PVI for senators, district PVI "
    "for House). extremity = residual signed toward the party flank "
    "(-residual for D, +residual for R); extremity_p90 = 90th percentile "
    "of |extremity| across the chamber's D+R members with full records "
    "(Voteview's 0,0 placeholders excluded). votes = each member's "
    "count of scaled roll calls; the score weights a position's extremity by "
    "position_confidence(votes, reliability); with too few full records the "
    "chamber's last scale is carried; prior = the last Congress's positions, read only by "
    "the flank-break rule. Construct: "
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
        client = make_async_client(follow_redirects=True)
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


def _seat_district(row: dict, district_pvi: dict[str, int]) -> int:
    """The district number a House row's PVI resolves under: its own, or
    0 when the state is at-large (Voteview codes some at-large seats 1)."""
    st = (row.get("state_abbrev") or "").strip().upper()
    d = int(_number(row.get("district_code")) or 0)
    return d if f"{st}-{d}" in district_pvi else 0


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


# (CSV column, name shown in score breakdowns), in order of preference.
POSITION_COLUMNS = (("nokken_poole_dim1", "Nokken-Poole"), ("nominate_dim1", "DW-NOMINATE"))


def _position_column(rows: list[dict]) -> tuple[str, str]:
    """The congress-specific Nokken-Poole column when Voteview has published
    it for at least 90% of the members with any estimate, else DW-NOMINATE —
    one column for the whole chamber, so every member is scored on the same
    scale as the regression they are compared against."""
    def count(col):
        return sum(1 for r in rows if (r.get(col) or "").strip())
    available = max(count(col) for col, _ in POSITION_COLUMNS)
    for col, name in POSITION_COLUMNS:
        if available and count(col) >= 0.9 * available:
            return col, name
    return POSITION_COLUMNS[-1]


# Full records the saturation scale needs: below it (early in a Congress)
# the chamber's last scale is carried. The same floor as the old
# all-member quantile's (40).
SCALE_MIN_FULL = 40


def _vote_count(row: dict) -> int | None:
    try:
        n = int(float(row.get("nominate_number_of_votes") or ""))
    except (ValueError, OverflowError):
        return None
    return n if n >= 0 else None


def _number(value) -> float | None:
    """A Voteview CSV number: "200", "200.0" and "3.0" all parse (several
    Congresses' exports write floats); blank, NaN and junk do not."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _is_placeholder(row: dict) -> bool:
    """Voteview's 0, 0 for a member it could not scale: no estimate."""
    return _number(row.get("nokken_poole_dim1")) == 0 and _number(row.get("nokken_poole_dim2")) == 0


def _icpsr(row: dict) -> str:
    """An ICPSR id as one spelling: some exports write "14009.0"."""
    x = _number(row.get("icpsr"))
    return str(int(x)) if x is not None else str(row.get("icpsr") or "")


def switched_members(rows: list[dict]) -> bool:
    """Whether any member is listed under two ICPSR ids: a party switch
    during the Congress."""
    return bool(_switchers(rows))


def _switchers(rows: list[dict]) -> dict[str, list[str]]:
    """bioguide -> its ICPSR ids, in the export's order, for each member
    listed under more than one."""
    ids: dict[str, list[str]] = {}
    for row in rows:
        if (bio := (row.get("bioguide_id") or "").strip()) and _icpsr(row) not in ids.setdefault(bio, []):
            ids[bio].append(_icpsr(row))
    return {bio: v for bio, v in ids.items() if len(v) > 1}


def switcher_latest(
    rows: list[dict], earlier_ids: set[str], first_roll: dict[str, int] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Each switcher's latest ICPSR id, the one Voteview opened at the
    switch: the only one of their ids not in the last Congress's export
    (`earlier_ids`), or, when that doesn't single one out (a member who
    switched in their first Congress has two new ids), the one whose first
    roll call (`first_roll`) comes last, or that has none yet. Returns ({bioguide: id}, the
    bioguides neither settles)."""
    latest, unresolved = {}, []
    for bio, ids in _switchers(rows).items():
        new = [i for i in ids if i not in earlier_ids]
        if len(new) == 1:
            latest[bio] = new[0]
        elif first_roll is not None:
            # An id with no roll call yet is the newest; two at the top
            # (neither has voted) settle nothing.
            order = sorted(ids, key=lambda i: first_roll.get(i, float("inf")))
            if first_roll.get(order[-1], float("inf")) == first_roll.get(order[-2], float("inf")):
                unresolved.append(bio)
            else:
                latest[bio] = order[-1]
        else:
            unresolved.append(bio)
    return latest, unresolved


def _one_row_per_member(rows: list[dict], latest: dict[str, str] | None = None) -> list[dict]:
    """One row per bioguide id, in the export's order; rows with no
    bioguide id pass through. A switcher keeps the row of their latest id
    (`latest`, from switcher_latest), whether or not Voteview has placed it
    yet (a record since the switch with no position is no position yet,
    never a reason to read the old one); without `latest`, the later row in
    the export's order, which is usually but not always the latest."""
    keep: dict[str, dict] = {}
    for row in rows:
        bio = (row.get("bioguide_id") or "").strip()
        if not bio:
            continue
        if latest and bio in latest:
            if _icpsr(row) == latest[bio] and bio not in keep:
                keep[bio] = row
        else:
            keep[bio] = row
    return [row for row in rows if not (bio := (row.get("bioguide_id") or "").strip()) or keep.get(bio) is row]


def latest_rows(rows: list[dict], latest: dict[str, str] | None = None) -> list[dict]:
    """The rows build_chamber_ideal_points reads: one per member, a party
    switcher's latest (see _one_row_per_member). For scripts that read the
    rows beside the built section."""
    return _one_row_per_member(rows, latest)


def build_chamber_ideal_points(
    rows: list[dict], chamber: str,
    state_pvi: dict[str, int], district_pvi: dict[str, int],
    *, reliability: dict, congress: int | None = None, latest: dict[str, str] | None = None,
) -> tuple[dict, list[str]]:
    """One chamber's {members, votes, fit, extremity_p90, ...} section from
    parsed Voteview rows, plus build-stage failure strings (empty = clean).
    `reliability` (score_calculator._position_reliability) is stored for the
    score's weight. It doesn't filter the fits (no constant offset of a thin
    position is detected: position_confidence.json's thin_offset). The
    saturation scale is the 90th percentile of the full records'
    extremities (reliability's reference_votes or more, which count in
    full): a thin record's noise would widen it, and scaling it by the
    weights would cancel them whenever every record is equally thin. With
    fewer than SCALE_MIN_FULL full records (early in a Congress) it is None
    and refresh_member_ideal_points carries the chamber's last scale.

    A member Voteview lists twice in one Congress (a party switch during it,
    under a second ICPSR id) is read on their latest record, the one since
    the switch (`latest`, from switcher_latest), and only it enters the
    fits: one seat, one position. Reading the record since the switch is a
    choice, the record of who the member now is; the evidence
    (calibrate_position_confidence.switcher_test) is
    consistent with it but can't settle it. Until Voteview places that
    record, the member has no position."""

    column, measure = _position_column(rows)
    switched = sorted(_switchers(rows))
    rows = _one_row_per_member(rows, latest)
    members: dict[str, float] = {}
    parties: dict[str, str | None] = {}
    votes: dict[str, int] = {}
    seats: set[str] = set()
    seated = 0
    by_party: dict[str, list[tuple[float, float, bool]]] = {"D": [], "R": []}
    reference = (reliability or {}).get("reference_votes")
    unresolved_seats = 0

    for row in rows:
        bio = (row.get("bioguide_id") or "").strip()
        raw_dim1 = (row.get(column) or "").strip()
        if bio:
            # Every member's party, placed or not: the flank rule tells a
            # switch between Congresses by it.
            code = _number(row.get("party_code"))
            parties[bio] = (PARTY_CODES.get(int(code)) or str(int(code))) if code is not None else None
        if not bio or not raw_dim1:
            continue  # no estimate yet (e.g. a freshman pre-first-scaling)
        if column == "nokken_poole_dim1" and _is_placeholder(row):
            continue
        try:
            dim1 = float(raw_dim1)
        except ValueError:
            continue
        members[bio] = round(dim1, 4)
        n_votes = _vote_count(row) or None
        if n_votes is None and _number(row.get("nominate_dim1")) is None:
            # No count and no career position: newly sworn in, nothing yet
            # behind the position; it rests on no votes. (A member with a
            # career position and no count is the uncounted case the
            # calibration measures, left out of `votes`.)
            n_votes = 0
        if n_votes is not None:
            votes[bio] = n_votes
        code = _number(row.get("party_code"))
        party = PARTY_CODES.get(int(code)) if code is not None else None
        pvi = _seat_pvi_for(row, chamber, state_pvi, district_pvi)
        if pvi is None:
            unresolved_seats += 1
            continue
        seated += 1
        st = (row.get("state_abbrev") or "").strip().upper()
        # The seat the PVI lookup resolved, so "3" and "3.0", or an
        # at-large state coded 0 in one row and 1 in another, are one seat.
        seats.add(f"{st}-{_seat_district(row, district_pvi)}" if chamber == "house" else st)
        if party:
            full = not reference or (n_votes is not None and n_votes >= reference)
            by_party[party].append((float(pvi), dim1, full))

    fit: dict[str, dict[str, float]] = {}
    extremities: list[float] = []
    failures: list[str] = []
    for party, pairs in by_party.items():
        if len(pairs) < 20:
            failures.append(f"{chamber}/{party}: only {len(pairs)} members with seat+dim1 — parse drift?")
            continue
        xs = [p for p, _, _ in pairs]
        ys = [d for _, d, _ in pairs]
        a, b, r2 = _ols(xs, ys)
        fit[party] = {"a": round(a, 5), "b": round(b, 6), "n": len(pairs), "r2": round(r2, 3)}
        for pvi, dim1, full in pairs:
            if full:
                residual = dim1 - (a + b * pvi)
                extremities.append(abs(-residual if party == "D" else residual))

    extremity_p90 = (
        round(statistics.quantiles(extremities, n=10)[8], 4) if len(extremities) >= SCALE_MIN_FULL else None
    )

    if unresolved_seats:
        logger.info("voteview %s: %d members with no resolvable seat PVI (excluded from fit only)",
                    chamber, unresolved_seats)
    return {
        "members": members, "votes": votes, "fit": fit, "extremity_p90": extremity_p90,
        "measure": measure, "congress": congress, "seats": len(seats), "seated": seated,
        # Each position's party, and the members who switched parties during
        # this Congress: the flank rule never reads a last-Congress record
        # cast in another party (one of these, or a party that differs
        # between the two sections).
        "parties": parties, "switched": switched,
        "reliability": dict(reliability), "scale_congress": congress if extremity_p90 else None,
    }, failures


def ingestion_gates(chamber: str, data: dict) -> list[str]:
    """Structural + fidelity checks — guard the ingestion, not the scores."""
    failures = []
    members = data["members"]
    # Seats, not rows: a Congress's export also lists every member who left
    # and their replacement, and the House's delegates, so the row count
    # legitimately runs past the chamber's size (451 rows for the 119th
    # House by October 2026, which failed a 380-450 row bound and kept the
    # House on stale data). Seats with a positioned member can't.
    lo, hi = (45, 50) if chamber == "senate" else (380, 435)
    if not (lo <= data.get("seats", 0) <= hi):
        failures.append(f"{chamber}: {data.get('seats', 0)} seats with a member and dim1, expected {lo}-{hi}")
    # And most of each seat filled: an export with one senator per state
    # covers every state.
    floor = 90 if chamber == "senate" else 380
    if data.get("seated", 0) < floor:
        failures.append(f"{chamber}: {data.get('seated', 0)} seated members with dim1, expected at least {floor}")
    # Headroom beyond [-1, 1] so a legitimately extreme estimate never trips
    # it; this guards against reading the wrong column, not against outliers.
    if not all(-1.5 <= v <= 1.5 for v in members.values()):
        failures.append(f"{chamber}: dim1 outside [-1.5, 1.5] — column drift?")
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
        failures.append(f"{chamber}: no saturation scale (too few full records; none carried)")
    return failures


def with_carried_scale(data: dict, previous: dict) -> dict:
    """`data` with the chamber's last saturation scale when it has too few
    full records for its own. The scale describes the spread of the
    chamber's seats, not any member's record, so it holds across a
    Congress's first weeks."""
    if data.get("extremity_p90") or not (previous or {}).get("extremity_p90"):
        return data
    return {**data, "extremity_p90": previous["extremity_p90"],
            "scale_congress": previous.get("scale_congress") or previous.get("congress")}


def previous_positions(previous: dict, congress: int | None) -> dict | None:
    """The last Congress's positions, kept beside a new Congress's section
    for party_line_record's flank rule only (never scored: principle 6).
    Early in a Congress every new position rests on a few roll calls, and
    a lone defector's side of their party is then unreliable; the last
    Congress's full record is the better evidence of it until the new one
    catches up. Taken from the previous section when it is the Congress just
    before, or carried from one of the same Congress; only from a v6.27
    section, whose counts and reliability give each position its weight."""
    have = (previous or {}).get("congress")
    if have is None or congress is None:
        return None
    if int(have) == int(congress):
        return previous.get("prior")
    if int(have) != int(congress) - 1 or not isinstance(previous.get("reliability"), dict):
        # Only the Congress just before: the evidence for the rule
        # (calibrate_position_confidence.prior_test) is adjacent Congresses.
        return None
    return {"congress": previous["congress"], "members": previous.get("members") or {},
            "votes": previous.get("votes") or {}, "parties": previous.get("parties") or {},
            "reliability": previous["reliability"]}


def _first_rolls(text: str) -> dict[str, int]:
    """ICPSR id -> its first roll call in a vote export's text."""
    first: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        number = _number(row.get("rollnumber"))
        if number is None:
            continue  # no roll call: says nothing about when the id began
        i = _icpsr(row)
        first[i] = min(first.get(i, int(number)), int(number))
    return first


async def fetch_first_rolls(
    chamber: str, congress: int, client: httpx.AsyncClient | None = None,
) -> dict[str, int] | None:
    """ICPSR id -> the first roll call with a row in one chamber's vote
    export for one Congress, or None on fetch failure. Large, so fetched
    only when a switcher's latest id can't be told apart otherwise."""
    url = VOTES_URL.format(letter=_CHAMBER_LETTER[chamber], congress=congress)
    own_client = client is None
    if own_client:
        client = make_async_client(follow_redirects=True)
    try:
        resp = await fetch_with_retry(
            client, _rate_limiter, "GET", url, retry_on_4xx=False, log_label=f"voteview {chamber} votes",
        )
        if resp is None:
            return None
        # A whole chamber's votes: parsed off the event loop.
        return await asyncio.to_thread(_first_rolls, resp.text)
    except Exception:
        logger.warning("Voteview %s votes fetch failed", chamber, exc_info=True)
        return None
    finally:
        if own_client:
            await client.aclose()


async def _latest_ids(
    rows: list[dict], chamber: str, congress: int, client: httpx.AsyncClient | None,
) -> dict[str, str] | None:
    """switcher_latest for this export: {} with no switcher, None when a
    switcher's latest id can't be settled (an export that can't be read,
    or two new ids neither of which has voted): the caller keeps the
    previous section rather than guess."""
    if not switched_members(rows):
        return {}
    last = await fetch_member_rows(chamber, congress - 1, client=client)
    if last is None:
        return None
    latest, unresolved = switcher_latest(rows, {_icpsr(r) for r in last})
    if unresolved:
        first = await fetch_first_rolls(chamber, congress, client=client)
        if first is None:
            return None
        latest, unresolved = switcher_latest(rows, {_icpsr(r) for r in last}, first)
    return None if unresolved else latest


async def refresh_member_ideal_points(
    chamber: str, congress: int, client: httpx.AsyncClient | None = None,
) -> bool:
    """Fetch, build, gate, and persist one chamber's ideal-point section.

    Returns True on a successful write, False otherwise. NEVER raises and
    never writes gated-bad data: any failure keeps the previous run's
    section on the volume (the scoring loader's stale-data posture), logs
    why, and lets the pipeline run continue — best-effort side artifact,
    never aborting the pipeline run.
    """
    from app.pipeline.analyze.score_calculator import (
        _district_pvi, _member_ideal_points, _position_reliability, _state_pvi,
        write_member_ideal_points,
    )
    try:
        rows = await fetch_member_rows(chamber, congress, client=client)
        if rows is None:
            logger.warning(
                "Voteview %s unreachable — keeping previous member_ideal_points data", chamber,
            )
            return False
        latest = await _latest_ids(rows, chamber, congress, client)
        condition = f"voteview-switcher-{chamber}"
        if latest is None:
            logger.warning(
                "Voteview %s: a party switcher's latest record can't be told apart — keeping "
                "previous member_ideal_points data", chamber,
            )
            # Kept data stops being current at the next Congress, when the
            # position part drops out: worth an operator's look, once a
            # Congress.
            from app import ops_alerts
            from app.ordinals import ordinal
            await asyncio.to_thread(
                ops_alerts.send_ops_alert,
                f"Voteview {chamber}: party switcher unresolved",
                f"The {ordinal(congress)} Congress's {chamber} export lists a member under two ids, and "
                "the exports needed to tell the record since the switch apart (the last Congress's "
                "members, the vote export) couldn't be read or didn't settle it. The previous "
                "ideal-point section is kept until they do.",
                dedupe_key=f"{condition}-{congress}", condition=condition,
            )
            return False
        from app import ops_alerts
        await asyncio.to_thread(ops_alerts.resolve_ops_alert, condition)
        data, failures = build_chamber_ideal_points(
            rows, chamber, _state_pvi(), _district_pvi(),
            reliability=_position_reliability(chamber), congress=congress, latest=latest,
        )
        previous = _member_ideal_points(chamber) or {}
        if failures == []:
            data = with_carried_scale(data, previous)
        prior = previous_positions(previous, congress)
        if prior:
            data = {**data, "prior": prior}
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
