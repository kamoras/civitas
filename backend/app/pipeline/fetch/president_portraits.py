"""Each president's portrait: a public-domain image, with its credit.

Which image: Wikidata's image (P18) for every person who held the office of
President of the United States (Q11696), keyed by the office's own series
ordinal on that statement (P1545: 1 for the first president, and two numbers
for a person with two non-consecutive terms). Nothing here names a
president: a new one is picked up when Wikidata records the office.

Whether to use it: only a file Wikimedia Commons marks public domain (an
official portrait is a work of the US government, or old enough), read from
the file's own license metadata. A file under any other license is skipped,
and that president's page keeps its placeholder. The credit shown is the
file's author and credit lines from the same metadata, with the Commons page
linked.

Where Wikidata's image isn't public domain (checked 2026-10-20: one
president's is a library's CC BY scan), the National Portrait Gallery's open
access collection stands in: the Smithsonian's API (api.data.gov key), an
object whose media the Smithsonian marks CC0 and whose one sitter is the
president (each word of the roster's name matching the sitter's, an initial
standing for a name). The credit is the object's artist and the gallery.

Stored in api_cache for the read path (`stored_portraits`), which serves the
image through the site's own `/photo/president/<id>` (AGENTS.md §8): the
thumbnail URLs are on Wikimedia's and the Smithsonian's image hosts, which
`app.photos` allows for the kind. An outage keeps the last stored set.
"""

