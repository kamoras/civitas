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
  - "republished": the item's own source couldn't be read (a wall or an
    outage), and the quote is on a county election office's printing of
    the same deadline (`republished`), labelled as such on the page. The
    own source is tried first on every run.

An item that can't be verified this run never holds back the others
(build() says what happens to it): a passage the state moved is found on
another page of the same host; a deadline verified before, whose page is
now unreadable (a bot wall, which is never worked around -- AGENTS.md
section 7 -- or an outage) or which has passed (states take passed dates
off their pages), keeps that verification with the date it was last
confirmed and why it wasn't now; any other is left out and named as
unconfirmed, so a date a voter acts on is never published from memory. A
state none of whose deadlines can be read from any official source
carries `unread` instead of items, and its page says so and sends the
reader to the state's own lookup and county office.

Run from the repo (network required), after editing the sources file:
    PYTHONPATH=backend python3 backend/scripts/build_election_calendar.py \
        [--cache DIR] [--text URL] [--in-use FILE]

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
from zoneinfo import ZoneInfo

import httpx
import lxml.etree
import lxml.html
import pdfplumber

from app.http_client import is_bot_challenge
from app.pipeline.fetch.http_utils import FETCH_HEADERS

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
        r = client.get(url, headers=FETCH_HEADERS, follow_redirects=True, timeout=30)
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
    if not src.get("quote"):
        sys.exit(f"{where}: source needs the quoted passage that states the rule")
    rep = item.get("republished")
    if rep and not all(rep.get(f) for f in ("publisher", "url", "quote")):
        sys.exit(f"{where}: republished needs publisher, url and quote")
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


_MONTHS = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                     r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b\.?\s*\d", re.IGNORECASE)


def federal_holidays(year: int) -> set[dt.date]:
    """The federal holidays (5 U.S.C. 6103) that fall in a general
    election's window (Labor Day to Thanksgiving), with Veterans Day's
    observed day when Nov 11 is a weekend: the days a state's deadline
    rule may move off."""
    def nth(weekday: int, n: int, month: int) -> dt.date:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))

    veterans = dt.date(year, 11, 11)
    observed = veterans + dt.timedelta(days={5: -1, 6: 1}.get(veterans.weekday(), 0))
    return {nth(0, 1, 9), nth(0, 2, 10), veterans, observed, nth(3, 4, 11)}


def _date_forms(d: dt.date) -> list[str]:
    """How a citation can write `d`, normalised as quotes are (lower case),
    longest first so a full form is replaced before its parts."""
    month, short = d.strftime("%B").lower(), d.strftime("%b").lower()
    day, weekday = str(d.day), d.strftime("%A").lower()
    return [
        f"{weekday}, {month} {day}, {d.year}", f"{month} {day}, {d.year}", f"{short}. {day}, {d.year}",
        f"{short} {day}, {d.year}", f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{weekday}, {month} {day}", f"{month} {day}", f"{short}. {day}",
    ]


def redate_quote(quote: str, moves: dict[dt.date, dt.date], years: tuple[int, int]) -> str:
    """The quote as the state's page would print it for the new election:
    each date it names moved with the rule (same weekday, whole weeks
    later), and the election year after them. build() then re-reads the
    cited page for exactly that text, so an item is carried only where
    the state's own page now says it for the new election."""
    out = normalise(quote)
    for before, after in sorted(moves.items(), reverse=True):
        for old_form, new_form in zip(_date_forms(before), _date_forms(after)):
            out = out.replace(old_form, new_form)
    return re.sub(rf"\b{years[0]}\b", str(years[1]), out)


