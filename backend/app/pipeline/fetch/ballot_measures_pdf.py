"""Generic ballot-measure PDF pipeline stage: fetch, cache, and normalize
statewide ballot-measure PDFs for ANY state that has a registered source
and parsing strategy — the only way Civitas reads ballot measures.

This is deliberately NOT one parser that guesses an arbitrary state's PDF
layout. Every state's Secretary of State (or equivalent) publishes its
own format — California's Voter Information Guide has a two-level nested-
column layout (see ballot_measures_ma.py); other
states may turn out to need a completely different geometric strategy, or
none at all. Guessing a layout risks exactly the failure mode this
codebase treats as worst-case: a plausible-looking but WRONG yes/no
framing (AGENTS.md Core Design Principle 7). So each state gets its own
small, hand-verified page-parser function, built against that state's
real, currently-fetched document — but the fetch/cache/error-handling/
output-shaping code around it (this module) is shared, so adding a state
means writing one parse function + one registry entry
(ballot_measure_pdf_sources.json), not a whole new pipeline.

STRATEGIES maps a source's "strategy" key to that state's whole-document
parser function: `pages -> list[dict]` (pdfplumber's `pdf.pages`, not one
page) with keys number/title/origin/official_summary/fiscal_impact/
yes_means/no_means/title_authority/fiscal_authority (see
ballot_measures_ma.parse_information_for_voters for a reference implementation and
field-by-field contract). Operating on the whole document rather than one
page at a time is deliberate: California's format happens to fit one
proposition-pair per page, but Massachusetts's does not — a long ballot
question's summary can fill an entire page on its own, pushing that
question's "WHAT YOUR VOTE WILL DO" and fiscal-impact sections onto the
NEXT page (verified on the real document). A strategy that only ever saw
one page at a time could never stitch those back together; one that
scans however many pages it needs can.
"""

import io
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
import pdfplumber

