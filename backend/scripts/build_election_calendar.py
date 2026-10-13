"""Build each state's voter calendar for a general election.

Regenerates app/data/election_calendar.json, read by app/voter_calendar.py
into the state ballot page and the homepage's "this week" strip: when
registration closes (by each method, and same-day registration where a
state has it), the early in-person voting window, and the mail-ballot
request and return deadlines.

Input: scripts/election_calendar_sources.json, one entry per state (and
D.C.) with each rule as the state states it, the date it gives or the
"N days before the election" it is counted from, and the page that says so
-- the state election authority's own page, or its statute where the
authority's site cannot be read. Never an aggregator (vote.org, NCSL...).

This script does not decide any date. It computes the "N days before"
ones (keeping the rule's text beside the date, so both are shown), checks
any date the source also states against that count (a deadline a state
moves off a weekend or holiday must say so in `adjustment`), and re-reads
every cited page to confirm the quoted passage is still on it:

  - "verified": the quote is on the election authority's own page;
  - "statute": the quote is in the statute's official text;
  - "unverified": the page could not be read (a bot wall, which is never
    worked around -- AGENTS.md section 7 -- or an outage) or no longer
    carries the passage. The value is still shown, and says so.

Run from the repo (network required), after editing the sources file:
    PYTHONPATH=backend python3 backend/scripts/build_election_calendar.py \
        [--cache DIR] [--text URL]

--text URL prints the normalised text a quote is matched against (for
copying a quote out of a page exactly). --cache DIR keeps fetched pages
between runs. Exits 1 on a malformed entry.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import sys
import unicodedata

import httpx
import lxml.html
import pdfplumber

from app.http_client import is_bot_challenge
from app.pipeline.fetch.http_utils import BROWSER_HEADERS

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = pathlib.Path(__file__).resolve().parent / "election_calendar_sources.json"
OUTPUT = ROOT / "app" / "data" / "election_calendar.json"

KINDS = {"registration", "same_day_registration", "early_voting", "absentee_request", "ballot_return"}
METHODS = {"online", "mail", "in_person", "any"}
BASES = {"postmarked", "received", "submitted"}
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                         "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u00ad": ""})


def normalise(text: str) -> str:
    """What a quote is matched against: NFKC, straight quotes and hyphens,
    one space between words, lower case -- so a quote survives a page's
    re-flow and typography but not a change of wording."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip().lower()


def _page_text(content: bytes, content_type: str) -> str:
    if content[:5] == b"%PDF-" or "pdf" in content_type:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    doc = lxml.html.fromstring(content)
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    return doc.text_content()


def fetch(url: str, client: httpx.Client, cache: pathlib.Path | None) -> tuple[str | None, str]:
    """(normalised text, how the read went). A bot wall's challenge is
    reported as such and never retried another way."""
    key = cache / hashlib.sha256(url.encode()).hexdigest() if cache else None
    if key and key.exists():
        return key.read_text(), "ok (cached)"
    try:
        r = client.get(url, headers=BROWSER_HEADERS, follow_redirects=True, timeout=30)
    except httpx.HTTPError as exc:
        return None, f"fetch failed: {type(exc).__name__}"
    if is_bot_challenge(r):
        return None, f"blocked by a bot wall ({r.status_code})"
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    text = normalise(_page_text(r.content, r.headers.get("content-type", "")))
    if key:
        key.write_text(text)
    return text, "ok"


def _date(value: str | None, where: str) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        sys.exit(f"{where}: bad date {value!r}")


