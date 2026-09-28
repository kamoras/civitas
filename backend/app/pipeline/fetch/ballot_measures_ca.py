"""California's ballot-measure strategy — the Secretary of State's
Official Voter Information Guide, read as HTML (one of the
MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py: an index page plus
two pages per proposition).

Why HTML and not the guide's PDF: this module used to read the PDF's
"Quick Reference Guide" pages, and the 2026 general guide broke that in a
way no parser can fix. Its text layer maps the fi/fl/ff ligature glyphs
to a bare "f" — the extractable text literally reads "infation",
"fnance", "efect" — so quoting it would put words on the page the state
never wrote. The same guide is published as HTML at
voterguide.sos.ca.gov, whose text is real characters, and the Secretary
keeps past guides at vigarchive.sos.ca.gov/<year>/general/. The PDF
reader is gone: the HTML covers every guide it was verified on.

Discovery, for `year` (never a hardcoded election): the current guide
(voterguide.sos.ca.gov) is used when its banner names "General Election"
and `year`'s election day ("November 3, 2026"); otherwise the archive's
<year>/general/ guide, whose banner must name it the same way. A current
guide for some other election (the June primary) and no archived guide
for `year` (404) means this general's guide isn't posted yet:
NotYetPublished (not yet covered). A page that can't be fetched is a
failure (None).

Per proposition, all verbatim:
- the guide's index (/propositions/) lists every proposition on the
  ballot; each is read, and the number read must equal the number the
  index lists or the state is refused (None);
- "Official Title and Summary" (propositions/<n>/title-summary.htm),
  under the page's own "PREPARED BY THE ATTORNEY GENERAL" heading: the
  title (official_title — the ballot label title) and the bulleted
  summary (official_summary), title_authority the Attorney General; then,
  under "SUMMARY OF LEGISLATIVE ANALYST'S ESTIMATE OF NET STATE AND LOCAL
  GOVERNMENT FISCAL IMPACT", the fiscal bullets (fiscal_impact),
  fiscal_authority the Legislative Analyst's Office. The page must name
  both drafters itself, or it is refused.
- the proposition's Quick Reference page (propositions/<n>/): the
  state's own "A YES vote on this measure means: ..." / "A NO vote ..."
  sentences (yes_means / no_means: the text after "means:", which the
  card labels "A YES VOTE" / "A NO VOTE"), and "Put on the Ballot by
  ..." (origin).

Each page must carry the proposition's own number (its "PROP <n>" badge)
and the same title on both pages. Bullets are kept one per line ("• ",
and "– " for a list nested inside an item, as the page nests them) —
layout, not wording. Anything missing refuses the whole state.

Verified live 2026-09-28: all 14 propositions on the November 3, 2026
guide (1-5, 37-45), and all 10 on the archived November 5, 2024 guide
(2-6, 32-36).
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_text,
    get_text_or_missing,
    long_date,
)

logger = logging.getLogger(__name__)

CURRENT_GUIDE = "https://voterguide.sos.ca.gov/"
ARCHIVE_GUIDE = "https://vigarchive.sos.ca.gov/{year}/general/"
TITLE_AUTHORITY = "California Attorney General"
FISCAL_AUTHORITY = "California Legislative Analyst's Office"

_PROP_LINK_RE = re.compile(r"/propositions/(\d+)/(?:index\.htm)?$")
_MEANS_RE = re.compile(r"^(YES|NO)\s+A (YES|NO) vote on this measure means\s*:\s*(.+)$", re.DOTALL)
_PUT_ON_RE = re.compile(r"^Put on the Ballot by\s+(.+)$", re.IGNORECASE)
_LAO_HEADING_RE = re.compile(r"LEGISLATIVE ANALYST.S ESTIMATE .*FISCAL IMPACT", re.IGNORECASE)


def _text(el) -> str:
    return clean_text(el.text_content()) or ""


def names_general_election(page_html: str, year: int) -> bool:
    """Whether the guide's own banner names `year`'s general election."""
    tree = lxml_html.fromstring(page_html)
    banner = tree.xpath("//div[@id='txtBnr']")
    text = _text(banner[0]) if banner else ""
    return "General Election" in text and long_date(election_day(year)) in text


def proposition_links(index_html: str, index_url: str) -> dict[str, str] | None:
    """{number: proposition page url} from the guide's index, in page
    order; None when the index lists nothing it can read (a guide page
    without its proposition list is not a guide with no propositions)."""
    tree = lxml_html.fromstring(index_html)
    items = tree.xpath("//div[@id='mainCont']//ul[contains(@class,'contentNav')]/li")
    links: dict[str, str] = {}
    for li in items:
        a = li.xpath("./a[@href]")
        m = _PROP_LINK_RE.search(a[0].get("href").split("?")[0]) if a else None
        if m is None or m.group(1) in links:
            logger.warning("CA guide index entry %r isn't a proposition link this reader knows", _text(li)[:80])
            return None
        links[m.group(1)] = urljoin(index_url, a[0].get("href"))
    return links or None


def _prop_badge(tree) -> tuple[str | None, str | None]:
    """(number, title) from the page's own "PROP <n>" badge and name."""
    num = tree.xpath("//div[@id='mainCont']//span[@id='propNum']")
    name = tree.xpath("//div[@id='mainCont']//div[contains(@class,'propName')]//h2")
    return (
        (_text(num[0]) or None) if len(num) == 1 else None,
        (_text(name[0]) or None) if len(name) == 1 else None,
    )


