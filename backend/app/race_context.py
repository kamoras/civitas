"""What shapes a federal race, as figures the ballot page only lays out.

Descriptive only. Nothing here predicts an outcome or weighs one side's
chances: a seat's lean is placed among every other seat's, and a campaign's
contributions are split into the FEC's own categories. Civitas never calls a
race (AGENTS.md, "Election night and after").
"""

import csv
import io
from collections import Counter

from app.district_lean import lean_label
from app.election_calendar import federal_states
from app.models import Candidate
from app.readout import mark
from app.score_display import shown_share

# The FEC totals row's categories of `contributions`, which add up to it:
# (key the API sends, Candidate column). "Smaller" individual donors are the
# ones the FEC does not itemize — a campaign itemizes a donor only once their
# gifts to it pass $200 in the election (52 U.S.C. 30104(b)(3)(A)).
FUNDING_CATEGORIES = (
    ("smallDonors", "individual_unitemized_contributions"),
    ("largerDonors", "individual_itemized_contributions"),
    ("committees", "other_committee_contributions"),
    ("party", "party_committee_contributions"),
    ("candidate", "candidate_contribution"),
)

# How far the categories may miss the reported total and still be shown as
# its parts: a dollar, for rounding in the FEC's own figures. Live on
# 2026-10-14, 8 of 2,101 candidates' 2026 totals rows missed by more (a
# filer whose categories double-count, or are blank beside a total); those
# are said not to add up, never shown as shares of a total they don't make.
RECONCILE_TOLERANCE = 1.0

# Blocks in the page's composition strip (each one 5% of contributions).
FUNDING_BLOCKS = 20


def apportion(values: list[float], total_units: int) -> list[int]:
    """Whole units for each value, in proportion, adding up to
    `total_units` exactly (largest remainder, Hamilton's method). Ties in
    the remainder go to the earlier value, so the result is deterministic."""
    whole = sum(values)
    if whole <= 0:
        return [0] * len(values)
    exact = [v / whole * total_units for v in values]
    units = [int(x) for x in exact]
    order = sorted(range(len(values)), key=lambda i: (-(exact[i] - units[i]), i))
    for i in order[: total_units - sum(units)]:
        units[i] += 1
    return units


def funding_sources(cand: Candidate) -> dict | None:
    """Where a candidate's contributions came from, or None when the FEC
    figures haven't been read (never synced, no report for this election,
    or a sync from before these columns existed).

    `adds_up` False: the filing's categories don't make its total, so no
    share is given — the amounts are still sent, as filed."""
    total = cand.contributions
    amounts = [getattr(cand, column) for _, column in FUNDING_CATEGORIES]
    if total is None or total <= 0 or any(a is None for a in amounts):
        return None
    adds_up = all(a >= 0 for a in amounts) and abs(sum(amounts) - total) <= RECONCILE_TOLERANCE
    # Each share in the words every other surface uses (shown_share), so a
    # share reads the same here as on a profile for the same figures. Only
    # the blocks are apportioned, since they must fill the strip exactly.
    blocks = apportion(amounts, FUNDING_BLOCKS) if adds_up else [None] * len(amounts)
    parts = [
        {
            "key": key,
            "amount": amount,
            "share": round(amount / total, 4) if adds_up else None,
            "shown": shown_share(amount / total) if adds_up else None,
            "blocks": block,
        }
        for (key, _), amount, block in zip(FUNDING_CATEGORIES, amounts, blocks)
    ]
    return {"addsUp": adds_up, "parts": parts}