from app.http_client import answering_client
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.ballot_measure_pdf_sources import terms_block, source_for_state
from app.models import BallotMeasure
from app.pipeline.fetch import web_archive
from app.pipeline.fetch.ballot_measure_text import DocumentNotLinked, NotYetPublished, SourceBlocked
from app.pipeline.fetch.ballot_measures_al import fetch_measures as al_fetch_measures
from app.pipeline.fetch.ballot_measures_ar import fetch_measures as ar_fetch_measures
from app.pipeline.fetch.ballot_measures_az import fetch_measures as az_fetch_measures
from app.pipeline.fetch.ballot_measures_az import fetch_republished as az_fetch_republished
from app.pipeline.fetch.ballot_measures_ca import fetch_measures as ca_fetch_measures
from app.pipeline.fetch.ballot_measures_co import parse_document as parse_co_document
from app.pipeline.fetch.ballot_measures_fl import fetch_measures as fl_fetch_measures
from app.pipeline.fetch.ballot_measures_ky import fetch_measures as ky_fetch_measures
from app.pipeline.fetch.ballot_measures_ak import parse_document as parse_ak_document
from app.pipeline.fetch.ballot_measures_hi import fetch_measures as hi_fetch_measures
from app.pipeline.fetch.ballot_measures_hi import fetch_archived as hi_fetch_archived
from app.pipeline.fetch.ballot_measures_id import parse_document as parse_id_document
from app.pipeline.fetch.ballot_measures_la import parse_document as parse_la_document
from app.pipeline.fetch.ballot_measures_ma import parse_information_for_voters as parse_ma_document
from app.pipeline.fetch.ballot_measures_md import fetch_measures as md_fetch_measures
from app.pipeline.fetch.ballot_measures_mo import fetch_measures as mo_fetch_measures
from app.pipeline.fetch.ballot_measures_nc import fetch_measures as nc_fetch_measures
from app.pipeline.fetch.ballot_measures_sc import fetch_measures as sc_fetch_measures
from app.pipeline.fetch.ballot_measures_tn import fetch_measures as tn_fetch_measures
from app.pipeline.fetch.ballot_measures_tx import fetch_measures as tx_fetch_measures
from app.pipeline.fetch.ballot_measures_va import fetch_measures as va_fetch_measures
from app.pipeline.fetch.ballot_measures_ct import fetch_measures as ct_fetch_measures
from app.pipeline.fetch.ballot_measures_me import fetch_measures as me_fetch_measures
from app.pipeline.fetch.ballot_measures_me import self_check as me_self_check
from app.pipeline.fetch.ballot_measures_nj import fetch_measures as nj_fetch_measures
from app.pipeline.fetch.ballot_measures_vt import parse_document as parse_vt_document
from app.pipeline.fetch.ballot_measures_wv import fetch_measures as wv_fetch_measures
from app.pipeline.fetch.ballot_measures_il import fetch_measures as il_fetch_measures
from app.pipeline.fetch.ballot_measures_in import fetch_measures as in_fetch_measures
from app.pipeline.fetch.ballot_measures_ks import fetch_measures as ks_fetch_measures
from app.pipeline.fetch.ballot_measures_civic import fetch_measures as civic_fetch_measures
from app.pipeline.fetch.ballot_measures_nd import fetch_measures as nd_fetch_measures
from app.pipeline.fetch.ballot_measures_ne import fetch_measures as ne_fetch_measures
from app.pipeline.fetch.ballot_measures_ok import fetch_measures as ok_fetch_measures
from app.pipeline.fetch.ballot_measures_sd import fetch_measures as sd_fetch_measures
from app.pipeline.fetch.ballot_measures_mt import fetch_measures as mt_fetch_measures
from app.pipeline.fetch.ballot_measures_nm import fetch_measures as nm_fetch_measures
from app.pipeline.fetch.ballot_measures_or import fetch_measures as or_fetch_measures
from app.pipeline.fetch.ballot_measures_ri import fetch_measures as ri_fetch_measures
from app.pipeline.fetch.ballot_measures_wa import fetch_measures as wa_fetch_measures
from app.pipeline.fetch.ballot_measures_wy import parse_document as parse_wy_document
from app.pipeline.fetch.ballot_measures_ga import fetch_measures as ga_fetch_measures
from app.pipeline.fetch.ballot_measures_ga import fetch_republished as ga_fetch_republished
from app.pipeline.fetch.ballot_measures_ms import fetch_measures as ms_fetch_measures
from app.pipeline.fetch.ballot_measures_nh import fetch_measures as nh_fetch_measures
from app.pipeline.fetch.ballot_measures_nv import fetch_measures as nv_fetch_measures
from app.pipeline.fetch.ballot_measures_nv import fetch_republished as nv_fetch_republished
from app.pipeline.fetch.ballot_measures_mt import fetch_archived as mt_fetch_archived
from app.pipeline.fetch.ballot_measures_oh import fetch_archived as oh_fetch_archived
from app.pipeline.fetch.ballot_measures_oh import fetch_measures as oh_fetch_measures
from app.pipeline.fetch.ballot_measures_state_common import same_site
from app.pipeline.fetch.ballot_measures_ut import fetch_measures as ut_fetch_measures

logger = logging.getLogger(__name__)

_PDF_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+\.pdf)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_ID_KEY_RE = re.compile(r"[^A-Za-z0-9]+")

# No state is required to name its guide any particular thing — there's
# no format spec to target, only convention. These are generic election-
# vocabulary terms, not any one state's branding, used to recognize a
# PAGE worth following, not to identify a specific document.
_FOLLOW_KEYWORDS = ("ballot", "measure", "amendment", "proposition", "referendum", "initiative", "voter guide", "pamphlet", "blue book")