def _bullets(ul) -> str | None:
    """A bulleted list as one line per item — "• " for an item, "– " for
    an item of a list nested inside it — words untouched."""
    lines: list[str] = []
    for li in ul.xpath("./li"):
        own_parts = [li.text or ""]
        for child in li:
            if child.tag != "ul":
                own_parts.append(child.text_content())
            own_parts.append(child.tail or "")
        own = clean_text(" ".join(own_parts))
        if own:
            lines.append(f"• {own}")
        for sub_li in li.xpath("./ul/li"):
            t = _text(sub_li)
            if t:
                lines.append(f"– {t}")
    return "\n".join(lines) or None


def parse_title_summary(page_html: str, number: str) -> dict | None:
    """{title, summary, fiscal} from a proposition's Official Title and
    Summary page, or None when it isn't that page for `number` or either
    drafter's section is missing."""
    tree = lxml_html.fromstring(page_html)
    badge, title = _prop_badge(tree)
    main = tree.xpath("//div[@id='mainCont']")
    if badge != number or not title or not main:
        return None
    heads = [_text(h) for h in main[0].xpath(".//div[contains(@class,'titleSumPrepared')]//h3")]
    if "OFFICIAL TITLE AND SUMMARY" not in heads or "PREPARED BY THE ATTORNEY GENERAL" not in heads:
        return None
    lists = main[0].xpath(".//ul[contains(@class,'blts')][not(ancestor::ul)]")
    lao = [h for h in main[0].xpath(".//h3") if _LAO_HEADING_RE.search(_text(h))]
    if len(lists) != 2 or len(lao) != 1:
        return None
    summary_ul, fiscal_ul = lists
    # The LAO heading sits between the two lists, directly above its own.
    if lao[0].getnext() is not fiscal_ul:
        return None
    summary, fiscal = _bullets(summary_ul), _bullets(fiscal_ul)
    if not summary or not fiscal:
        return None
    return {"title": title, "summary": summary, "fiscal": fiscal}


def parse_quick_reference(page_html: str, number: str) -> dict | None:
    """{title, origin, yes, no} from a proposition's Quick Reference page,
    or None when it isn't that page for `number` or either vote sentence
    is missing."""
    tree = lxml_html.fromstring(page_html)
    badge, title = _prop_badge(tree)
    if badge != number or not title:
        return None
    put_on = [
        m.group(1) for h in tree.xpath("//div[@id='mainCont']//h3[contains(@class,'preparedBy')]")
        if (m := _PUT_ON_RE.match(_text(h)))
    ]
    means: dict[str, str] = {}
    for p in tree.xpath("//div[@id='mainCont']//p[span[contains(@class,'yesNoProCon')]]"):
        m = _MEANS_RE.match(_text(p))
        if m is None:
            continue  # the PRO / CON arguments share the label class
        if m.group(1) != m.group(2) or m.group(1) in means:
            return None
        means[m.group(1)] = clean_text(m.group(3))
    if set(means) != {"YES", "NO"} or len(put_on) != 1:
        return None
    return {"title": title, "origin": clean_text(put_on[0]), "yes": means["YES"], "no": means["NO"]}


def combine(number: str, title_summary: dict, quick: dict) -> dict | None:
    if title_summary["title"] != quick["title"]:
        return None
    return {
        "number": number,
        "title": title_summary["title"],
        # The Attorney General's ballot label title, printed on the ballot.
        "official_title": title_summary["title"],
        "origin": quick["origin"],
        "official_summary": title_summary["summary"],
        "fiscal_impact": title_summary["fiscal"],
        "yes_means": quick["yes"],
        "no_means": quick["no"],
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": FISCAL_AUTHORITY,
    }


async def find_guide(client: httpx.AsyncClient, year: int) -> str | None:
    """The guide root for `year`'s general election. Raises
    NotYetPublished when the current guide is for another election and
    the archive has none for `year`; None when a page couldn't be read."""
    current = await get_text(client, urljoin(CURRENT_GUIDE, "propositions/"), "CA current voter guide")
    if current is None:
        return None
    if names_general_election(current, year):
        return CURRENT_GUIDE
    archive = ARCHIVE_GUIDE.format(year=year)
    archived, missing = await get_text_or_missing(
        client, urljoin(archive, "propositions/"), f"CA {year} archived voter guide",
    )
    if archived is not None and names_general_election(archived, year):
        return archive
    if missing:
        raise NotYetPublished(f"California's Official Voter Information Guide for the {year} general election")
    logger.warning("CA %d: archived guide unreachable or not this election's", year)
    return None


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    guide = await find_guide(client, year)
    if guide is None:
        return None
    index_url = urljoin(guide, "propositions/")
    index_html = await get_text(client, index_url, f"CA {year} propositions index")
    if index_html is None:
        return None
    try:
        if not names_general_election(index_html, year):
            return None
        links = proposition_links(index_html, index_url)
    except Exception:
        logger.exception("CA %d propositions index was not parseable", year)
        return None
    if links is None:
        return None

    results: list[tuple[dict, str]] = []
    for number, prop_url in links.items():
        summary_url = urljoin(prop_url, "title-summary.htm")
        quick_html = await get_text(client, prop_url, f"CA Prop {number}")
        summary_html = await get_text(client, summary_url, f"CA Prop {number} title and summary")
        if quick_html is None or summary_html is None:
            return None
        try:
            ts = parse_title_summary(summary_html, number)
            quick = parse_quick_reference(quick_html, number)
            parsed = combine(number, ts, quick) if ts and quick else None
        except Exception:
            logger.exception("CA Prop %s pages were not parseable", number)
            return None
        if parsed is None:
            logger.warning("CA Prop %s pages didn't match the verified shape — refusing the guide", number)
            return None
        results.append((parsed, summary_url))
    # Fail-closed completeness: every proposition the guide's own index
    # lists was read (the loop above returns None on any that isn't).
    if [p["number"] for p, _ in results] != list(links):
        return None
    return results