def carry(sources: dict, election: dt.date) -> tuple[dict, dict[str, list[str]]]:
    """The sources re-dated for another general election, and per state the
    items that could not be carried, with why.

    Every general election is a Tuesday (2 U.S.C. 7), so a rule counted
    from it ("29 days before", "the Friday before", "10 days after") lands
    on the same weekday every cycle: it moves by the whole number of weeks
    between the two elections, and a weekend adjustment a state makes (a
    Sunday deadline moved to Monday) still applies. A citation that prints
    the dates is re-dated the same way (redate_quote) and must be on the
    cited page again in that form (build()): a state's page
    updated for the new election verifies it; a document about one
    election (a year's calendar PDF) does not, and that item is left out.
    Never carried: a rule worded as a calendar date ("October 15"), not a
    count; a date that lands on a federal holiday it did not land on before
    (Columbus Day and Veterans Day move against election day), where a
    state's own rule may move it. A state that loses items says so
    (`incomplete`); one that loses all of them is `unread`."""
    old = dt.date.fromisoformat(sources["electionDate"])
    shift = election - old
    if shift.days % 7:
        raise ValueError(f"{election} is not a Tuesday election day {shift.days} days from {old}")
    holidays = federal_holidays(old.year) | federal_holidays(election.year)
    out = {**sources, "electionDate": election.isoformat(), "states": {}}
    dropped: dict[str, list[str]] = {}
    for code, entry in sources["states"].items():
        items, lost = [], []
        for item in entry.get("items") or []:
            why = None
            if _MONTHS.search(item.get("rule", "")):
                why = "its rule is a calendar date, not a count from election day"
            moved = {k: v for k, v in item.items() if k != "adjustment"}
            moves = {old: election}
            for field in ("date", "endDate"):
                if item.get(field):
                    before = dt.date.fromisoformat(item[field])
                    after = before + shift
                    if after in holidays and before not in holidays:
                        why = why or f"{field} would fall on a federal holiday ({after})"
                    moved[field] = after.isoformat()
                    moves[before] = after
            if item.get("daysBefore") is not None:
                counted = old - dt.timedelta(days=int(item["daysBefore"]))
                moves.setdefault(counted, counted + shift)
            moved["source"] = {**item["source"], "quote": redate_quote(item["source"]["quote"], moves, (old.year, election.year))}
            if item.get("republished"):
                moved["republished"] = {**item["republished"], "quote": redate_quote(
                    item["republished"]["quote"], moves, (old.year, election.year))}
            if why:
                lost.append(f"{item['kind']}/{item.get('method', '')}: {why}")
            else:
                items.append(moved)
        new = {k: v for k, v in entry.items() if k != "items"}
        if items:
            new["items"] = items
            if lost:
                new["incomplete"] = True
        elif entry.get("items"):
            new["unread"] = "None of its deadlines could be carried to this election."
        out["states"][code] = new
        if lost:
            dropped[code] = lost
    return out, dropped


def official_host(host: str, cited: set[str]) -> bool:
    """A government host: .gov, a state.xx.us name, or the domain of a host
    the state's own deadline citations already use (D.C.'s dcboe.org)."""
    host = (host or "").lower()
    domain = ".".join(host.split(".")[-2:])
    return (host.endswith(".gov") or re.search(r"\.state\.[a-z]{2}\.us$", host) is not None
            or domain in {".".join(h.split(".")[-2:]) for h in cited})


def check_register(entry: dict, cited: set[str], client: httpx.Client, cache: pathlib.Path | None,
                   today: str) -> tuple[dict | None, str | None]:
    """(what the page may link, why it may not). The register page must
    answer on an official host, before and after redirects, and read as a
    registration page; one behind a bot wall is kept, marked unconfirmed
    (a wall is never worked around, and the page then says it couldn't
    check), never one that answers 404 or lands elsewhere: that one is
    left out, named on stderr, and the page links vote.gov. A `none` entry
    (a state with no registration) is held to its quote like a deadline."""
    url = entry.get("url") or ""
    out = {"url": url, "publisher": entry.get("publisher"), "checkedOn": today}
    if not official_host(httpx.URL(url).host, cited):
        return None, f"{url} is not on an official government host"
    if entry.get("none"):
        text, how = fetch(url, client, cache)
        if text is None or not entry.get("quote") or normalise(entry["quote"]) not in text:
            return None, f"no-registration quote not verified ({how if text is None else 'quote not on the page'}, {url})"
        return {**out, "none": True, "status": "verified"}, None
    r = None
    for _ in range(2):  # once more after a refusal or a dropped connection, which come and go
        try:
            r = client.get(url, headers=FETCH_HEADERS, follow_redirects=True, timeout=30)
        except httpx.HTTPError as exc:
            r, failed = None, type(exc).__name__
            continue
        if r.status_code < 400 or is_bot_challenge(r):
            break
    if r is None:
        return None, f"{url}: fetch failed ({failed})"
    if is_bot_challenge(r):
        return {**out, "status": "blocked"}, None
    if r.status_code >= 400:
        return None, f"{url}: HTTP {r.status_code}"
    if not official_host(r.url.host, cited):
        return None, f"{url} redirects off an official host, to {r.url}"
    if "regist" not in str(r.url).lower() and "regist" not in r.text.lower():
        return None, f"{url} does not read as a registration page"
    return {**out, "status": "verified"}, None


