"""Write redrawn_congressional_maps.json with each election's maps derived
from the Census Bureau's block equivalency files.

The bundled app/data/redrawn_congressional_maps.json is the researched
record of each cycle it lists (the enacting act or order, its official
source). A cycle it doesn't list yet is derived here, by rule, from the
Census Bureau's Redistricting Data Office, which publishes one block
equivalency file (BEF) per Congress: every 2020 block and the district it
lies in on that Congress's lines, with a file of its own for each state
that has a new plan. A state's map changes for an election exactly when
its blocks' districts in the BEF of the Congress that election seats differ
from those of the map it voted on last time.

Measured 2026-10-22 against the two cycles researched by hand: the 119th
Congress's BEF differs from the 118th's in exactly AL, GA, LA, NY and NC
(2024's redraws), and the 120th's (re-released 2026-10-07, after the
Supreme Court's 2026-09-25 order kept Missouri on its 2022 map) from the
119th's in exactly AL, CA, FL, LA, NC, OH, TN, TX and UT, the bundled 2026
entry's states.

Where the files are, by rule: mapping-files/<year the Congress convenes>/
<N>-congressional-district-bef(s)/cd<N>.zip (the 113th to the 120th all sit
there); when not under that year, every year directory is searched.

Gates (any failure prints why and writes nothing):
  * both files hold the same blocks (a new census's blocks are a decennial
    redistricting, not a mid-decade redraw this rule can compare);
  * every state whose blocks changed has its own file in the zip (the
    Census Bureau files one per state with a new plan);
  * the bundled cycles are written unchanged.

A cycle whose Congress has no BEF published yet gets no entry: it stays
unconfirmed (readers carry nothing into it), and `_pending` says what was
looked for. The Census Bureau published the 119th's file on 2024-07-29 and
the 120th's by 2026-08-31, so an election's entry appears in its summer.

Usage: derive_redrawn_maps.py OUT --cycle YEAR [--cache DIR]
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.ordinals import ordinal  # noqa: E402

BUNDLED = BACKEND / "app" / "data" / "redrawn_congressional_maps.json"
RDO = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files"
STATES_URL = "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"
_LISTING_RE = re.compile(r'<a href="([^"?/][^"]*)">[^<]*</a></td><td align="right">(\d{4}-\d\d-\d\d \d\d:\d\d)')


def seated_by(cycle: int) -> int:
    """The Congress a November election seats (2026 -> 120th)."""
    return (cycle - 1788) // 2 + 1


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def listing(url: str) -> dict[str, str]:
    """{entry name: modified} of an Apache directory listing."""
    return dict(_LISTING_RE.findall(_get(url).decode("utf-8", "replace")))


def find_bef(congress: int) -> tuple[str, str] | None:
    """(URL, modified) of the Census BEF zip for `congress`, or None."""
    want = re.compile(rf"^{congress}-congressional-district-befs?/$")
    years = [str(1789 + 2 * (congress - 1))] + sorted(
        (y.rstrip("/") for y in listing(RDO + "/") if re.fullmatch(r"\d{4}/", y)), reverse=True)
    for year in dict.fromkeys(years):
        try:
            entries = listing(f"{RDO}/{year}/")
        except Exception:
            continue
        for sub in entries:
            if not want.match(sub):
                continue
            files = listing(f"{RDO}/{year}/{sub}")
            for name, modified in files.items():
                if name.lower() == f"cd{congress}.zip":
                    return f"{RDO}/{year}/{sub}{name}", modified
    return None


def _fetch(url: str, cache: Path) -> Path:
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(_get(url))
        tmp.replace(path)
    return path


def state_digests(zip_path: Path) -> tuple[dict[str, int], dict[str, int], set[str], dict[str, str]]:
    """(state FIPS -> order-independent digest of its block assignments,
    state FIPS -> blocks, FIPS of the states with a file of their own, FIPS ->
    that file's date) from one BEF zip's national file."""
    digests: dict[str, int] = collections.defaultdict(int)
    counts: dict[str, int] = collections.Counter()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        national = next(n for n in names if n.lower().startswith("national"))
        own = {}
        for info in zf.infolist():
            m = re.search(r"(?:^|_)(\d{2})(?:_|\.)", info.filename)
            if m and info.filename != national:
                own[m.group(1)] = dt.date(*info.date_time[:3]).isoformat()
        with zf.open(national) as fh:
            rows = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
            head = next(rows)
            gi, ci = head.index("GEOID"), head.index("CDFP")
            for row in rows:
                geoid, cd = row[gi], row[ci]
                h = int.from_bytes(hashlib.blake2b(f"{geoid},{cd}".encode(), digest_size=8).digest(), "big")
                st = geoid[:2]
                digests[st] = (digests[st] + h) % (1 << 64)
                counts[st] += 1
    return dict(digests), dict(counts), set(own), own


def map_in(cycles: dict, cycle: int, state: str) -> str:
    """As live_results.sync._map_in: the newest listed cycle at or before
    `cycle` naming the state, else the CD119 lines every unlisted state is on."""
    for year in sorted((int(y) for y in cycles), reverse=True):
        if year <= cycle and state in cycles[str(year)]:
            return cycles[str(year)][state].get("map", "CD119")
    return "CD119"


def derive(cycle: int, cycles: dict, fips: dict[str, str], cache: Path) -> tuple[dict | None, str]:
    """The cycle's entry (state -> map entry; {} when no state redrew), or
    None with why not."""
    congress = seated_by(cycle)
    found = find_bef(congress)
    if found is None:
        return None, f"no Census block equivalency file for the {ordinal(congress)} Congress under {RDO}/ yet"
    url, modified = found
    new, new_counts, own, own_dates = state_digests(_fetch(url, cache))
    states = sorted({st for c in cycles.values() for st in c} | set(fips.values()))
    in_force = {st: map_in(cycles, cycle - 2, st) for st in states}
    base_digests: dict[str, tuple[dict, dict]] = {}
    for name in sorted(set(in_force.values())):
        k = int(name.removeprefix("CD"))
        found_base = find_bef(k)
        if found_base is None:
            return None, f"no Census block equivalency file for the {ordinal(k)} Congress (the map {name} is in force)"
        d, c, _, _ = state_digests(_fetch(found_base[0], cache))
        base_digests[name] = (d, c)
    entry: dict[str, dict] = {}
    for code_fips, st in sorted(fips.items(), key=lambda kv: kv[1]):
        if code_fips not in new:
            continue
        base, base_counts = base_digests[in_force.get(st, "CD119")]
        if base_counts.get(code_fips) != new_counts.get(code_fips):
            return None, (f"{st}: {new_counts.get(code_fips)} blocks in the {ordinal(congress)} Congress's file, "
                          f"{base_counts.get(code_fips)} in {in_force.get(st)} (a new census's blocks?)")
        if base.get(code_fips) == new[code_fips]:
            continue
        if code_fips not in own:
            return None, f"{st}'s blocks changed but the {ordinal(congress)} Congress's file has none of its own for it"
        entry[st] = {
            "map": f"CD{congress}",
            "reason": (f"The Census Bureau's {ordinal(congress)} Congress block equivalency file assigns {st}'s blocks to "
                       f"new districts (its plan filed {own_dates[code_fips]})"),
            "kind": "census_bef",
            "settled": own_dates[code_fips],
            "source": {
                "title": f"{ordinal(congress)} Congress block equivalency file (cd{congress}.zip, published {modified[:10]})",
                "issuedBy": "U.S. Census Bureau, Redistricting Data Office",
                "url": url,
            },
        }
    return entry, f"{url} (published {modified})"


def fips_states(cache: Path) -> dict[str, str]:
    text = _fetch(STATES_URL, cache).read_text(encoding="utf-8", errors="replace")
    out = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        if row.get("STATE") and row.get("STATEFP"):
            out[row["STATEFP"]] = row["STATE"]
    return out


def build(cycle: int, cache: Path) -> dict:
    doc = json.loads(BUNDLED.read_text())
    cycles = dict(doc["cycles"])
    fips = fips_states(cache)
    derived, pending = {}, {}
    start = max(int(c) for c in cycles) + 2 if cycles else cycle
    for year in range(start, cycle + 1, 2):
        entry, note = derive(year, cycles, fips, cache)
        if entry is None:
            pending[str(year)] = note
            break  # a later cycle compares against this one's maps
        cycles[str(year)] = entry
        derived[str(year)] = note
    doc["cycles"] = cycles
    doc["_derived"] = derived
    doc["_pending"] = pending
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--cycle", type=int, required=True, help="the election the site is on")
    ap.add_argument("--cache", type=Path, required=True)
    args = ap.parse_args(argv)
    doc = build(args.cycle, args.cache)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"derived": doc["_derived"], "pending": doc["_pending"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
