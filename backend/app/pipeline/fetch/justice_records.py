"""The academic records the justice score reads (justice_loyalty.py).

- The Supreme Court Database (Spaeth, Epstein, Martin et al.,
  scdb.la.psu.edu): every justice's vote in every orally argued case, with
  each case's lead parties and who won. Its newest release is found on the
  data archive page every run and read once.
- The Federal Judicial Center's directory of Article III judges
  (fjc.gov): each justice's nomination, commission and end of service. The
  appointing president is whoever held office on the nomination date, from
  the presidents' own terms, so no president's name is matched.
- Martin-Quinn scores (mqscores.wustl.edu): each justice's position per
  term, shown on the scorecard, not scored.

A fetch that fails returns None, and the caller keeps what it stored last:
an unreachable source is not a justice who cast no votes.
"""

import csv
import io
import logging
import re
import zipfile
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.pipeline.analyze.justice_loyalty import Vote
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

SCDB_ARCHIVE = "https://scdb.la.psu.edu/data/"
FJC_JUDGES = "https://www.fjc.gov/sites/default/files/history/judges.csv"
MQ_JUSTICES = "https://mqscores.wustl.edu/media/{year}/justices.csv"

_CACHE_TIER = "justice_records"
_rate_limiter = RateLimiter(rps=1.0)

_RELEASE_RE = re.compile(r"https://scdb\.la\.psu\.edu/data/(\d{4})-release-(\d{2})/")
_DOWNLOAD_RE = re.compile(r'<a[^>]+href="(https://scdb\.la\.psu\.edu/\?jet_download=[0-9a-f]+)"[^>]*>(.*?)</a>', re.S)
_SCDB_COLUMNS = {"justiceName", "voteId", "dateDecision", "term", "decisionType",
                 "petitioner", "respondent", "partyWinning", "majority"}
# Orally argued, signed decisions: an opinion of the Court (1) or a
# judgment of the Court (7), as Epstein & Posner count them.
_SIGNED_DECISIONS = {"1", "7"}
# The Database's codes for the federal government as a party: 1 (the
# Attorney General), 27 (the United States) and its federal block. Learned
# from Epstein & Posner's hand-coded cases, 1946-2001, and checked on
# 2002-2014 (docs/research/justice-scores.md).
_GOVERNMENT_CODES = (1, 27)
_FEDERAL_BLOCK = range(300, 421)


def _government(code: str) -> bool:
    try:
        n = int(float(code))
    except (TypeError, ValueError):
        return False
    return n in _GOVERNMENT_CODES or n in _FEDERAL_BLOCK


def _iso(date: str) -> str | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return None


async def _get(client: httpx.AsyncClient, url: str, label: str, **kwargs) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, retry_on_4xx=False, log_label=label, timeout=120.0, **kwargs,
    )


def scdb_president_votes(rows) -> tuple[list[tuple[Vote, int]], set[str], int]:
    """((vote, term) for every vote in a case with the federal government on
    exactly one side, the justices voting in the newest term, that term)
    from the Database's justice-centered rows."""
    votes: list[tuple[Vote, int]] = []
    newest: dict[int, set[str]] = {}
    for r in rows:
        try:
            term = int(r["term"])
        except (TypeError, ValueError):
            continue
        newest.setdefault(term, set()).add(r["justiceName"])
        if r["decisionType"] not in _SIGNED_DECISIONS or r["partyWinning"] not in ("0", "1") or r["majority"] not in ("1", "2"):
            continue
        pet, resp = _government(r["petitioner"]), _government(r["respondent"])
        date = _iso(r["dateDecision"])
        if pet == resp or date is None:
            continue
        petitioner_won, in_majority = r["partyWinning"] == "1", r["majority"] == "2"
        votes.append((Vote(
            justice=r["justiceName"], date=date, government_petitioner=pet,
            for_government=(petitioner_won == in_majority) if pet else (petitioner_won != in_majority),
        ), term))
    last = max(newest) if newest else 0
    return votes, newest.get(last, set()), last