# How many pages of the cited host a moved citation is looked for on: the
# links of the cited page and of the host's front page, in document order.
# A state authority's front page and a deadlines page link a few dozen
# pages each; the reads are shared across a state's items in one run.
MOVED_SEARCH_PAGES = 40
_DATE_FIELDS = ("date", "endDate", "time", "endTime")


def _links(url: str, client: httpx.Client) -> list[str]:
    """The same-host links of the page at `url`, in document order."""
    try:
        r = client.get(url, headers=FETCH_HEADERS, follow_redirects=True, timeout=30)
    except httpx.HTTPError:
        return []
    if r.status_code >= 400 or is_bot_challenge(r) or "html" not in r.headers.get("content-type", ""):
        return []
    host = httpx.URL(url).host
    try:
        doc = lxml.html.fromstring(r.content, base_url=str(r.url))
    except (ValueError, lxml.etree.ParserError):
        return []
    doc.make_links_absolute(str(r.url))
    out = []
    for href in doc.xpath("//a/@href"):
        link = href.split("#")[0]
        if link.startswith("https://") and httpx.URL(link).host == host and link not in out:
            out.append(link)
    return out


def find_moved(src: dict, client: httpx.Client, cache: pathlib.Path | None,
               pages: dict[str, tuple[str | None, str]]) -> str | None:
    """The page of the cited host that now carries the quoted passage, when
    the cited page answers without it: the state moved the passage (a
    rebuilt site, a page renamed for the election). Looked for among the
    links of the cited page and of the host's front page, never off the
    cited host, and verified as any citation is: the exact quote on the
    page. None when no such page holds it."""
    url = httpx.URL(src["url"])
    quote = normalise(src["quote"])
    candidates = []
    for start in (str(url), f"{url.scheme}://{url.host}/"):
        candidates += [link for link in _links(start, client) if link not in candidates and link != str(url)]
    for link in candidates[:MOVED_SEARCH_PAGES]:
        if link not in pages:
            pages[link] = fetch(link, client, cache)
        text, _ = pages[link]
        if text is not None and quote in text:
            return link
    return None


def _key(item: dict) -> tuple:
    return item["kind"], item.get("method") or "", item.get("basis") or "", item["rule"]


def _passed(item: dict, local_today: dt.date) -> bool:
    return dt.date.fromisoformat(item.get("endDate") or item["date"]) < local_today