def _matches(haystack: str, year: int, keywords: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    if any(x in haystack for x in exclude):
        return False
    if str(year) not in haystack:
        return False
    if any(k.lower() not in haystack for k in keywords):
        return False
    return True


async def discover_pdf_url(
    client: httpx.AsyncClient, start_url: str, year: int,
    keyword: str | tuple[str, ...] | None = None, exclude: tuple[str, ...] = ("primary",),
    max_pages: int = 8, max_depth: int = 2,
) -> str | None:
    url, _ = await discover_pdf_url_checked(
        client, start_url, year, keyword, exclude, max_pages=max_pages, max_depth=max_depth,
    )
    return url


async def discover_pdf_url_checked(
    client: httpx.AsyncClient, start_url: str, year: int,
    keyword: str | tuple[str, ...] | None = None, exclude: tuple[str, ...] = ("primary",),
    max_pages: int = 8, max_depth: int = 2,
) -> tuple[str | None, bool]:
    """This cycle's ballot-guide PDF, starting from a state's own election
    site — evergreen against BOTH failure modes states show in practice:
    a PDF filename that changes every cycle (verified: Colorado's real
    filename has no two years alike back to 2012), and a listing page
    that itself moves or gets restructured (no state is bound to any
    particular site layout — there's no format spec here, only whatever
    convention that state's web team happens to use this year).

    `start_url` doesn't have to be the exact page carrying the PDF link —
    a shallow, bounded crawl (`max_depth` hops, `max_pages` total fetches)
    follows same-domain links whose text/href matches generic election
    vocabulary (_FOLLOW_KEYWORDS — "ballot", "voter guide", ... — terms
    no state owns, not any one state's branding) until it finds a PDF
    link matching `year` (and `keyword`, if given — one or more durable
    branding terms like "blue book", not a filename; ALL must be present
    when more than one is given — verified necessary on Louisiana's real
    archive, where "nov" alone also matched an unrelated "November 2024
    Presidential Election" link, needing "nov" AND "constitutional"
    together to pick the right document). Never leaves the starting
    site (the starting host and its subdomains, same_site), so a page
    that happens to link an outside site (a news article, a different
    state, Ballotpedia) can't pull this off course.

    None if no confident match anywhere in the crawl — never guesses.

    discover_pdf_url_checked also says whether every page the crawl
    tried to read was actually read: (None, True) means the state's own
    pages were all there and none links this year's document, which is
    a different fact from (None, False) — a page that couldn't be
    fetched, where the document may well be linked. Only the first can
    ever read as "not published yet"; the second is an outage.
    """
    keywords = (keyword,) if isinstance(keyword, str) else tuple(keyword or ())
    start_domain = urlparse(start_url).netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    fetched = 0
    complete = True

    while queue and fetched < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            # A redirect within the state's own site is the page moved, not
            # gone: voteidaho.gov sent every page to a maintenance landing
            # page on archive.voteidaho.gov in 2026-10, which still links
            # the pamphlet, and a 302 read as an unreadable page failed the
            # state. One that leaves the site was not read (below).
            response = await client.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            html = response.text
        except Exception:
            complete = False
            continue
        landed = str(response.url)
        if response.history:
            # The document linked from where the page landed is taken, but
            # its absence there is not "not published": a moved page that
            # lands on a homepage or a stopgap says nothing about the
            # document the registered page would have linked.
            complete = False
            if not same_site(urlparse(landed).netloc, start_domain):
                continue
        url = landed
        visited.add(url)
        fetched += 1

        for href, text in _PDF_LINK_RE.findall(html):
            haystack = f"{href} {_TAG_RE.sub('', text)}".lower()
            if _matches(haystack, year, keywords, exclude):
                return urljoin(url, href), True

        if depth >= max_depth:
            continue
        for href, text in _LINK_RE.findall(html):
            if href.lower().endswith(".pdf"):
                continue
            haystack = f"{href} {_TAG_RE.sub('', text)}".lower()
            if not any(k in haystack for k in _FOLLOW_KEYWORDS):
                continue
            next_url = urljoin(url, href)
            if same_site(urlparse(next_url).netloc, start_domain) and next_url not in visited:
                queue.append((next_url, depth + 1))

    # A crawl that stopped at its page budget with links still queued did
    # NOT read everything it would have: the document could be on a page
    # it never opened, so that is not a "complete" miss.
    unread = [u for u, _ in queue if u not in visited]
    return None, complete and fetched > 0 and not unread

STRATEGIES = {
    "ma_information_for_voters": parse_ma_document,
    "co_quick_ballot_reference": parse_co_document,
    "la_proposed_amendments": parse_la_document,
    "vt_constitutional_amendment_notice": parse_vt_document,
    "ak_sample_ballot": parse_ak_document,
    "id_voter_pamphlet": parse_id_document,
    "wy_statewide_ballot_propositions": parse_wy_document,
}

# A strategy here doesn't fit STRATEGIES' `pdf.pages -> list[dict]`
# shape at all — the fetch/parse work can't be reduced to "hand this
# module a PDF's pages", either because the state's measures are several
# SEPARATE documents (Virginia — discovered from an index page, not one
# URL config can name) or because there's no PDF at all (Missouri — a
# single HTML page pdfplumber has nothing to do with). Either way the
# strategy function does its own end-to-end fetching and hands back
# (parsed, source_url) pairs directly. See each module's own docstring
# for why that state specifically needs this and most states don't.
# A reader's self-check, for a document that exists only in a year with a
# measure: `async (client, year) -> str | None`, the reader run on an
# earlier year's document from an official archive, None when it reads it
# whole. While this year's document is not published, a passing self-check
# means the reader works and "not published" is the state's answer
# (election_pipeline._late_cycle_check).
SELF_CHECKS = {
    "me_citizens_guide": me_self_check,
}

MULTI_DOCUMENT_STRATEGIES = {
    "ca_voter_guide_html": ca_fetch_measures,
    "va_referenda": va_fetch_measures,
    "mo_ballot_measures": mo_fetch_measures,
    "ct_sample_ballots": ct_fetch_measures,
    "me_citizens_guide": me_fetch_measures,
    "nj_public_questions": nj_fetch_measures,
    # Each reads that state's own certified list end to end (a landing
    # page of per-measure PDFs, one HTML page, a search form, a one-page
    # report) — see each module's docstring for the shape and what makes
    # an empty answer a checked one.
    "al_fair_ballot_statements": al_fetch_measures,
    "ar_general_assembly_referrals": ar_fetch_measures,
    "fl_initiatives_database": fl_fetch_measures,
    "ky_constitutional_amendments": ky_fetch_measures,
    "md_ballot_questions": md_fetch_measures,
    "nc_statewide_referenda": nc_fetch_measures,
    "sc_vrems_referendums": sc_fetch_measures,
    "tn_proposed_amendments": tn_fetch_measures,
    "tx_lrl_amendment_elections": tx_fetch_measures,
    "wv_amendment_notices": wv_fetch_measures,
    "il_voters_guide_questions": il_fetch_measures,
    "in_legislation_summary": in_fetch_measures,
    "ks_proposed_amendments": ks_fetch_measures,
    "nd_measures_on_ballot": nd_fetch_measures,
    "ne_ballot_measures": ne_fetch_measures,
    "ok_state_questions": ok_fetch_measures,
    "sd_ballot_questions": sd_fetch_measures,
    "hi_proposed_amendments": hi_fetch_measures,
    "mt_qualified_ballot_issues": mt_fetch_measures,
    "nm_sos_statewide_questions": nm_fetch_measures,
    "or_voters_pamphlet_absence": or_fetch_measures,
    "ri_voter_information_handbook": ri_fetch_measures,
    "wa_certified_measures": wa_fetch_measures,
    "ga_amendments_booklet": ga_fetch_measures,
    "ms_sample_ballot": ms_fetch_measures,
    "nh_general_election_questions": nh_fetch_measures,
    "nv_ballot_questions_booklet": nv_fetch_measures,
    "az_publicity_pamphlet": az_fetch_measures,
    "oh_official_sample_ballot": oh_fetch_measures,
    "ut_general_election_certification": ut_fetch_measures,
}

# Readers that can read the state's own document from another public
# office's republication of it — a county election office (GA, NV) or, for
# Arizona, the state's Citizens Clean Elections Commission — when the
# state's site can't be read at all (SourceBlocked); `republished_by` in
# the registry names the offices.
# Each takes (client, year, the county's page) and returns verified
# (parsed, url) pairs or None.
REPUBLISHED_STRATEGIES = {
    "ga_amendments_booklet": ga_fetch_republished,
    "nv_ballot_questions_booklet": nv_fetch_republished,
    "az_publicity_pamphlet": az_fetch_republished,
}

# Readers that can read the state's own document from the Internet
# Archive's capture of the state's own URL (web_archive.py), tried only
# when the state's host is walled (SourceBlocked) and no office in
# `republished_by` has a copy that verifies. Each takes (client, year, the
# state-host addresses this election's measures were last read from) and
# returns verified (parsed, capture url) pairs or None. An archived answer
# must also agree with the measures already stored from the state's own
# host (_agrees_with_stored).
ARCHIVED_STRATEGIES = {
    "oh_official_sample_ballot": oh_fetch_archived,
    "mt_qualified_ballot_issues": mt_fetch_archived,
    "hi_proposed_amendments": hi_fetch_archived,
}
# The fields a capture must reproduce exactly: the ballot's words.
_QUOTED_FIELDS = ("number", "official_title", "official_summary", "yes_means", "no_means")

# These are documents a state republishes wholesale on the rare occasion
# they change, so there is little to catch by polling more often: the
# platform's general 72h API-cache default. That applies to a non-empty list only:
# an empty answer ([], a confirmed none) is cached for
# cache.EMPTY_RESPONSE_TTL_HOURS (6h), so every nightly run re-checks it
# and a measure certified after a "none" appears the next night. A
# failure (None) and NotYetPublished are never cached at all.
CACHE_TTL_HOURS = 72

# "_v2": entries written before the list was cached bare (and before
# _finish's checks, and official_title's no-default rule) sat under
# "ballot_measure_pdf" as {"measures": [...]} and would otherwise be served
# for up to 72h after a deploy — unchecked, with labels presented as
# official titles. Nothing else reads the old tier; its rows simply expire.
CACHE_TIER = "ballot_measure_pdf_v2"


# A national reader of a state's measures, given the state and its entry:
# the Voting Information Project's ballot data through Google Civic, for a
# state whose own sites are not read (ballot_measures_civic.py).
NATIONAL_STRATEGIES = {
    "google_civic": civic_fetch_measures,
}


def is_configured(state: str) -> bool:
    """Whether `state` has both a registered source AND a strategy
    function for it — a source entry with a typo'd/unregistered strategy
    key is a config bug, not a signal to guess at parsing."""
    source = source_for_state(state)
    strategy = source.get("strategy") if source else None
    return strategy in STRATEGIES or strategy in MULTI_DOCUMENT_STRATEGIES or strategy in NATIONAL_STRATEGIES


def _to_measure(state: str, parsed: dict, election_date: str, source_url: str) -> dict:
    """One parsed proposition -> the combined raw+detail shape
    election_pipeline._upsert_measure expects. A strategy function
    carries every field in one pass, so the same dict is passed to
    _upsert_measure as both `raw` and `detail` — there's nothing a second
    fetch would add.
    """
    # A state whose ballot doesn't number its measures (NC, SC) or whose
    # page doesn't print the number (KY) supplies its own stable key
    # instead of a number this module would have to invent.
    id_key = parsed.get("id_key")
    key = _ID_KEY_RE.sub("-", id_key).strip("-") if id_key else parsed["number"]
    return {
        "id": f"{state}-{election_date}-{key}",
        "state": state,
        "election_date": election_date,
        "number": parsed["number"],
        "title": parsed["title"] or f"Proposition {parsed['number']}",
        # Only ever the state's own ballot title, and only when the
        # strategy says so by supplying it. `title` is a display label and
        # is often one this codebase's reader composed ("Proposition 3",
        # "Question 1: Citizen Initiative") or a publisher's heading that
        # is not on the ballot — rendering that under "OFFICIAL BALLOT
        # TITLE — Drafted by <office>" would put words in the drafter's
        # mouth. No default: a strategy that supplies nothing gets None.
        "official_title": parsed.get("official_title"),
        "official_summary": parsed["official_summary"],
        "fiscal_impact": parsed["fiscal_impact"],
        "yes_means": parsed["yes_means"],
        "no_means": parsed["no_means"],
        "measure_type": None,
        "origin": parsed["origin"],
        # Who drafted the quoted text: the official title's drafter when
        # there is one, otherwise the drafter of official_summary. The card
        # renders "Drafted by ..." beside whichever of the two it names.
        "title_authority": parsed.get("title_authority"),
        "fiscal_authority": parsed.get("fiscal_authority"),
        # The summary's and the yes/no framing's drafters, where the state
        # names one other than the title's (Alabama's Fair Ballot
        # Commission under the Legislature's question). None: no separate
        # drafter named.
        "summary_authority": parsed.get("summary_authority"),
        "framing_authority": parsed.get("framing_authority"),
        "source_url": source_url,
        # Set only when the state's own site couldn't be read and its
        # document was read from a county election office's republication
        # (_from_republished_copy): the card names both.
        "republished_by": parsed.get("republished_by"),
        # The state's own record says this measure was struck from the
        # ballot (Florida's Status "Removed"). Not upserted as on the
        # ballot; election_pipeline reconciles it to removed and counts it
        # as an explained drop, not a suspicious shrink.
        "removed": bool(parsed.get("removed")),
    }


def cached_answer(db, state: str, year: int) -> list[dict] | None:
    """The state's answer as currently cached (what fetch_state_measures_pdf
    would serve without asking the state), or None."""
    cached = api_cache_get(db, CACHE_TIER, f"{state}-{year}", max_age_hours=CACHE_TTL_HOURS)
    return cached if isinstance(cached, list) else None


def forget_cached(db, state: str, year: int) -> None:
    """Drop the state's cached answer, so the next fetch asks the state
    again — used when the pipeline holds an answer back as suspicious."""
    from app.models import ApiCache

    db.query(ApiCache).filter(
        ApiCache.tier == CACHE_TIER, ApiCache.cache_key == f"{state}-{year}",
    ).delete(synchronize_session=False)


async def _from_republished_copy(
    client: httpx.AsyncClient, state: str, source: dict, strategy_key: str, year: int, blocked: SourceBlocked,
) -> list[tuple[dict, str]] | None:
    """The state's own document, read from the first county office in
    `republished_by` whose copy the reader verifies, each measure marked
    with who republished it (the card says so). None when the state is
    blocked and no copy verifies: still a failure, never "none" — a county
    page without the booklet says nothing about the state's ballot."""
    reader = REPUBLISHED_STRATEGIES.get(strategy_key)
    for office in source.get("republished_by", []) if reader else []:
        pairs = await reader(client, year, office["url"])
        if pairs:
            logger.info("%s: %s; read the state's document as republished by %s", state, blocked, office["name"])
            return [({**parsed, "republished_by": office["name"]}, url) for parsed, url in pairs]
    logger.warning("%s: %s, and no county republication of its document verified", state, blocked)
    return None


def _stored_measures(db, state: str, election_date: str) -> list:
    return (
        db.query(BallotMeasure)
        .filter(
            BallotMeasure.state == state, BallotMeasure.election_date == election_date,
            BallotMeasure.status != "removed",
        )
        .all()
    )


def _agrees_with_stored(state: str, pairs: list[tuple[dict, str]], stored: list) -> bool:
    """Whether an archived or republished answer quotes exactly the
    measures already stored for this election. Nothing stored: nothing to disagree with (the
    reader's own checks stand alone). A capture that differs is refused:
    it may be older than the read on file, and a change of wording has to
    come from a copy that can be dated after it."""
    if not stored:
        return True
    want = sorted(tuple(getattr(m, f) or None for f in _QUOTED_FIELDS) for m in stored)
    got = sorted(tuple(p.get(f) or None for f in _QUOTED_FIELDS) for p, _ in pairs)
    if want != got:
        logger.error("%s: the archived copy's measures differ from the text stored from the state's own host — refusing", state)
        return False
    return True


async def _from_archive(
    client: httpx.AsyncClient, db, state: str, strategy_key: str, year: int, election_date: str,
) -> list[tuple[dict, str]] | None:
    reader = ARCHIVED_STRATEGIES.get(strategy_key)
    if reader is None:
        return None
    stored = _stored_measures(db, state, election_date)
    # The state's own addresses this election was read from: an earlier
    # archived read names its capture, which names the state's URL.
    known = list(dict.fromkeys(web_archive.original_url(m.source_url) for m in stored if m.source_url))
    pairs = await reader(client, year, known)
    if not pairs or not _agrees_with_stored(state, pairs, stored):
        return None
    logger.info("%s: read the state's document as archived by %s", state, web_archive.PUBLISHER)
    return [({**parsed, "republished_by": web_archive.PUBLISHER}, url) for parsed, url in pairs]


def _duplicate_ids(measures: list[dict]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for m in measures:
        if m["id"] in seen and m["id"] not in dupes:
            dupes.append(m["id"])
        seen.add(m["id"])
    return dupes


def _finish(db, state: str, year: int, measures: list[dict]) -> list[dict] | None:
    """The one exit every successful read goes through: refuse a list in
    which two measures share an id, then cache it.

    Two measures with one id is never two measures on the ballot — the
    second upsert would overwrite the first and the page would show one
    (Kentucky's unnumbered amendments were all keyed "CONSTITUTIONAL
    AMENDMENT" before this check existed). A reader that can't tell its
    measures apart hasn't read the ballot, so the state's whole answer is
    refused (ingest_failed) rather than published one measure short.

    The list itself is cached, not wrapped: api_cache_set gives an EMPTY
    payload the short EMPTY_RESPONSE_TTL_HOURS (6h), which is what makes a
    confirmed-none answer re-checked by every nightly run instead of
    pinned for CACHE_TTL_HOURS — a dict wrapping [] is not empty and got
    the full 72h.
    """
    dupes = _duplicate_ids(measures)
    numbers = [m["number"] for m in measures if m["number"]]
    dupe_numbers = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes or dupe_numbers:
        # A shared non-empty number is the same failure in another form:
        # BallotMeasure is unique on (state, election_date, number), so the
        # second insert fails and the state would publish one measure.
        logger.error(
            "Ballot measures for %s %d: %d measures share id(s) %s / number(s) %s — refusing the state's answer",
            state, year, len(measures), dupes, dupe_numbers,
        )
        return None
    # Every reader returns its measures in the state's own order (the
    # document's, or the order its index lists them); the page sorts by
    # this rather than by the printed number, which sorts as a string.
    for position, measure in enumerate(measures):
        measure["source_position"] = position
    api_cache_set(
        db, CACHE_TIER, f"{state}-{year}", measures,
        normal_ttl_hours=CACHE_TTL_HOURS,
    )
    return measures


async def fetch_state_measures_pdf(
    client: httpx.AsyncClient, db, state: str, year: int, election_date: str,
) -> list[dict] | None:
    """Every statewide ballot measure for `state`'s `year` general
    election, read directly from that state's own registered source — or
    None if `state` has no registered source/strategy at all.

    Three outcomes, never collapsed:
    - a list (possibly [] — a reader returns [] only from the state's own
      affirmative, year-bound statement that there is nothing; see each
      module's docstring);
    - None: a fetch or parse failure, including a document that exists
      but can't be read completely (ingest_failed);
    - NotYetPublished raised: the document this state publishes for the
      election isn't there yet, and nothing failed (not_yet_covered). A
      single-document source opts into that reading of a 404 / an
      undiscovered link with "absent_until_published": true in its
      registry entry — only where the state is known to post the document
      late (a sample ballot ~50 days out) or only in a year that has a
      measure.
    """
    source = source_for_state(state)
    if source is None:
        return None
    strategy_key = source["strategy"]
    strategy = STRATEGIES.get(strategy_key)
    multi_strategy = MULTI_DOCUMENT_STRATEGIES.get(strategy_key)
    national = NATIONAL_STRATEGIES.get(strategy_key)
    if strategy is None and multi_strategy is None and national is None:
        logger.error(
            "Ballot measure PDF source for %s references unknown strategy %r",
            state, strategy_key,
        )
        return None

    cache_key = f"{state}-{year}"
    cached = api_cache_get(db, CACHE_TIER, cache_key, max_age_hours=CACHE_TTL_HOURS)
    if isinstance(cached, list):
        return cached

    blocked = terms_block(state)
    if blocked and blocked.get("archive"):
        # The state's own host is never asked (its terms of use forbid
        # automated access): its document is read from the Internet
        # Archive's capture of it, with every check, or not at all.
        pairs = await _from_archive(client, db, state, strategy_key, year, election_date)
        if pairs is None:
            return None
        return _finish(db, state, year, [_to_measure(state, parsed, election_date, url) for parsed, url in pairs])

    measures = await _read_measures(client, db, state, year, election_date, source)
    if measures is None and national is None and client is not None:
        measures = await _read_archived(client, db, state, year, election_date, source)
    return None if measures is None else _finish(db, state, year, measures)


async def _read_measures(
    client: httpx.AsyncClient, db, state: str, year: int, election_date: str, source: dict,
) -> list[dict] | None:
    """The state's measures read by its registered reader through `client`
    (fetch_state_measures_pdf's outcomes, before _finish)."""
    strategy_key = source["strategy"]
    strategy = STRATEGIES.get(strategy_key)
    multi_strategy = MULTI_DOCUMENT_STRATEGIES.get(strategy_key)
    national = NATIONAL_STRATEGIES.get(strategy_key)
    if national is not None:
        try:
            pairs = await national(client, year, state, source)
        except NotYetPublished:
            raise
        except Exception:
            logger.exception("Ballot measure fetch failed for %s %d (%s)", state, year, strategy_key)
            return None
        if pairs is None:
            return None
        return [_to_measure(state, parsed, election_date, url) for parsed, url in pairs]

    if multi_strategy is not None:
        try:
            pairs = await multi_strategy(client, year)
        except (NotYetPublished, DocumentNotLinked):
            raise
        except SourceBlocked as blocked:
            pairs = await _from_republished_copy(client, state, source, strategy_key, year, blocked)
            # A county's copy is the state's document only when it quotes
            # what is stored from the state's own read word for word: a copy
            # posted before a correction must not replace the corrected text.
            if pairs is not None and not _agrees_with_stored(state, pairs, _stored_measures(db, state, election_date)):
                pairs = None
            if pairs is None:
                pairs = await _from_archive(client, db, state, strategy_key, year, election_date)
        except Exception:
            logger.exception("Multi-document ballot measure fetch failed for %s %d", state, year)
            return None
        if pairs is None:
            return None
        measures = [_to_measure(state, parsed, election_date, url) for parsed, url in pairs]
        return measures

    absent_until_published = bool(source.get("absent_until_published"))
    if "landing_page_url" in source:
        keyword = source.get("keyword")
        discover_kwargs = {}
        if isinstance(keyword, list):
            keyword = tuple(keyword)
        if "exclude" in source:
            discover_kwargs["exclude"] = tuple(source["exclude"])
        url, complete = await discover_pdf_url_checked(
            client, source["landing_page_url"], year, keyword, **discover_kwargs,
        )
        if url is None:
            if absent_until_published and complete:
                raise NotYetPublished(
                    f"{source['source_name']}: no {year} document linked from {source['landing_page_url']}",
                    deadline_applies=not source.get("absence_can_mean_none", False),
                )
            logger.warning("Could not discover current ballot measure PDF for %s %d", state, year)
            return None
        candidates = [url]
    else:
        # A list names every address the state has filed this document
        # under, newest first: Massachusetts moved its guide in 2026 from
        # .../research-and-statistics/IFV_{year}.pdf to an
        # .../information-for-voters/archive/ folder, and the landing page
        # that links it is behind a bot wall, so it can't be discovered.
        # Only a 404 moves on to the next address; any other failure is one.
        patterns = source["url_pattern"]
        if isinstance(patterns, str):
            patterns = [patterns]
        candidates = [p.format(year=year) for p in patterns]
    pdf_bytes = url = None
    for url in candidates:
        try:
            response = await client.get(url, timeout=60.0)
            response.raise_for_status()
            pdf_bytes = response.content
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            logger.warning(
                "Ballot measure PDF fetch failed for %s %d: HTTP %d",
                state, year, exc.response.status_code,
            )
            return None
        except Exception:
            logger.exception("Ballot measure PDF fetch failed for %s %d", state, year)
            return None
    if pdf_bytes is None:
        if absent_until_published:
            raise NotYetPublished(
                f"{source['source_name']}: {' / '.join(candidates)} not posted yet (404)",
                deadline_applies=not source.get("absence_can_mean_none", False),
            )
        logger.warning(
            "Ballot measure PDF fetch failed for %s %d: HTTP 404 at %s",
            state, year, " / ".join(candidates),
        )
        return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            measures = [
                _to_measure(state, parsed, election_date, url)
                for parsed in strategy(pdf.pages)
            ]
    except Exception:
        logger.exception("Ballot measure PDF parse failed for %s %d", state, year)
        return None

    return measures

async def _read_archived(
    client: httpx.AsyncClient, db, state: str, year: int, election_date: str, source: dict,
) -> list[dict] | None:
    """The state's own reader, run again on the Internet Archive's copy of
    every page it asks for (web_archive.ArchiveTransport), when the state's
    own read failed: walled, down, or moved. Nothing is sent to the state's
    host. The copy gets every check the reader applies, must quote exactly
    the measures already stored from the state's own host
    (_agrees_with_stored: a capture may be older than the read on file),
    and each measure is credited to its capture ("as archived by the
    Internet Archive on <date>"). A capture can say nothing is published
    or nothing is on the ballot only from a page the state has since
    changed, so only a read that finds measures is used; otherwise the
    state's read stays the failure it was."""
    archive = web_archive.ArchiveTransport(client)
    try:
        async with answering_client(archive) as copy:
            measures = await _read_measures(copy, db, state, year, election_date, source)
    except (NotYetPublished, DocumentNotLinked):
        return None
    if not measures:
        return None
    credited = []
    for measure in measures:
        page, _, fragment = measure["source_url"].partition("#")
        capture = archive.captures.get(page)
        if capture is None:
            logger.warning("%s: an archived measure names %s, which no capture answered — refusing", state, page)
            return None
        url = capture.page_url + (f"#{fragment}" if fragment else "")
        credited.append({**measure, "source_url": url, "republished_by": web_archive.PUBLISHER})
    stored = _stored_measures(db, state, election_date)
    if not _agrees_with_stored(state, [(m, m["source_url"]) for m in credited], stored):
        return None
    logger.info("%s: the state's own read failed; read its document as archived by %s", state, web_archive.PUBLISHER)
    return credited