def lean_context(
    office: str, pvi: int | None, pvi_level: str | None,
    state_pvi: dict[str, int], district_pvi: dict[str, int],
) -> dict | None:
    """Where this seat's lean sits among every seat of its kind: House
    districts on the lines this election is fought on, or the 50 states for
    a Senate race. None when the race has no lean, or a House race shows
    its state's number in place of a district's (a statewide stand-in can't
    be ranked among districts).

    `moreDemocratic` / `moreRepublican` count the other seats leaning
    further each way; `same` those with the identical figure. `distribution`
    is every seat's lean against the nation, one entry per figure, most
    Democratic first (with its label, written as the map writes it), and
    `seatLean` this seat's figure in it: the page draws them on one axis.
    `marks` are the axis's readouts, one per figure: how many seats have it
    and which ones."""
    if pvi is None:
        return None
    if office == "S":
        states = federal_states()
        table, unit = {k: v for k, v in state_pvi.items() if k in states}, "states"
    elif pvi_level == "district":
        table, unit = district_pvi, "districts"
    else:
        return None
    values = list(table.values())
    if not values:
        return None
    return {
        "unit": unit,
        "of": len(values),
        "moreDemocratic": sum(v < pvi for v in values),
        "moreRepublican": sum(v > pvi for v in values),
        # Less one: the seat itself is in the table.
        "same": max(sum(v == pvi for v in values) - 1, 0),
        "seatLean": pvi,
        "distribution": [
            {"lean": v, "label": lean_label(v), "count": n} for v, n in sorted(Counter(values).items())
        ],
        "marks": _lean_marks(table, unit, pvi),
    }


# Seats a lean column's readout names before it says "and N more".
_NAMED_SEATS = 6


def _seat_name(key: str) -> str:
    """A district key as the site writes it ("CA-12", "WY-AL"); a state as is."""
    state, _, number = key.partition("-")
    return f"{state}-{number if number != '0' else 'AL'}" if number else state


def _lean_marks(table: dict[str, int], unit: str, pvi: int) -> list[dict]:
    by_lean: dict[int, list[str]] = {}
    for key, v in table.items():
        by_lean.setdefault(v, []).append(_seat_name(key))
    one = unit[:-1]
    out = []
    for v, seats in sorted(by_lean.items()):
        seats.sort()
        more = len(seats) - _NAMED_SEATS
        names = ", ".join(seats[:_NAMED_SEATS]) + (f" and {more} more" if more > 0 else "")
        context = f"this seat's figure: {names}" if v == pvi else names
        out.append(mark(str(v), lean_label(v), f"{len(seats)} {one if len(seats) == 1 else unit}",
                        context=context))
    return out


# The state CSV's columns, in order. Every figure as filed with the FEC, and
# the dates that say what it is as of.
CSV_COLUMNS = (
    "race_id", "office", "state", "district", "is_special", "lean_pvi", "lean_level",
    "candidate_id", "name", "ballot_name", "party", "incumbent_challenge", "confirmed_general",
    "contributions", "cash_on_hand", "disbursements",
    *(column for _, column in FUNDING_CATEGORIES),
    "candidate_loans", "financials_through", "last_financials_sync",
    "fec_url",
)


def fec_candidate_url(candidate_id: str, cycle: int) -> str:
    """The candidate's FEC page for this election's figures."""
    return f"https://www.fec.gov/data/candidate/{candidate_id}/?cycle={cycle}&election_full=true"


def races_csv(rows: list[tuple]) -> str:
    """One CSV row per candidate. `rows` is (race, candidate, pvi, level)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for race, cand, pvi, level in rows:
        writer.writerow([
            race.id, race.office, race.state, "" if race.district is None else race.district,
            int(bool(race.is_special)), "" if pvi is None else pvi, level or "",
            cand.id, cand.name, cand.ballot_name or "", cand.ballot_party or cand.party,
            cand.incumbent_challenge or "", int(bool(cand.confirmed_general)),
            *("" if v is None else v for v in (
                cand.contributions, cand.cash_on_hand, cand.disbursements,
                *(getattr(cand, column) for _, column in FUNDING_CATEGORIES),
                cand.candidate_loans,
            )),
            cand.financials_through or "",
            cand.last_financials_sync.isoformat() + "Z" if cand.last_financials_sync else "",
            fec_candidate_url(cand.id, race.cycle_year) if cand.fec_filed else "",
        ])
    return out.getvalue()