def build(sources: dict, client: httpx.Client, cache: pathlib.Path | None, in_use: dict | None = None,
          now: dt.datetime | None = None) -> dict:
    """Each state's items, every citation re-read. An item that can't be
    verified this run never stops the others, and never keeps a date the
    page has stopped stating for a deadline still ahead:

      - its quote is on another page of the cited host (find_moved): the
        item is verified there, its source updated (`movedFrom`);
      - the cited page couldn't be read (a bot wall, an outage) or the
        deadline has passed (states take a passed date off their pages),
        and `in_use` (the calendar in use, for the same election) holds the
        item as verified with the same dates: that verification is kept,
        with this run's read beside it (`recheck`), and the page says when
        it was last confirmed and why it wasn't now;
      - otherwise (a deadline still ahead whose page answered without it, or
        one never verified): left out, and listed in the state's
        `unconfirmed` with why, so the page names the deadlines it couldn't
        confirm instead of reading as if there were none. A state left with
        no deadline at all is `unread`.

    A carried calendar (carry()) is built the same way; only a malformed
    sources entry stops the build."""
    election = dt.date.fromisoformat(sources["electionDate"])
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    pages: dict[str, tuple[str | None, str]] = {}
    prior = {}
    if in_use and in_use.get("electionDate") == election.isoformat():
        for code, entry in (in_use.get("states") or {}).items():
            for item in entry.get("items") or []:
                if item.get("verification", {}).get("status") in ("verified", "statute", "republished"):
                    prior[(code, *_key(item))] = item
    states = {}
    for code, entry in sorted(sources["states"].items()):
        if not entry.get("timeZone"):
            sys.exit(f"{code}: missing timeZone")
        if bool(entry.get("items")) == bool(entry.get("unread")):
            sys.exit(f"{code}: give items, or `unread` (why no official source could be read), not both")
        local_today = now.astimezone(ZoneInfo(entry["timeZone"])).date()
        items, unconfirmed = [], []
        for i, raw in enumerate(entry.get("items") or []):
            item = resolve(raw, election, f"{code}[{i}]")
            src = item["source"]
            if src["url"] not in pages:
                pages[src["url"]] = fetch(src["url"], client, cache)
            text, how = pages[src["url"]]
            rep = item.get("republished")
            republished = None
            if text is None and rep:
                # The item's own source can't be read (a wall or an outage,
                # never a page that answered without the passage): the same
                # deadline as a county election office prints it, labelled
                # on the page. Read again every run, so the state's own
                # source is used again the run it answers.
                if rep["url"] not in pages:
                    pages[rep["url"]] = fetch(rep["url"], client, cache)
                rtext, rhow = pages[rep["url"]]
                if rtext is not None and normalise(rep["quote"]) in rtext:
                    republished = {"publisher": rep["publisher"], "stateSource": src["url"], "stateSourceRead": how}
                    print(f"{code}[{i}] {item['kind']}: {src['url']} unread ({how}); as published by "
                          f"{rep['publisher']}", file=sys.stderr)
                    text, src = rtext, {"type": "official", **rep}
                    item["source"] = src
            item.pop("republished", None)
            moved_from = None
            last = prior.get((code, *_key(item)))
            same_dates = last is not None and all(last.get(f) == item.get(f) for f in _DATE_FIELDS)
            passed = _passed(item, local_today)
            if text is not None and normalise(src["quote"]) not in text and not republished:
                moved = find_moved(src, client, cache, pages)
                if moved:
                    moved_from, src = src["url"], {**src, "url": moved}
                    item["source"] = src
                    text, how = pages[moved]
                    print(f"{code}[{i}] {item['kind']}: the quote moved from {moved_from} to {moved}", file=sys.stderr)
            if text is None or normalise(src["quote"]) not in text:
                why = how if text is None else "quote not on the page"
                if same_dates and (text is None or passed):
                    kept = {**last, "verification": {**last["verification"], "recheck": {
                        "on": today, "result": why, "passed": passed}}}
                    print(f"{code}[{i}] {item['kind']}: {why} ({src['url']}); kept as verified on "
                          f"{last['verification'].get('checkedOn')}", file=sys.stderr)
                    items.append(kept)
                    continue
                print(f"{code}[{i}] {item['kind']}: not verified ({why}, {src['url']}); listed as unconfirmed",
                      file=sys.stderr)
                unconfirmed.append({"kind": item["kind"], "method": item.get("method"), "basis": item.get("basis"),
                                    "source": {"publisher": src.get("publisher"), "url": src["url"]},
                                    "why": why, "checkedOn": today})
                continue
            item["verification"] = {
                "status": "republished" if republished else "verified" if src["type"] == "official" else "statute",
                "checkedOn": today,
                **({"republished": republished} if republished else {}),
                **({"movedFrom": moved_from} if moved_from else {}),
            }
            items.append(item)
        if not items and entry.get("items"):
            entry = {**entry, "unread": "None of its deadlines could be confirmed for this election."}
        states[code] = {"timeZone": entry["timeZone"], "items": items}
        if unconfirmed:
            states[code]["unconfirmed"] = unconfirmed
        if entry.get("register"):
            cited = {httpx.URL(i["source"]["url"]).host for i in entry.get("items") or []}
            reg, why = check_register(entry["register"], cited, client, cache, today)
            if why:
                # Never linked, but not a reason to hold back the deadlines:
                # the page links vote.gov instead. Named, so it gets fixed.
                print(f"{code} register: not linked ({why}); vote.gov instead", file=sys.stderr)
            else:
                states[code]["register"] = reg
        if entry.get("unread"):
            states[code]["unread"] = True
        if entry.get("note"):
            states[code]["note"] = entry["note"]
        if entry.get("incomplete") or unconfirmed:
            states[code]["incomplete"] = True
    fallback = None
    if sources.get("registerFallback"):
        fallback, why = check_register(sources["registerFallback"], set(), client, cache, today)
        if why:
            print(f"registerFallback: not linked ({why})", file=sys.stderr)
    return {
        "_source": (
            "Each state's (and D.C.'s) election authority's own pages, or its statute where that "
            f"authority's site could not be read; cited per item. Built {today} by "
            "backend/scripts/build_election_calendar.py from scripts/election_calendar_sources.json, "
            "which re-read every cited page and checked its quoted passage is on it "
            "(verification.status: verified = on the authority's page, statute = in the statute's "
            "official text; verification.recheck: this build couldn't re-read it, because the page was "
            "unreadable or the deadline has passed, and checkedOn is when it was last confirmed). A "
            "deadline still ahead that could not be verified is never written: it is listed under the "
            "state's unconfirmed, with why. A state with unread: true has no deadline read from any "
            "official source, and lists none."
        ),
        "_use": (
            "Read by app/voter_calendar.py. Dates are local to timeZone (a state spanning two "
            "zones uses the one where a deadline passes last). date: the deadline, or an early "
            "voting window's first day (endDate its last); for ballot_return with basis "
            "'postmarked', date is the postmark deadline and endDate the day it must arrive by. "
            "time/endTime: the hour on that day, when the source states one, else the day's end. "
            "countedDate: the 'N days before' count, kept beside a date the state moved. "
            "register: the state's page for registering to vote (status verified, or blocked: behind a "
            "bot wall, so unconfirmed; none: true where the state has no registration, quoted); "
            "registerFallback: vote.gov, for a state without one."
        ),
        "electionDate": election.isoformat(),
        "builtOn": today,
        "registerFallback": fallback,
        "states": states,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=pathlib.Path)
    ap.add_argument("--text", metavar="URL")
    ap.add_argument("--election", type=dt.date.fromisoformat,
                    help="build for this general election, carrying each rule from the sources' (see carry())")
    ap.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    ap.add_argument("--in-use", type=pathlib.Path,
                    help="the calendar in use: a deadline it verified is kept while its page can't be read or once "
                         "it has passed (see build())")
    args = ap.parse_args()
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client() as client:
        if args.text:
            text, how = fetch(args.text, client, args.cache)
            print(text if text is not None else how)
            return
        sources = json.loads(SOURCES.read_text())
        if args.election and args.election.isoformat() != sources["electionDate"]:
            sources, dropped = carry(sources, args.election)
            for code, lost in sorted(dropped.items()):
                print(f"{code}: not carried: {'; '.join(lost)}", file=sys.stderr)
        in_use = json.loads(args.in_use.read_text()) if args.in_use and args.in_use.exists() else None
        result = build(sources, client, args.cache, in_use=in_use)
    args.output.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    counts: dict[str, int] = {}
    for entry in result["states"].values():
        for item in entry["items"]:
            s = item["verification"]["status"] + (" (kept)" if item["verification"].get("recheck") else "")
            counts[s] = counts.get(s, 0) + 1
        if entry.get("unconfirmed"):
            counts["unconfirmed"] = counts.get("unconfirmed", 0) + len(entry["unconfirmed"])
    print(f"Wrote {args.output} ({len(result['states'])} states): {counts}")


if __name__ == "__main__":
    main()