import logging
import re
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.contact import BOT_USER_AGENT
from app.models import President
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_SPARQL_URL = "https://query.wikidata.org/sparql"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Every holder of the office with its ordinal and an image.
_QUERY = """
SELECT ?ordinal ?image WHERE {
  ?person p:P39 ?held .
  ?held ps:P39 wd:Q11696 ; pq:P1545 ?ordinal .
  ?person wdt:P18 ?image .
}
"""
_CACHE_TIER = "president-portraits"
_CACHE_KEY = "by-number"
_CACHE_TTL_HOURS = 24 * 30
_NO_EXPIRY_HOURS = 24 * 365 * 100
# Wide enough for the profile's portrait at 3x, small enough to stay cheap.
_THUMB_WIDTH = 480
_HEADERS = {"User-Agent": BOT_USER_AGENT}
_RATE_LIMITER = RateLimiter(rps=1.0)
_SI_SEARCH = "https://api.si.edu/openaccess/api/v1.0/search"
# The Smithsonian's image delivery service: the only media address used.
_NPG_IMAGE_PREFIX = "https://ids.si.edu/ids/deliveryService?id="
# Generational suffixes in a roster name ("Jr."), not name words to match.
_SUFFIXES = {"jr", "sr", "ii", "iii"}
_PUBLIC_DOMAIN = re.compile(r"\bpublic domain\b|^pd\b|^pd-|^cc0", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")


def _text(meta: dict, key: str) -> str:
    value = (meta.get(key) or {}).get("value") or ""
    return re.sub(r"\s+", " ", _TAG.sub("", str(value))).strip()


def portrait_from_imageinfo(page: dict) -> dict | None:
    """The stored portrait for one Commons file page, or None when the file
    is not marked public domain or has no thumbnail."""
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    license_name = _text(meta, "LicenseShortName")
    if not _PUBLIC_DOMAIN.search(license_name) or not info.get("thumburl"):
        return None
    return {
        # Without Commons' tracking query (utm_*): the file's own address.
        "url": info["thumburl"].split("?", 1)[0],
        "page": info.get("descriptionurl"),
        "license": license_name,
        "artist": _text(meta, "Artist") or None,
        "credit": _text(meta, "Credit") or None,
    }


def _name_words(name: str) -> list[str]:
    return [w for w in re.split(r"[\s.,]+", name.lower()) if w and w not in _SUFFIXES]


def sitter_is(roster_name: str, sitter: str) -> bool:
    """Every word of the roster's name is a word of the sitter's ("Franklin
    Delano Roosevelt, 30 Jan 1882 - 12 Apr 1945"), or its initial."""
    sitter_words = _name_words(sitter.split(",", 1)[0])
    return bool(sitter_words) and all(
        w in sitter_words or (len(w) == 1 and any(s.startswith(w) for s in sitter_words))
        for w in _name_words(roster_name)
    )


def npg_portrait_from_row(row: dict, roster_name: str) -> dict | None:
    """The stored portrait for one Smithsonian search row: an NPG object with
    CC0 image media whose only sitter is the president; else None."""
    content = row.get("content") or {}
    names = (content.get("freetext") or {}).get("name") or []
    sitters = [n.get("content", "") for n in names if n.get("label") == "Sitter"]
    if len(sitters) != 1 or not sitter_is(roster_name, sitters[0]):
        return None
    meta = content.get("descriptiveNonRepeating") or {}
    if meta.get("unit_code") != "NPG":
        return None
    media = next((m for m in ((meta.get("online_media") or {}).get("media") or [])
                  if (m.get("usage") or {}).get("access") == "CC0" and m.get("type") == "Images"), None)
    if not media or not str(media.get("content", "")).startswith(_NPG_IMAGE_PREFIX):
        return None
    artist = next((n.get("content", "").split(",")[0] for n in names if n.get("label") == "Artist"), None)
    return {
        "url": f"{media['content']}&max={_THUMB_WIDTH}",
        "page": meta.get("record_link"),
        "license": "CC0",
        "artist": artist,
        "credit": "National Portrait Gallery, Smithsonian Institution",
        "via": "the National Portrait Gallery, Smithsonian Institution",
    }


async def npg_portrait(client: httpx.AsyncClient, roster_name: str) -> dict | None:
    """The first CC0 portrait of the president alone in the NPG's open access
    collection, or None (also when no key is set or the API can't be read)."""
    if not settings.DATA_GOV_API_KEY:
        return None
    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", _SI_SEARCH, log_label="Smithsonian NPG portraits",
        request_url=_SI_SEARCH + "?" + urlencode({
            "q": f'{roster_name} AND unit_code:NPG AND online_media_type:"Images"', "rows": 20,
            "api_key": settings.DATA_GOV_API_KEY}),
        headers=_HEADERS,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Smithsonian open access search could not be read")
        return None
    for row in (resp.json().get("response") or {}).get("rows") or []:
        if portrait := npg_portrait_from_row(row, roster_name):
            return portrait
    return None


async def fetch_president_portraits(client: httpx.AsyncClient, db: Session) -> dict[str, dict] | None:
    """{president number (as a string): portrait} for every president whose
    Wikidata image is public domain on Commons, or None when the sources
    can't be read (the last stored set is kept)."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_TTL_HOURS)
    if cached is not None:
        return cached["portraits"]
    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", _SPARQL_URL, log_label="Wikidata president portraits",
        params={"query": _QUERY, "format": "json"}, headers=_HEADERS,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Wikidata president portraits could not be read")
        return None
    files: dict[str, str] = {}
    for row in resp.json().get("results", {}).get("bindings", []):
        ordinal = row.get("ordinal", {}).get("value", "")
        image = row.get("image", {}).get("value", "")
        if ordinal.isdigit() and "/Special:FilePath/" in image:
            # One image per number; a person with several keeps the first.
            files.setdefault(ordinal, "File:" + httpx.URL(image).path.rsplit("/", 1)[1])
    portraits: dict[str, dict] = {}
    titles = sorted(set(files.values()))
    by_title: dict[str, dict] = {}
    for i in range(0, len(titles), 40):  # the API's 50-title limit, with room
        resp = await fetch_with_retry(
            client, _RATE_LIMITER, "GET", _COMMONS_API, log_label="Commons portrait licenses",
            params={"action": "query", "format": "json", "prop": "imageinfo",
                    "iiprop": "url|extmetadata", "iiurlwidth": _THUMB_WIDTH,
                    "titles": "|".join(titles[i:i + 40])},
            headers=_HEADERS,
        )
        if resp is None or resp.status_code != 200:
            logger.warning("Commons portrait licenses could not be read")
            return None
        body = resp.json().get("query", {})
        # The API returns titles normalised (underscores to spaces, decoded).
        normal = {n["from"]: n["to"] for n in body.get("normalized", [])}
        pages = {p.get("title"): p for p in body.get("pages", {}).values()}
        for t in titles[i:i + 40]:
            page = pages.get(normal.get(t, t))
            if page and (portrait := portrait_from_imageinfo(page)):
                by_title[t] = portrait
    for number, title in files.items():
        if title in by_title:
            portraits[number] = by_title[title]
    # The gallery stands in for anyone left: same person under every number.
    for p in sorted(db.query(President).all(), key=lambda p: p.number):
        if str(p.number) in portraits:
            continue
        same = next((portraits[str(o.number)] for o in db.query(President).filter(President.name == p.name)
                     if str(o.number) in portraits), None)
        found = same or await npg_portrait(client, p.name)
        if found:
            portraits[str(p.number)] = found
    if not portraits:
        logger.warning("No public-domain president portrait found; keeping the last stored set")
        return None
    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"portraits": portraits})
    return portraits


def stored_portraits(db: Session) -> dict[str, dict]:
    """The last stored portraits, whatever their age (the read path never
    fetches); empty when none are stored."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_NO_EXPIRY_HOURS)
    return cached["portraits"] if cached else {}
