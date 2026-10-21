"""State totals from the U.S. Election Assistance Commission's Election
Administration and Voting Survey (EAVS), for the election administration
view (app/election_administration.py, GET /api/elections/administration).

Writes app/data/eavs.json (AGENTS.md 3a: generated, never hand-typed).

Source: the EAC's public release of the EAVS, the survey every state and
local election office answers after each federal general (52 U.S.C.
§20508 and §20981 direct the EAC to collect it), as the "nolabel" CSV the
EAC posts on its datasets page. A U.S. government work, public domain. The
newest year and version on that page are read, never a typed URL, so a new
survey (or a corrected version of one) is picked up by rerunning this.

One row per reporting jurisdiction (a county, or a town in New England).
Negative values are the survey's own codes for "not applicable", "data not
available" and the like, never counts, so a state's total for an item sums
only the jurisdictions that reported a count for it, and the file records
how many did (`reporting`, of `jurisdictions`). A rate the view shows is
computed only over a state where every jurisdiction reported both items.

Run from backend/ (network required), optionally naming the output file
(app/data_refresh.py runs it monthly that way, into a candidate it gates):

    python -m scripts.fetch_eavs [OUT]
"""

import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.contact import BOT_USER_AGENT

DATASETS_URL = "https://www.eac.gov/research-and-data/datasets-codebooks-and-surveys"
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "eavs.json"

# The items the view shows: the survey's own codes and its codebook's names.
ITEMS = {
    "A1a": "Total registered voters",
    "F1a": "Total voters who participated",
    "F1b": "Voted in person on election day",
    "F1f": "Voted in person before election day",
    "F1d": "Voted by mail",
    "C1a": "Mail ballots sent to voters",
    "C1b": "Mail ballots returned by voters",
    "C8a": "Mail ballots counted",
    "C9a": "Mail ballots rejected",
    "E1a": "Provisional ballots cast",
    "E1d": "Provisional ballots rejected",
}

_RELEASE = re.compile(r'href="([^"]*?(\d{4})_EAVS_for_Public_Release_nolabel_V(\d+)[^"]*?csv\.zip)"', re.I)


def newest_release(html: str, base: str) -> tuple[int, int, str]:
    """(year, version, url) of the newest survey's CSV the page links."""
    found = [(int(y), int(v), urljoin(base, href)) for href, y, v in _RELEASE.findall(html)]
    if not found:
        raise SystemExit("No EAVS CSV release linked on the EAC's datasets page: has it moved?")
    return max(found)


def _count(raw: str) -> int | None:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def aggregate(rows: list[dict]) -> dict:
    """{state: {"jurisdictions": n, "items": {code: {"value", "reporting"}}}}."""
    states: dict[str, dict] = {}
    for row in rows:
        st = (row.get("State_Abbr") or "").strip().upper()
        if len(st) != 2:
            continue
        entry = states.setdefault(st, {"jurisdictions": 0, "items": {k: {"value": 0, "reporting": 0}
                                                                     for k in ITEMS}})
        entry["jurisdictions"] += 1
        for code in ITEMS:
            value = _count(row.get(code))
            if value is not None:
                entry["items"][code]["value"] += value
                entry["items"][code]["reporting"] += 1
    return dict(sorted(states.items()))


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    with httpx.Client(headers={"User-Agent": BOT_USER_AGENT}, follow_redirects=True, timeout=120) as client:
        page = client.get(DATASETS_URL)
        page.raise_for_status()
        year, version, url = newest_release(page.text, str(page.url))
        archive = client.get(url)
        archive.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(name).decode("latin-1")
    rows = list(csv.DictReader(io.StringIO(text)))
    states = aggregate(rows)
    # Gates: every state and DC, and the release's own item codes present.
    missing = [code for code in ITEMS if code not in (rows[0] if rows else {})]
    if missing or len([s for s in states if s != "DC"]) < 50:
        raise SystemExit(f"EAVS {year} release looks wrong: {len(states)} jurisdictions' states, missing {missing}")
    out.write_text(json.dumps({
        "_source": (f"U.S. Election Assistance Commission, {year} Election Administration and Voting Survey, "
                    f"public release version {version} ({url}), summed by state from its jurisdiction rows; "
                    f"negative survey codes are left out of each sum and counted as not reporting. "
                    f"Generated by backend/scripts/fetch_eavs.py."),
        "year": year,
        "version": version,
        "url": url,
        "items": ITEMS,
        "states": states,
    }, indent=1) + "\n")
    print(f"wrote {out} ({year} v{version}, {len(states)} states, {len(rows)} jurisdictions)")


if __name__ == "__main__":
    main()
