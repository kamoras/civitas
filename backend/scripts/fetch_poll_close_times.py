"""Fetch each state's poll-closing time for a general election.

Regenerates app/data/poll_close_times.json, read by
app/pipeline/fetch/poll_close.py: the live-results sync stores nothing
for a state — no count, no "flip", no post — until its LAST polls have
closed. Florida makes releasing results before a county's polls close a
felony (Fla. Stat. 104.21) and Nevada a misdemeanor (NRS 293.3606), and
AP does not call a race before a split-zone state's last close; the page
follows the strictest reading everywhere.

Source: Ballotpedia's "State Poll Opening and Closing Times ({year})",
which cites each state's own statute per row. Its hours are LOCAL, and a
state spanning two time zones closes in each at local time, so the last
close is the latest of those instants on the election date itself (a
zone keeping daylight time read as it is that night), unless the row
names the zone a time belongs to (Nebraska's does). A state whose hours
"vary by municipality/county" gets null, and the loader holds it until the latest
close of any state — the error the gate is allowed to make is waiting
too long, never too short.

STATE_ZONES is geography, not a calculation: which IANA zones each state
spans, per the U.S. time zone boundaries (49 CFR Part 71), east to west.

Run from the repo (network required):
    python3 backend/scripts/fetch_poll_close_times.py [year] [output.json]

Or, after a change to how a close is derived, with no network — every
state's close and zone recomputed from the hours text the file stores:
    python3 backend/scripts/fetch_poll_close_times.py --rederive [file.json]

Exits 1 if any state's row is missing.
"""

import datetime as dt
import html
import json
import pathlib
import re
import sys
import urllib.request

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

ET, CT, MT, MST, PT = "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles"
STATE_ZONES = {
    "AL": [ET, CT], "AK": ["America/Anchorage", "America/Adak"], "AZ": [MST, MT], "AR": [CT],
    "CA": [PT], "CO": [MT], "CT": [ET], "DE": [ET], "FL": [ET, CT], "GA": [ET], "HI": ["Pacific/Honolulu"],
    "ID": [MT, PT], "IL": [CT], "IN": [ET, CT], "IA": [CT], "KS": [CT, MT], "KY": [ET, CT], "LA": [CT],
    "ME": [ET], "MD": [ET], "MA": [ET], "MI": [ET, CT], "MN": [CT], "MS": [CT], "MO": [CT], "MT": [MT],
    "NE": [CT, MT], "NV": [MT, PT], "NH": [ET], "NJ": [ET], "NM": [MT], "NY": [ET], "NC": [ET],
    "ND": [CT, MT], "OH": [ET], "OK": [CT], "OR": [MT, PT], "PA": [ET], "RI": [ET], "SC": [ET],
    "SD": [CT, MT], "TN": [ET, CT], "TX": [CT, MT], "UT": [MT], "VT": [ET], "VA": [ET], "WA": [PT],
    "WV": [ET], "WI": [CT], "WY": [MT],
}
_ZONE_WORDS = {"eastern": ET, "central": CT, "mountain": MT, "pacific": PT}

# Ballotpedia answers a bare non-browser User-Agent with an empty 202;
# the "compatible;" form still names Civitas and a contact address.
UA = {"User-Agent": "Mozilla/5.0 (compatible; Civitas/1.0; poll-closing times; +mack.ryanm@gmail.com)"}
SOURCE_URL = "https://ballotpedia.org/State_Poll_Opening_and_Closing_Times_({year})"
DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "poll_close_times.json"

_PM_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*p\.m\.", re.IGNORECASE)


def _rows(page: str) -> dict[str, str]:
    """{state name: polling-hours cell} from the 'Polling hours by state' table."""
    start = page.find("Polling hours by state")
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", page[start:], re.S):
        cells = [
            html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        ]
        if len(cells) >= 2 and cells[0]:
            out[cells[0]] = re.sub(r"\[\d+\]", "", cells[1]).strip()
    return out


def election_day(year: int) -> dt.date:
    """The Tuesday after the first Monday in November (2 U.S.C. 7)."""
    nov1 = dt.date(year, 11, 1)
    return nov1 + dt.timedelta(days=(0 - nov1.weekday()) % 7 + 1)


