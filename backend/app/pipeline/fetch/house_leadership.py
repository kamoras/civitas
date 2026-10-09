"""House party leadership as the Clerk lists it (clerk.house.gov/Members).

congress-legislators, the source of every leadership title, records a change
of House leadership when a volunteer gets to it. On 2026-10-08 it still named
the previous Republican Policy Committee chair, while the Clerk's member page
and the committee's own site named the current one; the other eight posts
the Clerk lists agreed. So for the posts the Clerk lists — Speaker, the two
leaders and whips, both conference chairs, the Democratic caucus vice chair
and the Policy Committee chair — the Clerk decides who holds them. Titles
the Clerk doesn't list (an assistant leader, conference vice chairs and
secretaries, campaign committee chairs) stay as congress-legislators has
them or absent: no official list publishes them, and neither party's
conference site lists its leadership in a readable form (gop.gov has no
leadership page; dems.gov lists none), which /about/data says.

The Clerk names each leader ("Rep. Jane Q. Doe"); the name is matched to a
bioguide id by the official name in the Clerk's own MemberData.xml.
"""

import logging
import unicodedata

import httpx
from lxml import etree, html

from app.pipeline.fetch.house_clerk import MEMBER_DATA_URL
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CLERK_MEMBERS_URL = "https://clerk.house.gov/Members"
# The Clerk lists nine posts (five Republican, four Democratic); far fewer
# read means the page changed.
_MIN_POSTS = 6
_rate_limiter = RateLimiter(rps=1.0)


def _fold(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return " ".join(text.lower().replace('"', " ").split())


def parse_clerk_leadership(page: bytes | str | None) -> list[tuple[str, str]]:
    """[(name as printed, title)] from the Members page's leadership lists:
    each entry under a "... Leadership" heading is the member's name in
    bold and a link whose text is the post."""
    if not page:
        return []
    try:
        doc = html.fromstring(page)
    except (etree.ParserError, ValueError):
        return []
    posts = []
    for heading in doc.xpath("//h3[contains(normalize-space(.), 'Leadership')]"):
        for item in heading.xpath("ancestor::ul[1]/li[b and a]"):
            name = " ".join(item.findtext("b").split())
            title = " ".join(item.find("a").text_content().split())
            if name.startswith("Rep. "):
                name = name[len("Rep. "):]
            if name and title:
                posts.append((name, title))
    return posts


def official_names(member_data: bytes | None) -> dict[str, str]:
    """{folded official name: bioguide id} from the Clerk's MemberData.xml."""
    if not member_data:
        return {}
    try:
        root = etree.fromstring(member_data)
    except etree.XMLSyntaxError:
        return {}
    names = {}
    for info in root.iterfind("members/member/member-info"):
        bioguide = (info.findtext("bioguideID") or "").strip()
        if bioguide:
            names[_fold(info.findtext("official-name") or "")] = bioguide
    return names


def site_title(clerk_title: str) -> str:
    """The Clerk's post as congress-legislators words it: "Majority Leader"
    -> "House Majority Leader"; "Speaker of the House" as it is."""
    return clerk_title if "House" in clerk_title else f"House {clerk_title}"


def apply_clerk_leadership(roles: dict[str, str], posts: list[tuple[str, str]], names: dict[str, str]) -> dict[str, str]:
    """`roles` (bioguide -> title) with every post the Clerk lists given to
    the member the Clerk names. Unchanged when the page didn't read whole —
    too few posts, or a name that matches no member — rather than half
    applied."""
    resolved = [(names.get(_fold(name)), site_title(title)) for name, title in posts]
    if len(resolved) < _MIN_POSTS or any(b is None for b, _ in resolved):
        logger.warning("Clerk leadership list not applied: %d posts, %d unmatched",
                       len(resolved), sum(b is None for b, _ in resolved))
        return roles
    listed = {title for _, title in resolved}
    out = {b: t for b, t in roles.items() if t not in listed}
    out.update(resolved)
    changed = {b for b in set(roles) | set(out) if roles.get(b) != out.get(b)}
    if changed:
        logger.info("Clerk leadership changed %d member(s)' titles: %s", len(changed), ", ".join(sorted(changed)))
    return out


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    resp = await fetch_with_retry(client, _rate_limiter, "GET", url, retry_on_4xx=False, log_label=f"house leadership {url}")
    return resp.content if resp is not None else None


async def clerk_house_leadership(client: httpx.AsyncClient, roles: dict[str, str]) -> dict[str, str]:
    """`roles` with the Clerk's House posts applied. Best-effort: unchanged
    when either page can't be read."""
    page = await _fetch(client, CLERK_MEMBERS_URL)
    member_data = await _fetch(client, MEMBER_DATA_URL)
    return apply_clerk_leadership(roles, parse_clerk_leadership(page), official_names(member_data))
