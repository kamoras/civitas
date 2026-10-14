"""What changed in each state that redrew its U.S. House map for the
coming election: where each new district's residents lived before, where
each old district's residents went, and how the lean moved.

The counts are app/data/redistricting_overlap.json
(scripts/build_redistricting_overlap.py: Census block population joined
to the 119th- and 120th-Congress block equivalency files). The lean is
joined here, at read time, from the pinned per-Congress Cook PVI tables
(district_pvi_for_congress), so a pin advanced in district_pvi_sources.json
reaches these numbers without a rebuild. Every figure the pages show is
computed here; the pages only word it.

Descriptive only: which residents are in which district, and the lean
Cook published for each. Nothing here says why a line was drawn.
"""

from __future__ import annotations

import json
import logging
import pathlib
from collections import Counter, defaultdict

from app.live_results.sync import _maps
from app.pipeline.fetch.district_pvi import district_pvi_for_congress

logger = logging.getLogger(__name__)

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "redistricting_overlap.json"
_cache: dict | None = None


def overlap_file() -> dict:
    """The bundled overlap file, or {} (never a guess) when unreadable."""
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PATH.read_text())
        except Exception:
            logger.exception("redistricting_overlap.json unavailable")
            _cache = {}
    return _cache


def redrawn_overlap_states() -> list[str]:
    return sorted((overlap_file().get("states") or {}).keys())


def number_of(key: str) -> int:
    """"TX-5" -> 5 (an at-large seat is "ST-0")."""
    return int(key.rsplit("-", 1)[1])


def share_pct(part: int, whole: int) -> int:
    """A whole-number percentage that never reads as all or nothing when
    it isn't: 99.6% of a district is 99, not 100, and 0.4% is 1, not 0."""
    if whole <= 0 or part <= 0:
        return 0
    if part >= whole:
        return 100
    return min(99, max(1, round(100 * part / whole)))


def lean_category(pvi: int | None) -> str | None:
    """The PVI's own three-way reading: its sign. No band is invented."""
    if pvi is None:
        return None
    return "EVEN" if pvi == 0 else ("R" if pvi > 0 else "D")


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def map_source(state: str, cycle: str) -> dict | None:
    """The enacting act or order, as redrawn_congressional_maps.json
    records it (reason, and its official source when listed)."""
    entry = (_maps().get(cycle) or {}).get(state)
    if not entry:
        return None
    src = entry.get("source") or {}
    return {
        "reason": entry.get("reason"),
        "title": src.get("title"),
        "issuedBy": src.get("issuedBy"),
        "url": src.get("url"),
    }