def resolve(item: dict, election: dt.date, where: str) -> dict:
    """The item with its date computed from `daysBefore` (when it has one)
    and checked against any date the source states."""
    if item.get("kind") not in KINDS:
        sys.exit(f"{where}: unknown kind {item.get('kind')!r}")
    if item.get("method") and item["method"] not in METHODS:
        sys.exit(f"{where}: unknown method {item['method']!r}")
    if item.get("basis") and item["basis"] not in BASES:
        sys.exit(f"{where}: unknown basis {item['basis']!r}")
    for field in ("rule", "source"):
        if not item.get(field):
            sys.exit(f"{where}: missing {field}")
    src = item["source"]
    if src.get("type") not in ("official", "statute") or not src.get("url"):
        sys.exit(f"{where}: source needs type official|statute and url")
    # No quote only where the cited page could not be read at all (a bot
    # wall): the item is then unverified, and says so.
    out = dict(item)
    stated = _date(item.get("date"), where)
    if item.get("daysBefore") is not None:
        counted = election - dt.timedelta(days=int(item["daysBefore"]))
        if stated and stated != counted and not item.get("adjustment"):
            sys.exit(f"{where}: source date {stated} is not {item['daysBefore']} days before "
                     f"{election} ({counted}); say why in `adjustment`")
        out["date"] = (stated or counted).isoformat()
        out["countedDate"] = counted.isoformat()
    elif not stated:
        sys.exit(f"{where}: needs a date or daysBefore")
    for field in ("date", "endDate"):
        d = _date(out.get(field), where)
        if d and not (election - dt.timedelta(days=120) <= d <= election + dt.timedelta(days=30)):
            sys.exit(f"{where}: {field} {d} is not near the {election} election")
    for field in ("time", "endTime"):
        if out.get(field) and not re.fullmatch(r"\d\d:\d\d", out[field]):
            sys.exit(f"{where}: {field} must be HH:MM (24h, local)")
    return out


def build(sources: dict, client: httpx.Client, cache: pathlib.Path | None) -> dict:
    election = dt.date.fromisoformat(sources["electionDate"])
    today = dt.date.today().isoformat()
    pages: dict[str, tuple[str | None, str]] = {}
    states = {}
    for code, entry in sorted(sources["states"].items()):
        if not entry.get("timeZone"):
            sys.exit(f"{code}: missing timeZone")
        items = []
        for i, raw in enumerate(entry.get("items") or []):
            item = resolve(raw, election, f"{code}[{i}]")
            src = item["source"]
            if src["url"] not in pages:
                pages[src["url"]] = fetch(src["url"], client, cache)
            text, how = pages[src["url"]]
            found = text is not None and bool(src.get("quote")) and normalise(src["quote"]) in text
            status = ("verified" if src["type"] == "official" else "statute") if found else "unverified"
            item["verification"] = {
                "status": status,
                "checkedOn": today,
                "detail": "quote found on the page" if found else (
                    how if text is None else
                    "page read, quote not found" if src.get("quote") else "page read, no quote recorded"),
            }
            items.append(item)
        states[code] = {"timeZone": entry["timeZone"], "items": items}
        if entry.get("note"):
            states[code]["note"] = entry["note"]
    return {
        "_source": (
            "Each state's (and D.C.'s) election authority's own pages, or its statute where that "
            f"authority's site could not be read; cited per item. Built {today} by "
            "backend/scripts/build_election_calendar.py from scripts/election_calendar_sources.json, "
            "which re-read every cited page and checked its quoted passage is on it "
            "(verification.status: verified = on the authority's page, statute = in the statute's "
            "official text, unverified = the page could not be read or no longer says it)."
        ),
        "_use": (
            "Read by app/voter_calendar.py. Dates are local to timeZone (a state spanning two "
            "zones uses the one where a deadline passes last). date: the deadline, or an early "
            "voting window's first day (endDate its last); for ballot_return with basis "
            "'postmarked', date is the postmark deadline and endDate the day it must arrive by. "
            "time/endTime: the hour on that day, when the source states one, else the day's end. "
            "countedDate: the 'N days before' count, kept beside a date the state moved."
        ),
        "electionDate": election.isoformat(),
        "states": states,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=pathlib.Path)
    ap.add_argument("--text", metavar="URL")
    args = ap.parse_args()
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client() as client:
        if args.text:
            text, how = fetch(args.text, client, args.cache)
            print(text if text is not None else how)
            return
        result = build(json.loads(SOURCES.read_text()), client, args.cache)
    OUTPUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    counts: dict[str, int] = {}
    for entry in result["states"].values():
        for item in entry["items"]:
            s = item["verification"]["status"]
            counts[s] = counts.get(s, 0) + 1
    print(f"Wrote {OUTPUT} ({len(result['states'])} states): {counts}")


if __name__ == "__main__":
    main()
