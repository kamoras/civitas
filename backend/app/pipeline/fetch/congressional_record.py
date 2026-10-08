"""Fetch Senate and House floor speeches from the Congressional Record via
the GovInfo API.

The Congressional Record (CREC) is published daily when Congress is in
session, one GovInfo package per day, one granule per Record section.

Data flow, per day:
  1. List the window's packages via the GovInfo collections endpoint,
     keeping only issues dated inside it (the endpoint lists packages
     *modified* since a date, and GovInfo reprocesses old issues: a 1996
     issue once came back in a 60-day window).
  2. Read the package's MODS: every granule with its chamber and the
     members the Record lists as SPEAKING in it. Only granules of the
     chamber with a speaking member are fetched — the rest (prayer,
     communications, bill lists, constitutional authority statements) hold
     no member's words.
  3. Fetch each such granule's HTML and split it, from the Record's own
     layout, into speeches (``parse_granule_speeches``).

What a speech is, and what it is titled, comes from the Record's layout,
not from the granule's title. Inside a granule the Record prints its own
centered headings over each speech ("Healthcare Fraud", "Tribute to ..."),
and a member's turn begins a paragraph with the member's designation
("  Mr. SMITH."). A turn ends at the next member, the next presiding
officer or clerk designation, or the next heading. Every turn used to be
saved under the granule's title, so a member answering a colleague's
tribute was credited with a speech titled after the tribute.
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import timedelta

import httpx

from app.config import settings
from app.pipeline.fetch.congress import congress_of_date
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S, fetch_with_retry, redact_url
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

GOVINFO_API_BASE = "https://api.govinfo.gov"

_rate_limiter = RateLimiter(settings.GOVINFO_RPS)


# ── Internal HTTP helpers ────────────────────────────────────────


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """GET a GovInfo endpoint with rate limiting and retries.

    The credential-bearing URL is built separately and passed via
    `request_url`, so `url` — the value fetch_with_retry logs on every
    request/retry/failure — never carries the API key (see
    http_utils.fetch_with_retry's docstring).
    """
    full_url = str(httpx.URL(url).copy_merge_params({"api_key": settings.DATA_GOV_API_KEY}))
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url,
        request_url=full_url, retry_on_4xx=False, log_label="GovInfo CREC",
    )


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict | None:
    resp = await _fetch(client, url)
    return resp.json() if resp is not None else None


async def _fetch_htm(client: httpx.AsyncClient, url: str) -> str | None:
    """A granule's HTML, or None when it could not be fetched."""
    await _rate_limiter.acquire()
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"
    try:
        resp = await client.get(full_url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code == 200:
            return resp.text
        logger.warning("GovInfo HTM fetch returned %d: %s", resp.status_code, url)
    except Exception as e:
        # This request bypasses fetch_with_retry, so the api_key redaction
        # it does isn't applied here — the exception message can embed
        # full_url, so redact directly.
        logger.warning("GovInfo HTM fetch failed: %s", redact_url(str(e)))
    return None


# ── Package index ────────────────────────────────────────────────


def issue_date(package_id: str) -> str:
    """"CREC-2026-09-24" -> "2026-09-24"."""
    return package_id.removeprefix("CREC-")


async def fetch_crec_packages(client: httpx.AsyncClient, days_back: int = 60) -> list[str] | None:
    """Daily CREC package IDs (e.g. ``CREC-2025-02-20``) issued in the last
    *days_back* days and in the sitting Congress, newest first; None when
    the index could not be read.

    Not cached: a page of the index is one request, and the days it lists
    are each read once (the caller records them), so a fresh listing every
    run costs nothing and shows a new issue the night it is published —
    the 72-hour cache it had held one back for up to three days."""
    since = utcnow() - timedelta(days=days_back)
    first_day = since.strftime("%Y-%m-%d")
    logger.info("Fetching CREC package index (last %d days)...", days_back)

    packages: list[str] = []
    offset = 0
    while True:
        data = await _fetch_json(
            client,
            f"{GOVINFO_API_BASE}/collections/CREC/{since.strftime('%Y-%m-%dT00:00:00Z')}"
            f"?pageSize=100&offset={offset}",
        )
        if data is None:
            return None
        batch = data.get("packages", [])
        for pkg in batch:
            pid = pkg.get("packageId", "")
            # The endpoint filters on last modification, not issue date:
            # a reprocessed 1996 issue is "modified" this month.
            if (pid.startswith("CREC-") and issue_date(pid) >= first_day
                    and congress_of_date(issue_date(pid)) == settings.CURRENT_CONGRESS):
                packages.append(pid)
        if len(batch) < 100:
            break
        offset += 100

    packages = sorted(set(packages), reverse=True)
    logger.info("Found %d CREC daily packages", len(packages))
    return packages


_MODS = "{http://www.loc.gov/mods/v3}"


def speech_granules(mods_xml: str, chamber: str) -> list[tuple[str, str]]:
    """(granule ID, title) of each granule of `chamber` ("SENATE" or
    "HOUSE") in a package's MODS that lists at least one member as
    SPEAKING, in Record order.

    Measured on 14 days of the Record (2026-03 to 2026-09): of their 2,042
    Senate and House granules, 733 held a member's turn, and MODS listed a
    speaking member for 732 of them (the one missed held a single
    1,212-character House statement). Fetching only the 1,015 it lists
    halves the requests: about 73 a day instead of 146."""
    root = ET.fromstring(mods_xml)
    out: list[tuple[str, str]] = []
    for item in root.iter(f"{_MODS}relatedItem"):
        if item.get("type") != "constituent":
            continue
        # A granule's MODS may hold several <extension> elements.
        exts = item.findall(f"{_MODS}extension")
        cls = next((t for e in exts if (t := e.findtext(f"{_MODS}granuleClass"))), "")
        gid = next((t for e in exts if (t := e.findtext(f"{_MODS}accessId"))), "")
        if cls.upper() != chamber or not gid:
            continue
        if any(m.get("role") == "SPEAKING" for e in exts for m in e.iter(f"{_MODS}congMember")):
            out.append((gid, item.findtext(f"{_MODS}titleInfo/{_MODS}title") or ""))
    return out


async def fetch_package_mods(client: httpx.AsyncClient, package_id: str) -> str | None:
    resp = await _fetch(client, f"{GOVINFO_API_BASE}/packages/{package_id}/mods")
    return resp.text if resp is not None else None


# ── Parsing ──────────────────────────────────────────────────────

# A member's designation opening a paragraph: "Mr. SMITH.", "Ms. VAN
# WEST.", "Mr. McDONALD.", "Ms. De La PAZ.", "Mr. O'BRIEN." and,
# where the chamber has two members of a surname, "Mr. SMITH of Florida."
# — up to three leading words of any case, the last word carrying at least
# two capitals in a row (the Record prints surnames in capitals; "Mr.
# President," never matches). Anchored to a paragraph start, so a name
# quoted inside a speech never splits it.
SPEAKER_RE = re.compile(
    r"^  (?:Mr|Mrs|Ms|Miss)\.\s+"
    r"((?:[A-Z][A-Za-z'\-]*\s+){0,3}?[A-Z][A-Za-z'\-]*?[A-Z]{2}[A-Z'\-]*)"
    r"(?:\s+of\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*))?\.(?=\s|$)"
)

# Designations of the chair and the clerks, which end a member's turn and
# whose own text (rulings, roll calls, bill text read by the clerk) is
# nobody's speech: "The PRESIDING OFFICER.", "The PRESIDING OFFICER (Mr.
# X).", "The SPEAKER pro tempore.", "The Acting CHAIR.", "The ACTING
# PRESIDENT pro tempore.", "The Clerk read ...", "The senior assistant
# legislative clerk read as follows:". The Record's own role markers, a
# data-format convention like SPEAKER_RE — not a judgment on content.
OFFICER_RE = re.compile(
    r"^  The (?:(?:[A-Z][a-z]+ )?[A-Z]{2,}[A-Z ]*(?: pro tempore)?(?: \([^)]*\))?\.(?=\s|$)"
    r"|(?:[a-z]+ )*[Cc]lerk\b)"
)

# Officers the Record occasionally misprints with a member's courtesy title
# ("  Mr. SPEAKER. Members are reminded to direct their remarks to the
# Chair.", 2026-06-10): their words are the chair's, not a member's.
_OFFICER_NAMES = frozenset({"SPEAKER", "PRESIDENT", "PRESIDING OFFICER", "CHAIR", "CHAIRMAN", "CHAIRWOMAN", "CLERK"})

# The Record brackets a statement submitted rather than delivered aloud
# with bullets: "<bullet> Mr. RISCH. Madam President, ..." through
# "...success.<bullet>". The opening one stands where a paragraph's
# two-space indent would.
_BULLET = "<bullet>"

# A paragraph addressed to the presiding officer: "  Mr. President, ...",
# "  Madam Speaker, ...", "  Mr. Chairman, ...".
_ADDRESS_RE = re.compile(r"^  (?:Mr\.|Madam) (?:President|Speaker|Chair|Chairman|Chairwoman)\b")

# Lines that are pagination, not text: "[[Page S4960]]", "{time}  1330".
_PAGINATION_RE = re.compile(r"^\s*(?:\[\[Page [^\]]*\]\]|\{time\}.*)\s*$")

# The Record's vote-tally lines are centered like headings ("YEAS--49",
# "NOT VOTING--1", "ANSWERED ``PRESENT''--1"), but title nothing.
_TALLY_RE = re.compile(r"[A-Z `']+--\d+")

# A heading inside a granule is centered on the Record's 72-column line;
# paragraphs start two spaces in, quoted documents (letters, bill text,
# cloture motions) three to seven. Measured over the 1,015 speech granules
# of 14 days of the Record (2026-03 to 2026-09): of 2,774 one-line blocks 8 or
# more spaces in, 2,639 were centered (left margin + text + right margin
# 71 to 73): the Record's headings, vote tallies, and the headings of
# documents printed in it (bill titles, article headlines — which end the
# member's own words, and title nothing a speech opens). The other 135
# were the address and signature lines of letters a member put in the
# Record ("Washington, DC."). Centered blocks 3 to 7 spaces in were
# the granule's own title, when long (matched against its MODS title
# instead), or headings inside quoted bill text ("Subtitle B--...").
_CENTERED_INDENT = 8
_LINE_WIDTH = range(71, 74)


def record_lines(granule_html: str) -> list[str]:
    """A granule's text as printed: the <pre> body, tags removed, entities
    decoded, line breaks and indentation kept (the layout carries the
    structure), pagination lines dropped."""
    m = re.search(r"<pre>(.*)</pre>", granule_html, re.S)
    lines = []
    for ln in (m.group(1) if m else granule_html).split("\n"):
        if ln.startswith(_BULLET + " "):
            ln = "  " + ln[len(_BULLET) + 1:]
        ln = html.unescape(re.sub(r"<[^>]+>", "", ln.replace(_BULLET, ""))).rstrip()
        if not _PAGINATION_RE.match(ln):
            lines.append(ln)
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _heading(block: list[str], granule_title: str) -> str | None:
    """The heading a blank-line-delimited block is, or None."""
    text = _squash(" ".join(block))
    if text.casefold() == granule_title.casefold():
        return text
    last = block[-1]
    if (_indent(last) < _CENTERED_INDENT or 2 * _indent(last) + len(last.strip()) not in _LINE_WIDTH
            or any(3 <= _indent(ln) < _CENTERED_INDENT for ln in block)
            or text.startswith(("[", "From the Congressional Record"))
            or not text.strip("_ ")
            or _TALLY_RE.fullmatch(text)
            or SPEAKER_RE.match(block[0]) or OFFICER_RE.match(block[0])):
        return None
    return text


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        if ln.strip():
            current.append(ln)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _speech(speaker: str, heading: str | None, opens: bool, granule_title: str) -> dict:
    return {"speaker": speaker, "text": "", "heading": heading, "opens": opens, "granule_title": granule_title}


def parse_granule_speeches(
    granule_html: str, granule_title: str = "", floor: str | None = None,
) -> tuple[list[dict], str | None]:
    """Split one Record granule into member speeches, in order; returns
    them and the member still holding the floor at its end (None if an
    officer spoke since). `granule_title` is the granule's title from its
    MODS; `floor`, the member holding the floor at the end of the
    granule before it.

    Each speech is a dict:
      - ``speaker``: the surname as printed, with " of State" where the
        Record gives one ("SMITH of Florida");
      - ``text``: the member's whole turn, paragraphs kept, untruncated —
        consecutive turns by the same member under the same heading, with
        no one else in between, are one speech;
      - ``heading``: the Record's heading the turn sits under (the
        granule's own title until the Record prints another);
      - ``opens``: whether the turn follows its heading directly — the
        speech the Record titled. A presiding officer's or clerk's words
        in between ("In the Committee of the Whole", then the chair's
        statement of the bill) mean the heading is the business's, not
        this member's;
      - ``granule_title``: the granule's title.

    A heading followed by quoted text (indented three spaces or more) is a
    document's — an article or a bill a member put in the Record, under
    its own headline — not the Record's: the heading before it stands.

    The Record designates a member once per recognition: a member who
    goes on to another subject under a new heading of the Record's (a
    leader's remarks often cover several, each headed) is not designated
    again, and the words go on with an address to the chair ("  Mr.
    President, on China, ..."), which only a member makes. Those are the
    same member's, a new speech under the new heading. Paragraphs under a
    new heading that address no one (committee meeting authorizations, the
    parts of an arms sale notification a member put in the Record) are
    documents, not speech. A leader's subjects can also be granules of
    their own, the next opening "  Mr. President, now on ..." with no
    designation at all: those are the member's who held the floor at the
    end of the granule before.
    """
    granule_title = _squash(granule_title)
    speeches: list[dict] = []
    heading: str | None = granule_title or None
    opens = True  # the next member turn follows its heading directly
    before_headings: tuple[str | None, bool] | None = None  # state before a run of headings
    current: dict | None = None  # the turn being read; None inside officer text
    # The latest member speech, and whether no officer has spoken since it
    # began.
    last: dict | None = _speech(floor, None, False, granule_title) if floor else None
    holds_floor = last is not None
    for block in _blocks(record_lines(granule_html)):
        title = _heading(block, granule_title)
        if title is not None:
            before_headings = before_headings or (heading, opens)
            heading, opens, current = title, True, None
            continue
        if before_headings is not None and _indent(block[0]) >= 3:
            heading, opens = before_headings
        before_headings = None
        if not "".join(block).strip("_ "):  # the rule the Record prints between items
            current, holds_floor = None, False
            continue
        for ln in block:
            member = SPEAKER_RE.match(ln)
            if member and member.group(1).strip() in _OFFICER_NAMES or not member and OFFICER_RE.match(ln):
                current, opens, holds_floor = None, False, False
                continue
            if member:
                name = member.group(1).strip()
                speaker = f"{name} of {member.group(2)}" if member.group(2) else name
                if current is None or current["speaker"] != speaker:
                    current = _speech(speaker, heading, opens, granule_title)
                    speeches.append(current)
                    opens, last, holds_floor = False, current, True
                current["text"] += "\n" + ln[member.end():].strip()
                continue
            if current is None and holds_floor and _indent(ln) == 2:
                # The member holding the floor goes on past a heading: past
                # a document's, the same speech; under a new heading of the
                # Record's, a speech of its own.
                if heading == last["heading"]:
                    current = last
                elif _ADDRESS_RE.match(ln):
                    current = _speech(last["speaker"], heading, opens, granule_title)
                    speeches.append(current)
                    opens, last = False, current
            if current is not None:
                # Two spaces in starts a paragraph, six or more a quoted
                # one; anything else continues the line before.
                joiner = "\n" if _indent(ln) == 2 or _indent(ln) > 5 else " "
                current["text"] += joiner + ln.strip()
    for s in speeches:
        s["text"] = s["text"].strip()
    return [s for s in speeches if s["text"]], last["speaker"] if last and holds_floor else None


# ── Fetch ────────────────────────────────────────────────────────


def granule_url(package_id: str, granule_id: str) -> str:
    """The Record section's own page on GovInfo (text and PDF of that
    granule) — the most specific public address a speech has."""
    return f"https://www.govinfo.gov/app/details/{package_id}/{granule_id}"


CHAMBERS = ("Senate", "House")


async def fetch_day_speeches(client: httpx.AsyncClient, package_id: str) -> dict[str, list[dict]] | None:
    """Every member turn in one day's Record, per chamber ("Senate",
    "House"), each dict as parse_granule_speeches plus ``granule_id``,
    ``date`` and ``url``; None when anything could not be fetched, so a
    partial day is retried whole rather than read as a quiet one.

    Not cached here: the caller stores the result as documents and records
    the day as read, which is what keeps it from being fetched again."""
    mods = await fetch_package_mods(client, package_id)
    if mods is None:
        return None
    try:
        granules = {chamber: speech_granules(mods, chamber.upper()) for chamber in CHAMBERS}
    except ET.ParseError:
        logger.warning("CREC MODS for %s is not XML", package_id)
        return None
    out: dict[str, list[dict]] = {chamber: [] for chamber in CHAMBERS}
    for chamber, listed in granules.items():
        floor = None
        for granule_id, title in listed:
            page = await _fetch_htm(client, f"{GOVINFO_API_BASE}/packages/{package_id}/granules/{granule_id}/htm")
            if page is None:
                return None
            speeches, floor = parse_granule_speeches(page, title, floor)
            for speech in speeches:
                out[chamber].append({**speech, "granule_id": granule_id, "date": issue_date(package_id),
                                     "url": granule_url(package_id, granule_id)})
    return out
