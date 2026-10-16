"""The historical polls the map's fits train on: Silver Bulletin's raw polls.

Silver Bulletin (Nate Silver's newsletter) publishes the poll archive
behind its pollster ratings, every rated poll since 1998 with the
certified result beside it, on one page that it updates each winter after
a cycle's results are rated. Its terms: "You are welcome to use this data
free of charge for any purpose, but please attribute it to Silver
Bulletin." The map, its method note, /about/data and every file built
from it say so.

app/data/forecast_polls.json is the copy in use (scripts/
fetch_silver_bulletin_polls.py writes it; app/data_refresh.py refreshes
it and installs a new copy only when `gate` passes). It keeps the rows
the fits read (Senate and House generals, and the generic ballot, which
the file files as a House general in "US") and every column they use.

A refused file keeps the last accepted copy and its fits, and the alert
names the gate. A page or file that can't be fetched is an outage
(the script exits non-zero), never a file with no polls.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.election_calendar import next_election_day

PAGE_URL = "https://www.natesilver.net/p/pollster-ratings-silver-bulletin"
ATTACHMENT_LABEL = "Rawpolls"
ATTRIBUTION = "Silver Bulletin"
FILE_NAME = "forecast_polls.json"
# The two halves of the terms sentence that must still be on the page: no
# attribution-only grant, nothing ingested (a licence gate).
TERMS = ("free of charge for any purpose", "attribute it to Silver Bulletin")
SOURCE = ("Polls: Silver Bulletin, pollster-ratings raw polls (natesilver.net/p/pollster-ratings-silver-bulletin), "
          "used free of charge as its terms allow, attributed to Silver Bulletin as they ask.")

# The file's header, exactly. Any other set of columns is a changed file
# whose meaning has to be read again before anything is fitted on it.
EXPECTED_COLUMNS = (
    "poll_id_nate", "question_id_538", "race", "year", "location", "type_simple", "type_detail", "pollster",
    "pollster_rating_id", "partisan", "polldate", "samplesize", "cand1_name", "cand1_party", "cand1_pct",
    "cand2_name", "cand2_party", "cand2_pct", "cand3_pct", "margin_poll", "electiondate", "cand1_actual",
    "cand2_actual", "margin_actual", "error", "bias", "rightcall", "advancedplusminus", "comment",
)
# What forecast_polls.json keeps of each row, in this order (dates as
# ISO strings, everything else as the file prints it).
ROW_COLUMNS = (
    "poll_id_nate", "race", "location", "type_detail", "pollster", "partisan", "polldate", "samplesize",
    "cand1_party", "cand1_pct", "cand2_party", "cand2_pct", "cand3_pct", "margin_poll", "electiondate",
    "cand1_actual", "cand2_actual",
)
# The race types the fits read: Senate generals (regular and special) and
# House generals, whose "US" rows are the generic congressional ballot.
KEPT_TYPES = ("Sen-G", "Sen-GS", "House-G")
SENATE_TYPES = ("Sen-G", "Sen-GS")
GENERIC_LOCATION = "US"

# Tolerances, each a stated design choice:
# - two shares, each printed to the nearest point or half point, can add
#   to just over 100 (the file's own maximum is 101.0);
SHARE_SUM_MAX = 101.0
# - margin_poll is cand1_pct - cand2_pct as the file rounds it;
MARGIN_ROUNDING = 0.051
# - Silver Bulletin's stated result and MEDSL's certified count, both as
#   the Republican's two-party margin: 386 of the 390 Senate races
#   1998-2024 the two share agree within half a point, and the other four
#   are each off by 0.8 points or more (a runoff filed as the general, a
#   misprinted result), so half a point separates rounding from error;
RESULT_TOLERANCE = 0.5
# - a re-rated file may correct earlier polls: up to this share of the rows
#   already accepted may change (each correction is logged by id), none may
#   disappear, and more than this is a different file, not a correction.
CORRECTIONS_MAX_SHARE = 0.005

EXCEL_EPOCH = date(1899, 12, 30)


class FetchFailed(Exception):
    """The page or its attachment couldn't be read: an outage, never "no polls"."""


def excel_date(cell: str) -> str:
    """A spreadsheet date serial ("45601") as an ISO date."""
    return (EXCEL_EPOCH + timedelta(days=int(float(cell)))).isoformat()