def state_changes(state: str) -> dict | None:
    """Everything the redistricting pages show for one state, or None when
    the state did not redraw for the file's cycle."""
    doc = overlap_file()
    cells = (doc.get("states") or {}).get(state)
    if not cells:
        return None
    floor = int(doc["minResidents"])
    old_pvi, old_meta = district_pvi_for_congress(int(doc["fromCongress"]))
    new_pvi, new_meta = district_pvi_for_congress(int(doc["toCongress"]))

    new_pop: Counter = Counter()
    old_pop: Counter = Counter()
    into: dict[str, list[tuple[str, int]]] = defaultdict(list)
    out_of: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for old, new, pop in cells:
        new_pop[new] += pop
        old_pop[old] += pop
        into[new].append((old, pop))
        out_of[old].append((new, pop))

    def listed(pairs: list[tuple[str, int]], whole: int) -> tuple[list[dict], int]:
        """Overlaps of at least `floor` residents, largest first, and the
        residents of the smaller ones (each within about one block)."""
        kept = sorted((p for p in pairs if p[1] >= floor), key=lambda p: (-p[1], number_of(p[0])))
        rows = [
            {"district": k, "number": number_of(k), "residents": n, "sharePct": share_pct(n, whole)}
            for k, n in kept
        ]
        return rows, sum(n for _, n in pairs if n < floor)

    # A new district's predecessor: the old district most of its residents
    # (or the largest group of them) came from.
    predecessor = {new: max(pairs, key=lambda p: (p[1], -number_of(p[0])))[0] for new, pairs in into.items()}

    new_rows = []
    for new in sorted(into, key=number_of):
        sources, sliver = listed(into[new], new_pop[new])
        before = old_pvi.get(predecessor[new])
        after = new_pvi.get(new)
        weighted = None
        if all(o in old_pvi for o, _ in into[new]):
            weighted = round(sum(old_pvi[o] * n for o, n in into[new]) / new_pop[new], 1)
        main_old = predecessor[new]
        main_residents = dict(into[new])[main_old]
        new_rows.append({
            "district": new,
            "number": number_of(new),
            "population": new_pop[new],
            "sources": sources,
            "sliverResidents": sliver,
            "predecessor": main_old,
            "predecessorNumber": number_of(main_old),
            "predecessorSharePct": share_pct(main_residents, new_pop[new]),
            "pviBefore": before,
            "pviBeforeWeighted": weighted,
            "pviAfter": after,
            "pviShift": after - before if after is not None and before is not None else None,
            "leanBefore": lean_category(before),
            "leanAfter": lean_category(after),
            # Same people, same district: every resident came from one old
            # district, and every resident of that one came here.
            "unchanged": len(into[new]) == 1 and len(out_of[main_old]) == 1,
        })

    old_rows = []
    for old in sorted(out_of, key=number_of):
        dests, sliver = listed(out_of[old], old_pop[old])
        old_rows.append({
            "district": old,
            "number": number_of(old),
            "population": old_pop[old],
            "destinations": dests,
            "sliverResidents": sliver,
            "pvi": old_pvi.get(old),
            # Whether any new district grew mainly from this one; a
            # district split up so that none did has no successor.
            "hasSuccessor": old in predecessor.values(),
        })

    total = sum(new_pop.values())
    # Residents whose new district grew from a different old one.
    moved = sum(pop for old, new, pop in cells if predecessor[new] != old)
    shifts = [(abs(new_pvi[n] - old_pvi[o]), pop) for o, n, pop in cells if n in new_pvi and o in old_pvi]
    shift_pop = sum(p for _, p in shifts)
    before_counts = Counter(lean_category(old_pvi.get(o)) for o in out_of)
    after_counts = Counter(r["leanAfter"] for r in new_rows)

    return {
        "state": state,
        "cycle": doc["cycle"],
        "fromCongress": doc["fromCongress"],
        "toCongress": doc["toCongress"],
        "minResidents": floor,
        "map": map_source(state, doc["cycle"]),
        "summary": {
            "seats": len(new_rows),
            "population": total,
            "unchangedSeats": sum(r["unchanged"] for r in new_rows),
            "oldWithoutSuccessor": sum(not r["hasSuccessor"] for r in old_rows),
            "residentsMoved": moved,
            "residentsMovedPct": share_pct(moved, total),
            # Seats by lean category (the PVI's sign) on each map, and the
            # net change: what "how many seats changed" means to a reader,
            # and the figure the pages lead with.
            "seatsByLeanBefore": {k: before_counts.get(k, 0) for k in ("R", "EVEN", "D")},
            "seatsByLeanAfter": {k: after_counts.get(k, 0) for k in ("R", "EVEN", "D")},
            "netSeatChangeByLean": {
                k: after_counts.get(k, 0) - before_counts.get(k, 0) for k in ("R", "EVEN", "D")
            },
            # A different question, for the expert layer: new districts whose
            # category differs from their main predecessor's. It need not
            # equal the net change: a district built from several old ones
            # is compared with its largest source only, and an old district
            # split with no successor is compared with nothing.
            "districtsLeaningDifferentlyFromPredecessor": sum(
                1 for r in new_rows
                if r["leanBefore"] and r["leanAfter"] and r["leanBefore"] != r["leanAfter"]
            ),
            "meanPviBefore": _mean([old_pvi[o] for o in out_of if o in old_pvi]),
            "meanPviAfter": _mean([new_pvi[n] for n in into if n in new_pvi]),
            # How far the average resident's district lean moved, either
            # direction: the residents of each overlap, weighted.
            "meanResidentShift": round(sum(s * p for s, p in shifts) / shift_pop, 1) if shift_pop else None,
        },
        "newDistricts": new_rows,
        "oldDistricts": old_rows,
        # The whole matrix, exact and unfloored: [old, new, residents].
        "cells": sorted(cells, key=lambda c: (number_of(c[0]), number_of(c[1]))),
        "sources": {
            "overlap": doc.get("_source"),
            "pviBefore": old_meta,
            "pviAfter": new_meta,
        },
    }



def new_district_notes(state: str, cycle: int) -> dict[int, dict]:
    """District number -> the short "what changed" figures for a ballot
    page's House race, when `cycle` is the election the new lines are for
    (any other cycle's races are on other lines, and get none)."""
    if str(overlap_file().get("cycle")) != str(cycle):
        return {}
    changes = state_changes(state)
    if changes is None:
        return {}
    return {
        r["number"]: {
            "predecessorNumber": r["predecessorNumber"],
            "predecessorSharePct": r["predecessorSharePct"],
            "sourceCount": len(r["sources"]),
            "pviBefore": r["pviBefore"],
            "pviAfter": r["pviAfter"],
            "unchanged": r["unchanged"],
        }
        for r in changes["newDistricts"]
    }
