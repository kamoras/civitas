"""Issuer industry for a disclosed security, from the SEC's own records.

SEC.gov publishes a first-party bulk file mapping every registered ticker to
its company name and CIK, and a per-company submissions record carrying the
Standard Industrial Classification (SIC) code the SEC assigned the issuer —
no third-party dependency, consistent with this repo's sourcing posture
(see issue #45 investigation: the two third-party "Stock Watcher" shortcuts
are both dead).

The SIC code is structured metadata (tier 1 of the classification strategy):
the SEC's own statement of what the issuer does. It replaced running the
company *name* through the donor-industry embedding classifier, which put
Broadcom, ConocoPhillips and PPG under LOBBYISTS and GitLab and CoStar under
PRIVATE_PRISON (production, 2026-09-29). The SIC description itself is no
better a query: embedding all 429 SEC descriptions against the industry
prototypes read "Semiconductors & Related Devices" as PHARMA, "National
Commercial Banks" as REAL_ESTATE and "Motor Vehicles & Passenger Car Bodies"
as INSURANCE. So the code is decoded by the published SIC hierarchy
(SIC_INDUSTRY below) — a code the SEC assigned, translated into our
vocabulary the way fd_common.HOUSE_ASSET_TYPE_CATEGORY translates the House's
asset-type codes.
"""

import logging
import re
import time

import httpx
from sqlalchemy.orm import Session

from app.contact import CONTACT_EMAIL
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# SEC requests a descriptive User-Agent with contact info on all automated
# requests (https://www.sec.gov/os/webmaster-faq#developers) — a generic UA
# risks a block.
_HEADERS = {"User-Agent": f"Civitas civic-transparency-platform {CONTACT_EMAIL}"}
# The SEC's fair-access limit is 10 requests a second; half of it.
_rate_limiter = RateLimiter(rps=5.0)

_TICKERS_TTL_HOURS = 24 * 7
# An issuer's SIC code rarely changes; a first run fetches one record per
# issuer ever traded (~2,000, ~7 minutes), later runs only new issuers.
_SIC_TTL_HOURS = 24 * 90


class SecUnavailable(Exception):
    """The SEC's records couldn't be read. Raised, not answered with an
    empty result: a caller relabelling stored trades from an empty answer
    would clear every label on a night the SEC was down."""


_issuers_cache: dict | None = None
_issuers_cache_at: float = 0.0


# SIC code ranges -> our industry, from the SIC manual's own hierarchy
# (divisions, major groups, industry groups: https://www.sec.gov/search-filings/
# standard-industrial-classification-sic-code-list). The narrowest range
# containing a code wins, so a group can carve out of its division. None:
# the SIC names an industry we have no category for (metal mining, waste
# management, ADRs, blank-check shells...), which stays unclassified rather
# than forced into the nearest label.
SIC_INDUSTRY: tuple[tuple[int, int, str | None], ...] = (
    (100, 999, "AGRIBUSINESS"),       # agriculture, forestry, fishing
    (1200, 1299, "ENERGY"),           # coal mining
    (1300, 1399, "OIL_GAS"),          # oil & gas extraction
    (1500, 1799, "CONSTRUCTION"),
    (2000, 3999, "MANUFACTURING"),
    (2000, 2099, "AGRIBUSINESS"),     # food & kindred products
    (2100, 2199, "TOBACCO"),
    (2700, 2799, "MEDIA"),            # printing & publishing
    (2830, 2836, "PHARMA"),           # drugs
    (2870, 2879, "AGRIBUSINESS"),     # agricultural chemicals
    (2900, 2999, "OIL_GAS"),          # petroleum refining
    (3480, 3489, "GUNS"),             # ordnance & accessories (small arms, ammunition)
    (3570, 3579, "TECH"),             # computer & office equipment
    (3660, 3669, "TELECOM"),          # communications equipment
    (3670, 3679, "TECH"),             # electronic components, semiconductors
    (3720, 3729, "DEFENSE"),          # aircraft & parts
    (3760, 3769, "DEFENSE"),          # guided missiles & space vehicles
    (3812, 3812, "DEFENSE"),          # search, detection, navigation, guidance
    (3840, 3851, "HEALTHCARE"),       # medical instruments & supplies, ophthalmic goods
    (4000, 4799, "TRANSPORT"),
    (4600, 4699, "OIL_GAS"),          # pipelines
    (4800, 4899, "TELECOM"),
    (4830, 4839, "MEDIA"),            # radio & television broadcasting
    (4900, 4999, "ENERGY"),           # electric, gas & sanitary services
    (4920, 4929, "OIL_GAS"),          # natural gas transmission & distribution
    (4950, 4959, None),               # sanitary services, refuse, hazardous waste
    (5000, 5999, "RETAIL"),           # wholesale & retail trade
    (5170, 5172, "OIL_GAS"),          # petroleum products wholesale
    (6000, 6299, "FINANCE"),          # banks, credit, brokers, exchanges
    (6300, 6499, "INSURANCE"),
    (6500, 6599, "REAL_ESTATE"),
    (6700, 6799, "FINANCE"),          # holding & other investment offices
    (6792, 6792, "OIL_GAS"),          # oil royalty traders
    (6795, 6795, None),               # mineral royalty traders
    (6798, 6798, "REAL_ESTATE"),      # real estate investment trusts
    (7000, 7099, "REAL_ESTATE"),      # hotels (industry_classifier's lodging rule)
    (7310, 7319, "MEDIA"),            # advertising
    (7370, 7379, "TECH"),             # computer programming, software, data processing
    (7800, 7899, "MEDIA"),            # motion pictures
    (8000, 8099, "HEALTHCARE"),
    (8100, 8199, "LAWYERS"),
    (8200, 8299, "EDUCATION"),
    (8711, 8711, "CONSTRUCTION"),     # engineering services
    (8731, 8731, "PHARMA"),           # commercial physical & biological research
)