def _page_text(html: str) -> str:
    """Substack ships the post twice (rendered, and escaped in its preload
    JSON); one unescaped string reads both the same way."""
    return html.replace("\\u003c", "<").replace("\\u003e", ">").replace('\\"', '"')


def terms_sentence(html: str) -> str | None:
    """The sentence on the page that grants use of the data, when it still
    carries both halves of the terms; None otherwise."""
    text = re.sub(r"<[^>]+>", " ", _page_text(html))
    for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)):
        if all(t in sentence for t in TERMS):
            return sentence.strip()
    return None


def attachment(html: str) -> tuple[str, str] | None:
    """(label, url) of the page's raw-polls attachment: the file-embed whose
    title starts with ATTACHMENT_LABEL. None when the page has none."""
    found = re.search(
        r'file-embed-details-h1">\s*(%s[^<]*)<.*?href="(https://www\.natesilver\.net/api/v1/file/[0-9a-f-]+\.xlsx)"'
        % re.escape(ATTACHMENT_LABEL), _page_text(html), re.S)
    return (found.group(1).strip(), found.group(2)) if found else None


def kept_rows(header: list[str], rows: list[dict]) -> list[list[str]]:
    """The rows the fits read, as ROW_COLUMNS lists, dates made ISO."""
    out = []
    for r in rows:
        if r.get("type_detail") not in KEPT_TYPES:
            continue
        row = []
        for c in ROW_COLUMNS:
            v = (r.get(c) or "").strip()
            row.append(excel_date(v) if c in ("polldate", "electiondate") and v else v)
        out.append(row)
    return sorted(out, key=lambda x: int(x[0]))


def as_dicts(doc: dict) -> list[dict]:
    cols = doc.get("rowColumns") or []
    return [dict(zip(cols, row)) for row in doc.get("rows") or []]


def days_out(row: dict) -> int:
    return (date.fromisoformat(row["electiondate"]) - date.fromisoformat(row["polldate"])).days


def window_days(doc: dict) -> int | None:
    """How far before election day this file's polls reach: its oldest
    poll. None for a file with no rows."""
    rows = as_dicts(doc)
    return max(days_out(r) for r in rows) if rows else None


def cycle_of(row: dict) -> int | None:
    """The federal general a row is about (its election date's year), or
    None for anything else (an odd year, a runoff or special in another
    month)."""
    day = date.fromisoformat(row["electiondate"])
    general = next_election_day(date(day.year, 1, 1))
    return day.year if day == general else None


def stated_margin(row: dict) -> float | None:
    """The result Silver Bulletin prints beside a Democrat-versus-Republican
    poll, as the Republican's two-party margin in points; None otherwise."""
    try:
        actual = {row["cand1_party"]: float(row["cand1_actual"]), row["cand2_party"]: float(row["cand2_actual"])}
    except (KeyError, ValueError):
        return None
    if set(actual) != {"DEM", "REP"} or actual["DEM"] + actual["REP"] <= 0:
        return None
    return 100 * (actual["REP"] - actual["DEM"]) / (actual["REP"] + actual["DEM"])


def senate_key(row: dict) -> str:
    """"2024-PA" / "2024-OK-special": the key of `certified`."""
    return f"{row['electiondate'][:4]}-{row['location']}{'-special' if row['type_detail'] == 'Sen-GS' else ''}"


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------

def _shares(rows: list[dict]) -> list[str]:
    bad = []
    for r in rows:
        try:
            p1, p2 = float(r["cand1_pct"]), float(r["cand2_pct"])
            p3 = float(r["cand3_pct"]) if r["cand3_pct"] else 0.0
            margin = float(r["margin_poll"])
        except (KeyError, ValueError):
            bad.append(f"poll {r.get('poll_id_nate')}: a share is not a number")
            continue
        if not all(0 <= x <= 100 for x in (p1, p2, p3)):
            bad.append(f"poll {r['poll_id_nate']}: a share outside 0-100")
        elif p1 + p2 > SHARE_SUM_MAX:
            bad.append(f"poll {r['poll_id_nate']}: the two shares add to {p1 + p2:g}")
        elif abs(p1 - p2 - margin) > MARGIN_ROUNDING:
            bad.append(f"poll {r['poll_id_nate']}: margin_poll {margin:g} is not {p1:g} - {p2:g}")
    return bad