async def fetch_scdb(client: httpx.AsyncClient, db: Session) -> dict | None:
    """The newest release's president-case votes: {"release", "term",
    "current": [justiceName], "votes": [[justice, date, government
    petitioner, for government, term], ...]}. None when the archive, the
    release page or its download can't be read, or the file isn't the
    justice-centered data it should be."""
    archive = await _get(client, SCDB_ARCHIVE, "SCDB archive")
    releases = sorted({(int(y), int(n)) for y, n in _RELEASE_RE.findall(archive.text)}) if archive else []
    if not releases:
        logger.warning("SCDB: no release found on %s", SCDB_ARCHIVE)
        return None
    year, number = releases[-1]
    label = f"{year} Release {number:02d}"
    cached = api_cache_get(db, _CACHE_TIER, f"scdb-{year}-{number:02d}", max_age_hours=24 * 365)
    if cached is not None:
        return cached
    page = await _get(client, f"{SCDB_ARCHIVE}{year}-release-{number:02d}/", f"SCDB {label}")
    if page is None:
        return None
    # The page lists the case-centered downloads, then the justice-centered
    # ones under their own heading; the justice-centered CSV organized by
    # citation is the first CSV-by-citation link after that heading.
    section = page.text.rfind("Justice Centered")
    link = next((
        href for href, text in _DOWNLOAD_RE.findall(page.text[section:])
        if "CSV" in text and "Citation" in re.sub(r"<[^>]+>", " ", text)
    ), None) if section >= 0 else None
    if link is None:
        logger.warning("SCDB %s: justice-centered CSV link not found", label)
        return None
    download = await _get(client, link, f"SCDB {label} download")
    if download is None:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(download.content)) as z:
            name = next(n for n in z.namelist() if n.endswith(".csv") and "justiceCentered" in n)
            reader = csv.DictReader(io.StringIO(z.read(name).decode("latin-1")))
            if not _SCDB_COLUMNS <= set(reader.fieldnames or ()):
                logger.warning("SCDB %s: unexpected columns %s", label, reader.fieldnames)
                return None
            votes, current, term = scdb_president_votes(reader)
    except (zipfile.BadZipFile, StopIteration, KeyError):
        logger.warning("SCDB %s: the download is not the justice-centered data", label, exc_info=True)
        return None
    result = {
        "release": label, "term": term, "current": sorted(current),
        "votes": [[v.justice, v.date, v.government_petitioner, v.for_government, t] for v, t in votes],
    }
    api_cache_set(db, _CACHE_TIER, f"scdb-{year}-{number:02d}", result, normal_ttl_hours=24 * 365)
    return result


def fjc_appointments(rows) -> list[dict]:
    """Every Supreme Court appointment in the FJC directory: {"last",
    "first", "nominated", "from", "until"}, dates ISO, `until` None while
    serving. A justice appointed twice (Rehnquist) has two."""
    out = []
    for r in rows:
        for i in range(1, 7):
            if r.get(f"Court Type ({i})") != "Supreme Court":
                continue
            nominated = _iso(r.get(f"Nomination Date ({i})") or "") or _iso(r.get(f"Recess Appointment Date ({i})") or "")
            start = _iso(r.get(f"Commission Date ({i})") or "") or _iso(r.get(f"Recess Appointment Date ({i})") or "")
            if nominated is None or start is None:
                continue
            out.append({
                "last": re.sub(r"[^a-z]", "", (r.get("Last Name") or "").lower()),
                "first": (r.get("First Name") or "")[:1].upper(),
                "nominated": nominated, "from": start,
                "until": _iso(r.get(f"Termination Date ({i})") or ""),
            })
    return out


async def fetch_fjc(client: httpx.AsyncClient, db: Session) -> list[dict] | None:
    cached = api_cache_get(db, _CACHE_TIER, "fjc-supreme-court", max_age_hours=24 * 7)
    if cached is not None:
        return cached
    resp = await _get(client, FJC_JUDGES, "FJC judges")
    if resp is None:
        return None
    appointments = fjc_appointments(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    if len(appointments) < 100:  # 116 appointments in 2026; far fewer is a changed file
        logger.warning("FJC judges: only %d Supreme Court appointments read", len(appointments))
        return None
    api_cache_set(db, _CACHE_TIER, "fjc-supreme-court", appointments, normal_ttl_hours=24 * 7)
    return appointments


async def fetch_martin_quinn(client: httpx.AsyncClient, db: Session) -> dict[str, list[list]] | None:
    """{justiceName: [[term, position], ...]} from the newest yearly file:
    each year's is published after the term it adds, so the newest can be
    a few years back."""
    cached = api_cache_get(db, _CACHE_TIER, "martin-quinn", max_age_hours=24 * 7)
    if cached is not None:
        return cached
    for year in range(utcnow().year, utcnow().year - 4, -1):
        # A year not yet published is a 404, the normal answer, not an error.
        resp = await _get(client, MQ_JUSTICES.format(year=year), f"Martin-Quinn {year}", no_retry_statuses=(404,))
        if resp is None or "justiceName" not in resp.text[:200]:
            continue
        out: dict[str, list[list]] = {}
        for r in csv.DictReader(io.StringIO(resp.text)):
            try:
                out.setdefault(r["justiceName"], []).append([int(r["term"]), round(float(r["post_mn"]), 3)])
            except (KeyError, ValueError):
                continue
        api_cache_set(db, _CACHE_TIER, "martin-quinn", out, normal_ttl_hours=24 * 7)
        return out
    return None