def industry_for_sic(sic: int | None) -> str | None:
    """Our industry for an SEC SIC code, or None (no code, or no category)."""
    if not sic:
        return None
    matches = [(high - low, industry) for low, high, industry in SIC_INDUSTRY if low <= sic <= high]
    return min(matches, key=lambda m: m[0])[1] if matches else None


# Legal-form words a name is compared without. EDGAR's conformed names
# abbreviate them ("COMCAST CORP", "HOME DEPOT, INC.") where a filer such
# as a PAC's sponsor writes them out ("Comcast Corporation", "The Home
# Depot Inc."), so exact names missed 45% of the corporate PAC sponsors the
# SEC lists (measured 2026-10-08). A title two issuers share once these are
# dropped is left out, as before.
_LEGAL_FORMS = frozenset({
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY",
    "LTD", "LIMITED", "LLC", "PLC", "LP", "&",
})


def issuer_key(name: str) -> str:
    """A company name as compared against the SEC's titles: upper case,
    punctuation dropped, the SEC's trailing state-of-incorporation tag
    ("AMETEK INC/", "EXXON MOBIL CORP /NJ/") cut off, and a leading "The"
    and trailing legal-form words dropped."""
    words = re.sub(r"[^A-Z0-9& ]", " ", name.upper().split("/")[0]).split()
    if words[:1] == ["THE"]:
        words = words[1:]
    while words and words[-1] in _LEGAL_FORMS:
        words.pop()
    return " ".join(words)


# Words a brokerage statement writes after a company's name to describe the
# security, not the issuer: share class ("CL A", "CLASS B", "SERIES A"),
# kind ("COM", "COMMON STOCK", "ORDINARY SHARES", "SHS", "ADR"), a successor
# company ("NEW"), the state or country of incorporation ("DEL", "IRELAND"),
# and legal forms. A form-vocabulary convention, like _LEGAL_FORMS.
_SECURITY_WORDS = frozenset({
    "CLASS", "CL", "A", "B", "C", "SERIES", "SER", "COM", "COMMON", "STOCK", "CAPITAL", "SHS",
    "SHARES", "ORDINARY", "ADR", "ADS", "SPONSORED", "SPON", "NEW", "REIT", "DEL", "IRELAND",
    "NV", "PAR", "VALUE", "USD", "HLDGS", "HOLDINGS",
}) | _LEGAL_FORMS


def _abbreviates(short: str, word: str) -> bool:
    """Whether `short` is `word` as statements abbreviate it: the word, a
    prefix ("PAC" for PACIFIC, a name cut at 24 characters), or its letters
    in order from the same first letter ("INTL", "FINL", "PWR")."""
    if short == word or word.startswith(short):
        return True
    if short[:1] != word[:1]:
        return False
    rest = iter(word)
    return all(ch in rest for ch in short)


def _words(name: str) -> list[str]:
    words = [w for w in re.sub(r"[^A-Z0-9 ]", " ", name.upper().split("/")[0]).split()]
    return words[1:] if words[:1] == ["THE"] else words


def abbreviated_match(name: str, titles: dict[str, int]) -> int | None:
    """The CIK of the one SEC title `name` writes in a statement's short
    form, or None for none or more than one. The name must start with the
    title's words, each abbreviated at most (_abbreviates), "&" aside, and
    may go on only with words describing the security (_SECURITY_WORDS):
    "UNION PAC CORP COM" is Union Pacific, "HONEYWELL INTL INC" Honeywell
    International. A bond line ("... 5% DUE 2030") or anything else after
    the name matches nothing.

    Measured 2026-10-08 on the 1,446 names of a president's annual-report
    transactions with no industry: exact titles matched 192, this 683 more
    (34 matched two titles and were left), and 120 sampled matches were all
    the right company."""
    words = [w for w in _words(name) if w != "&"]
    found: set[int] = set()
    for n in range(1, len(words) + 1):
        if any(w not in _SECURITY_WORDS for w in words[n:]):
            continue
        for title_words, cik in _titles_by_shape(titles).get((n, words[0][:1]), ()):
            if all(_abbreviates(s, w) for s, w in zip(words[:n], title_words)):
                found.add(cik)
    return found.pop() if len(found) == 1 else None