def gate(new: dict, old: dict) -> list[str]:
    """Every reason to refuse `new` against `old`, the copy in use, each
    named by its gate. Empty: install it."""
    failures: list[str] = []
    if not new.get("terms") or not all(t in new["terms"] for t in TERMS):
        failures.append("terms: the page no longer says the data may be used free of charge for any purpose "
                        "with attribution to Silver Bulletin")
    columns = new.get("columns") or []
    if list(columns) != list(EXPECTED_COLUMNS):
        added = sorted(set(columns) - set(EXPECTED_COLUMNS))
        dropped = sorted(set(EXPECTED_COLUMNS) - set(columns))
        failures.append(f"columns: not the expected set (added {added or 'none'}, dropped {dropped or 'none'}"
                        f"{', reordered' if not added and not dropped else ''})")
    if list(new.get("rowColumns") or []) != list(ROW_COLUMNS):
        failures.append("columns: the kept columns are not ROW_COLUMNS")
        return failures
    rows = as_dicts(new)
    if not rows:
        return failures + ["rows: the file holds no Senate or House general polls"]
    before = as_dicts(old)
    by_id = {r["poll_id_nate"]: r for r in rows}
    if len(by_id) != len(rows):
        failures.append("append-only: a poll id appears twice")

    # Append-only: every accepted poll still there, unchanged but for a
    # bounded number of corrections.
    missing = [r["poll_id_nate"] for r in before if r["poll_id_nate"] not in by_id]
    fields = ("polldate", "electiondate", "cand1_pct", "cand2_pct", "location", "type_detail")
    changed = [r["poll_id_nate"] for r in before if r["poll_id_nate"] in by_id
               and any(by_id[r["poll_id_nate"]][f] != r[f] for f in fields)]
    if missing:
        failures.append(f"append-only: {len(missing)} accepted polls are gone (ids {', '.join(missing[:10])})")
    if before and len(changed) > CORRECTIONS_MAX_SHARE * len(before):
        failures.append(f"append-only: {len(changed)} accepted polls changed, more than "
                        f"{CORRECTIONS_MAX_SHARE:.1%} of {len(before)} (ids {', '.join(changed[:10])})")

    # The window: a poll's election day is 0 to N days after its fieldwork,
    # N the reach of the polls already accepted (the span the fits were
    # checked over); the first file sets it.
    reach = window_days(old) if before else None
    try:
        span = [(r["poll_id_nate"], days_out(r)) for r in rows]
    except (KeyError, ValueError):
        return failures + ["window: a poll or election date is unreadable"]
    early = [pid for pid, d in span if d < 0]
    late = [pid for pid, d in span if reach is not None and d > reach]
    if early:
        failures.append(f"window: {len(early)} polls end after their election (ids {', '.join(early[:10])})")
    if late:
        failures.append(f"window: {len(late)} polls end more than {reach} days before their election, the "
                        f"accepted file's reach (ids {', '.join(late[:10])})")

    failures += [f"shares: {b}" for b in _shares(rows)[:10]]

    # A new cycle has both halves the fits need.
    def cycles(rs):
        return {c for c in (cycle_of(r) for r in rs) if c is not None}

    for year in sorted(cycles(rows) - cycles(before)):
        in_year = [r for r in rows if cycle_of(r) == year]
        if not any(r["type_detail"] in SENATE_TYPES for r in in_year):
            failures.append(f"new cycle: {year} has no Senate general polls")
        if not any(r["type_detail"] == "House-G" and r["location"] == GENERIC_LOCATION for r in in_year):
            failures.append(f"new cycle: {year} has no generic-ballot polls")

    # The stated results of the polls not yet accepted, against MEDSL's
    # certified counts (the fits' results come from MEDSL, never from here).
    accepted = {r["poll_id_nate"] for r in before}
    certified = new.get("certified") or {}
    disagree = {}
    for r in rows:
        if r["poll_id_nate"] in accepted or r["type_detail"] not in SENATE_TYPES or cycle_of(r) is None:
            continue
        stated, key = stated_margin(r), senate_key(r)
        if stated is None or key not in certified:
            continue  # not a Democrat-versus-Republican race in either source: never fitted
        if abs(stated - certified[key]) > RESULT_TOLERANCE:
            disagree[key] = (stated, certified[key])
    for key, (stated, cert) in sorted(disagree.items()):
        failures.append(f"results: {key} stated R{stated:+.2f}, MEDSL certified R{cert:+.2f}")
    return failures