def last_close(hours: str, zones: list[str], on: dt.date) -> tuple[str, str] | None:
    """(HH:MM, IANA zone) of the latest closing time the hours text states,
    or None when it states none ("Varies by municipality").

    A time with no zone named holds at local time in every zone the state
    spans, so its close is the latest of those instants ON `on`, the
    election date itself — not a fixed "westernmost" zone. Zones that tie
    on that date are told apart by midsummer, when daylight time is in
    force everywhere that keeps it: Arizona's Phoenix (no DST) and Denver
    tie on a November date after DST ends, but Phoenix is the later one on
    any date it hasn't (election day 2032 is November 2), and the loader
    evaluates the stored zone on whatever date it gates. A zone never
    earlier than another on any date is the one stored."""
    from zoneinfo import ZoneInfo

    midsummer = dt.date(on.year, 7, 1)
    best = None
    for segment in hours.split(";"):
        times = _PM_RE.findall(segment)
        if not times:
            continue
        hour, minute = times[-1]  # "7 a.m. to 7 p.m.": the closing time is the last p.m.
        clock = dt.time(int(hour) + 12, int(minute or 0))
        word = next((z for w, z in _ZONE_WORDS.items() if w in segment.lower()), None)
        for zone in [word] if word else zones:
            key = (dt.datetime.combine(on, clock, ZoneInfo(zone)), dt.datetime.combine(midsummer, clock, ZoneInfo(zone)))
            if best is None or key > best[0]:
                best = (key, f"{clock.hour:02d}:{clock.minute:02d}", zone)
    return (best[1], best[2]) if best else None


def rederive(path: pathlib.Path) -> int:
    """Recompute every state's close and zone from the `hours` text an
    earlier fetch stored, with no network: for a change to last_close."""
    data = json.loads(path.read_text())
    on = election_day(data["year"])
    for code, entry in data["states"].items():
        close = last_close(entry["hours"], STATE_ZONES[code], on)
        entry["close"] = close[0] if close else None
        entry["zone"] = close[1] if close else STATE_ZONES[code][-1]
    data["_source"] = _source_note(data["_source"].split(" (polling hours")[0], data["_source"])
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"re-derived {len(data['states'])} states in {path}")
    return 0


def _source_note(url: str, previous: str | None = None) -> str:
    fetched = (re.search(r"fetched (\d{4}-\d{2}-\d{2})", previous or "") or [None, dt.date.today().isoformat()])[1]
    note = (
        f"{url} (polling hours per state statute, all times local), fetched {fetched} "
        "by backend/scripts/fetch_poll_close_times.py. Close is the latest closing time the row states: "
        "in the zone it names, or else the latest instant across the state's zones (49 CFR Part 71) "
        "on election day, ties broken by midsummer; null = varies."
    )
    if previous is not None:
        note += f" Closes and zones re-derived from the stored hours on {dt.date.today().isoformat()} (--rederive)."
    return note


def main() -> int:
    if sys.argv[1:2] == ["--rederive"]:
        return rederive(pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT)
    year = int(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today().year
    output = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    url = SOURCE_URL.format(year=year)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    if not page:
        print(f"{url} answered with an empty body (status {resp.status})", file=sys.stderr)
        return 1
    rows = _rows(page)

    # The zones as they are on election night itself: DST can still be in
    # force (election day 2032 is November 2, before it ends).
    reference = election_day(year)
    states, missing = {}, []
    for code, name in STATE_NAMES.items():
        hours = rows.get(name)
        if hours is None:
            missing.append(name)
            continue
        close = last_close(hours, STATE_ZONES[code], reference)
        states[code] = {
            "close": close[0] if close else None,
            "zone": close[1] if close else STATE_ZONES[code][-1],
            "hours": hours,
        }
    if missing:
        print(f"missing rows: {missing}", file=sys.stderr)
        return 1
    output.write_text(json.dumps({
        "_source": _source_note(url),
        "year": year,
        "states": states,
    }, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(states)} states to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
