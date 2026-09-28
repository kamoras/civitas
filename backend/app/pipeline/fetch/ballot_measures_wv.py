"""West Virginia's ballot-measure strategy — the Secretary of State's
public notice of each proposed constitutional amendment.

West Virginia's Secretary of State publishes the notice the Legislature's
joint resolution directs for every amendment it submits (2026: "issued
in accordance with Senate Joint Resolution 9"). It is posted as a dated
article in the Secretary's news listing (sos.wv.gov/allnews/all), and
carries, in the resolution's own words:

    Title of Amendment: “Amendment 1: Citizenship Requirement to Vote in
    West Virginia Elections Amendment”
    Summary of Purpose: “This amendment provides that ...”

The Secretary of State has no standing per-election page listing
amendments, so the listing is how the notices are found: its rows are
newest-first and each carries a <time datetime>; rows dated in `year`
whose headline announces a Public Notice of a proposed Amendment (or
Amendments) to the Constitution are opened, and paging stops at the
first row dated before `year` (bounded by MAX_PAGES — the listing holds
60 rows a page, so the whole of a year fits in two or three; running out
of pages still inside `year` is a failure, not a search that found
nothing).

Every "Title of Amendment" in a notice is read, each with the "Summary
of Purpose" that follows it: one notice may carry several amendments.

Stored verbatim: number and title (the official ballot title) from
"Title of Amendment" (the number is the one the title itself prints —
"Amendment 1: ..."), the Summary
of Purpose as official_summary, the resolution named by the notice in
title_authority ("West Virginia Legislature (Senate Joint Resolution
9)"). The notice carries the amendment's full text too, which is not
stored. No YES/NO explanation or fiscal statement is published — null.

This source cannot establish that there are NO amendments: no notice
found after reading every `year` row raises NotYetPublished (not yet
covered, no alert), never []. A notice whose article is
not dated in `year`, or that doesn't name the General Election, is not
read.

Verified live 2026-09-28: one amendment on the November 3, 2026 ballot
(Amendment 1, citizenship requirement to vote), notice dated July 23,
2026.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.ballot_measures_state_common import get_text

logger = logging.getLogger(__name__)

BASE_URL = "https://sos.wv.gov"
LISTING_URL = BASE_URL + "/allnews/all?page={page}"
MAX_PAGES = 6
ORIGIN = "West Virginia Legislature"

# "a proposed Amendment" (2026) or "proposed Amendments" — a year with
# more than one amendment may well be noticed in one article.
_NOTICE_HEADLINE_RE = re.compile(
    r"public notices?\b.*\bamendments?\b.*\bconstitution", re.IGNORECASE,
)
_TITLE_RE = re.compile(r"Title of Amendment:\s*[“\"](.*?)[”\"]\s*$", re.DOTALL)
_SUMMARY_RE = re.compile(r"Summary of Purpose:\s*[“\"](.*?)[”\"]\s*$", re.DOTALL)
_NUMBER_RE = re.compile(r"^Amendment\s+(\d+)\s*:")
_RESOLUTION_RE = re.compile(r"in accordance with\s+((?:Senate|House) Joint Resolution\s+\d+)", re.IGNORECASE)


def listing_rows(listing_html: str) -> list[dict]:
    """[{date, headline, url}] for every article row on one listing page."""
    tree = lxml_html.fromstring(listing_html)
    rows = []
    for row in tree.xpath("//div[contains(@class,'views-row')]"):
        time = row.xpath(".//time/@datetime")
        link = row.xpath(".//a[starts-with(@href,'/article/')]")
        if not time or not link:
            continue
        headline = (link[0].get("title") or "").removeprefix("Read article:").strip()
        rows.append({"date": time[0][:10], "headline": headline, "url": urljoin(BASE_URL, link[0].get("href"))})
    return rows


def parse_notice(article_html: str, year: int) -> list[dict] | None:
    """Every amendment the notice carries — each "Title of Amendment"
    paired with the "Summary of Purpose" that follows it — or None when
    the article isn't a `year` general-election notice of the verified
    shape. Reading only the first title (as this once did) would publish
    a notice of two amendments as one."""
    tree = lxml_html.fromstring(article_html)
    h1 = clean_text(" ".join(tree.xpath("//h1//text()"))) or ""
    # Scoped to the article node: the full page carries other blocks with
    # the same field classes (verified — an unscoped body lookup picked a
    # sidebar block on the live page).
    node = tree.xpath("//div[contains(@class,'node--full')]")
    if not node:
        return None
    dated = node[0].xpath(".//div[contains(@class,'field--name-field-date')]//time/@datetime")
    if not dated or not dated[0].startswith(str(year)) or "general election" not in h1.lower():
        return None
    body = node[0].xpath(".//div[contains(@class,'field--name-body')]")
    if not body:
        return None
    resolution = _RESOLUTION_RE.search(clean_text(body[0].text_content()) or "")
    if resolution is None:
        return None
    paragraphs = [clean_text(p.text_content()) or "" for p in body[0].xpath(".//p")]

    pairs: list[list[str | None]] = []
    for p in paragraphs:
        t = _TITLE_RE.search(p)
        s = _SUMMARY_RE.search(p)
        if t and s:
            return None  # both in one paragraph: not the verified layout
        if t:
            pairs.append([clean_text(t.group(1)), None])
        elif s:
            if not pairs or pairs[-1][1] is not None:
                return None  # a summary with no title before it
            pairs[-1][1] = clean_text(s.group(1))
        elif "title of amendment" in p.lower() or "summary of purpose" in p.lower():
            return None  # a label this reader couldn't read the quote after
    if not pairs:
        return None

    results = []
    for title, summary in pairs:
        number = _NUMBER_RE.match(title or "")
        if not title or not summary or number is None:
            return None
        results.append({
            "number": number.group(1),
            "title": title,
            # The resolution's own "Title of Amendment", as the ballot
            # prints it.
            "official_title": title,
            "origin": ORIGIN,
            "official_summary": summary,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": f"West Virginia Legislature ({resolution.group(1)})",
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    notices: list[str] = []
    reached_prior_year = False
    for page in range(MAX_PAGES):
        listing = await get_text(client, LISTING_URL.format(page=page), f"WV SOS news page {page}")
        if listing is None:
            return None
        rows = listing_rows(listing)
        if not rows:
            if page == 0:
                # A first page with no article rows is a changed layout,
                # not an empty newsroom.
                logger.warning("WV SOS news listing has no article rows — layout changed?")
                return None
            reached_prior_year = True  # the archive itself ended
            break
        for row in rows:
            if row["date"].startswith(str(year)) and _NOTICE_HEADLINE_RE.search(row["headline"]):
                notices.append(row["url"])
        if min(r["date"] for r in rows) < f"{year}-01-01":
            reached_prior_year = True
            break

    if not reached_prior_year:
        # MAX_PAGES ran out while still inside `year`: the notice could be
        # on a page never read, so neither "not published" nor a list
        # built from what was read would be true.
        logger.warning("WV SOS news listing: %d pages read without reaching %d-01-01", MAX_PAGES, year)
        return None

    if not notices:
        # Every `year` article was read and none is a notice. West Virginia
        # posts one only in a year WITH an amendment, so its absence can
        # never confirm none — nor is it a failure.
        raise NotYetPublished(f"West Virginia amendment public notice for {year}")

    results: dict[str, tuple[dict, str]] = {}
    for url in notices:
        article = await get_text(client, url, "WV amendment public notice")
        if article is None:
            return None
        try:
            parsed = parse_notice(article, year)
        except Exception:
            logger.exception("WV amendment notice parse failed: %s", url)
            return None
        if parsed is None:
            logger.warning("WV amendment notice didn't match the verified shape: %s", url)
            return None
        for amendment in parsed:
            if amendment["number"] in results:
                logger.warning("WV: two notices for Amendment %s — refusing to pick one", amendment["number"])
                return None
            results[amendment["number"]] = (amendment, url)
    return [results[n] for n in sorted(results, key=int)]
