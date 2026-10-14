"""What changed on the new U.S. House maps: the browsable index, one page
per redrawn state, and the overlap matrix as CSV for researchers.

Every number comes from app.redistricting; this adds what a reader finds
their district by — the counties and places on each map (AGENTS.md §8:
nobody is asked where they live; they filter by a place they know).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.politicians import counties_on_lines_of
from app.api.response_helpers import CACHE_TTL_REFERENCE_S, cached_json
from app.config import settings
from app.district_places import places_on_lines_of
from app.live_results.sync import _maps
from app.redistricting import overlap_file, redrawn_overlap_states, state_changes
from app.state_names import STATE_NAMES

router = APIRouter(prefix="/elections/redistricting")


@router.get("")
def redistricting_index():
    """Each state voting on new House lines this cycle, with its summary,
    and each state whose new map is not in force (listed with the reason)."""
    doc = overlap_file()
    cycle = doc.get("cycle")
    states = []
    for st in redrawn_overlap_states():
        changes = state_changes(st)
        states.append({
            "state": st,
            "stateName": STATE_NAMES.get(st, st),
            "map": changes["map"],
            "summary": changes["summary"],
        })
    redrawn = set(redrawn_overlap_states())
    held = [
        {"state": st, "stateName": STATE_NAMES.get(st, st), "reason": entry.get("reason"),
         "url": (entry.get("source") or {}).get("url")}
        for st, entry in sorted(((_maps().get(cycle) or {}) if cycle else {}).items())
        if st not in redrawn
    ]
    return cached_json(
        {
            "cycle": cycle,
            "fromCongress": doc.get("fromCongress"),
            "toCongress": doc.get("toCongress"),
            "states": states,
            "heldMaps": held,
        },
        max_age=CACHE_TTL_REFERENCE_S,
    )


def _with_areas(rows: list[dict], congress: int) -> None:
    counties = counties_on_lines_of(congress) or {}
    places = places_on_lines_of(congress) or {}
    for row in rows:
        row["counties"] = counties.get(row["district"], [])
        row["places"] = places.get(row["district"], [])


@router.get("/{state}")
def redistricting_state(state: str):
    """One redrawn state: every new district's sources and lean, every old
    district's destinations, the state summary, the enacting act or
    order, and the sources of every figure."""
    st = state.upper()
    changes = state_changes(st)
    if changes is None:
        raise HTTPException(status_code=404, detail="No new House map for this state")
    _with_areas(changes["newDistricts"], changes["toCongress"])
    _with_areas(changes["oldDistricts"], changes["fromCongress"])
    changes["stateName"] = STATE_NAMES.get(st, st)
    # Members now sitting were elected on the old lines: a profile may say
    # where their district's residents went. From the day the new Congress
    # sits, its members hold the new districts and that note would be wrong.
    changes["fromCongressSitting"] = settings.CURRENT_CONGRESS == changes["fromCongress"]
    return cached_json(changes, max_age=CACHE_TTL_REFERENCE_S)


@router.get("/{state}/old/{number}")
def redistricting_old_seat(state: str, number: int):
    """Where one old district's residents went — the note on the profile
    of the member elected there. `seat` is null for a district whose state
    kept its lines (most profiles ask, so it is an answer, not a 404)."""
    st = state.upper()
    changes = state_changes(st)
    key = f"{st}-{number}"
    seat = next((r for r in (changes or {}).get("oldDistricts", []) if r["district"] == key), None)
    return cached_json(
        {
            "seat": seat,
            "state": st,
            "stateName": STATE_NAMES.get(st, st),
            "cycle": (changes or {}).get("cycle"),
            "fromCongressSitting": bool(changes) and settings.CURRENT_CONGRESS == changes["fromCongress"],
        },
        max_age=CACHE_TTL_REFERENCE_S,
    )


@router.get("/{state}/overlap.csv")
def redistricting_csv(state: str):
    """The overlap matrix: one row per (old district, new district) pair
    sharing 2020 Census residents, exact counts, both leans."""
    st = state.upper()
    changes = state_changes(st)
    if changes is None:
        raise HTTPException(status_code=404, detail="No new House map for this state")
    # A plain CSV, no comment lines (they trip most readers); the sources
    # of every column are in the JSON this page's method section links.
    return Response(
        content=_csv_rows(changes),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": f"public, max-age={CACHE_TTL_REFERENCE_S}, stale-while-revalidate={CACHE_TTL_REFERENCE_S}",
            "Content-Disposition": f'attachment; filename="{st.lower()}-{changes["cycle"]}-district-overlap.csv"',
        },
    )


def _csv_rows(changes: dict) -> str:
    """Every overlap, nothing floored, from the same rows the page shows
    plus the slivers the page folds away."""
    from_c, to_c = changes["fromCongress"], changes["toCongress"]
    old_pvi = {r["district"]: r["pvi"] for r in changes["oldDistricts"]}
    new_pvi = {r["district"]: r["pviAfter"] for r in changes["newDistricts"]}
    lines = [f"old_district_{from_c},new_district_{to_c},residents_2020,share_of_new_pct,share_of_old_pct,old_pvi,new_pvi"]
    new_pop = {r["district"]: r["population"] for r in changes["newDistricts"]}
    old_pop = {r["district"]: r["population"] for r in changes["oldDistricts"]}
    for old, new, pop in changes["cells"]:
        lines.append(",".join(str(v) for v in (
            old, new, pop,
            round(100 * pop / new_pop[new], 2), round(100 * pop / old_pop[old], 2),
            "" if old_pvi.get(old) is None else old_pvi[old],
            "" if new_pvi.get(new) is None else new_pvi[new],
        )))
    return "\n".join(lines) + "\n"