_shape_index: tuple[dict, dict] | None = None


def _titles_by_shape(titles: dict[str, int]) -> dict:
    """{(word count, first letter): [(title words, cik)]}, built once per
    titles map."""
    global _shape_index
    if _shape_index is None or _shape_index[0] is not titles:
        index: dict = {}
        for key, cik in titles.items():
            words = [w for w in key.split() if w != "&"]
            if words:
                index.setdefault((len(words), words[0][:1]), []).append((words, cik))
        _shape_index = (titles, index)
    return _shape_index[1]


async def _fetch_issuers(client: httpx.AsyncClient, db: Session) -> dict:
    """{"tickers": {TICKER: cik}, "titles": {issuer_key(title): cik}} for
    every SEC-registered issuer. A title shared by two CIKs is left out
    rather than guessed between."""
    global _issuers_cache, _issuers_cache_at
    # Re-consult the TTL'd DB cache after its TTL even though the module
    # global is populated — the stock pipeline runs inside the long-lived
    # scheduler process, so a process-lifetime global would freeze the map
    # until restart, resolving new IPOs / ticker changes to nothing.
    if _issuers_cache is not None and (time.time() - _issuers_cache_at) < _TICKERS_TTL_HOURS * 3600:
        return _issuers_cache

    cached = api_cache_get(db, "sec_tickers", "company_issuers-v2", max_age_hours=_TICKERS_TTL_HOURS)
    if cached is None:
        try:
            resp = await client.get(TICKERS_URL, headers=_HEADERS, timeout=DEFAULT_FETCH_TIMEOUT_S)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            if _issuers_cache is not None:
                logger.warning("SEC company_tickers.json unavailable, using the last copy: %s", e)
                return _issuers_cache
            raise SecUnavailable(f"company_tickers.json: {e}") from e
        # Raw shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        titles: dict[str, int | None] = {}
        for entry in raw.values():
            key = issuer_key(entry.get("title") or "")
            if key:
                titles[key] = entry["cik_str"] if titles.get(key, entry["cik_str"]) == entry["cik_str"] else None
        cached = {
            "tickers": {e["ticker"].upper(): e["cik_str"] for e in raw.values() if e.get("ticker")},
            "titles": {k: v for k, v in titles.items() if v is not None},
        }
        api_cache_set(db, "sec_tickers", "company_issuers-v2", cached, normal_ttl_hours=_TICKERS_TTL_HOURS)
    _issuers_cache, _issuers_cache_at = cached, time.time()
    return cached


async def _sic_for(client: httpx.AsyncClient, db: Session, cik: int) -> int | None:
    """The SIC code the SEC assigned an issuer, or None when it assigned
    none or has no record (404). Raises SecUnavailable when the record
    couldn't be read — not cached then, so tried again."""
    key = str(cik)
    cached = api_cache_get(db, "sec_sic", key, max_age_hours=_SIC_TTL_HOURS)
    if cached is not None:
        return cached.get("sic")
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", SUBMISSIONS_URL.format(cik=cik), log_label="SEC submissions",
        headers=_HEADERS, expected_statuses=(404,),
    )
    if resp is None:
        raise SecUnavailable(f"submissions for CIK {cik}")
    try:
        record = resp.json() if resp.status_code != 404 else {}
    except ValueError as e:
        raise SecUnavailable(f"submissions for CIK {cik}: not JSON") from e
    sic = int(record["sic"]) if str(record.get("sic") or "").isdigit() else None
    api_cache_set(db, "sec_sic", key, {"sic": sic}, normal_ttl_hours=_SIC_TTL_HOURS)
    return sic


async def issuer_industries(
    client: httpx.AsyncClient, db: Session, tickers: list[str], names: list[str],
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """(ticker -> industry, name -> industry) for the tickers and company
    names the SEC knows. A ticker or name absent from a result has no SEC
    record (an ETF, an OTC ADR, a bond, a private company); one present
    with None has a record whose SIC names no category of ours. A name
    matches a title that is the same once normalized (issuer_key), or the
    one title it writes in a statement's abbreviated form
    (abbreviated_match) — never a near miss, which on company names is a
    different company.
    Raises SecUnavailable when the SEC's records couldn't be read; what was
    read before that stays cached, so the next call picks up from there."""
    issuers = await _fetch_issuers(client, db)
    # The SEC writes a share class with a hyphen ("BRK-B"); filings often use a dot.
    by_ticker = {t: cik for t in tickers if (cik := issuers["tickers"].get(t.upper().replace(".", "-")))}
    by_name = {}
    for n in names:
        cik = issuers["titles"].get(issuer_key(n)) or abbreviated_match(n, issuers["titles"])
        if cik:
            by_name[n] = cik
    sics = {cik: await _sic_for(client, db, cik) for cik in {*by_ticker.values(), *by_name.values()}}
    return (
        {t: industry_for_sic(sics[cik]) for t, cik in by_ticker.items()},
        {n: industry_for_sic(sics[cik]) for n, cik in by_name.items()},
    )
