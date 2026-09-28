"""Illinois's ballot-measure strategy — the State Board of Elections'
own Illinois Voters' Guide, "Questions of Public Policy" page (one of
many per-state strategies; see ballot_measures_pdf.py for the shared
contract and MULTI_DOCUMENT_STRATEGIES for why this one fetches end to
end itself).

Illinois puts two kinds of statewide question on a general-election
ballot: legislature-referred constitutional amendments and (up to three)
statewide advisory questions of public policy. The Board of Elections
publishes both, per election, on votersguide.elections.il.gov. The page
is an ASP.NET WebForms app: the election is chosen with a postback on
the home page's election dropdown (stored in the session), and
Questions.aspx then renders that election's questions. Verified live
2026-09-28 against three real elections:

  - 2026 general: "There are no statewide questions of public policy
    for this election." — the Board's own affirmative statement, the
    only thing this module turns into [] (CONFIRMED_NONE).
  - 2024 general: one link to a PDF of the three advisory questions.
  - 2022 general: one link to the "Proposed Constitutional Amendment"
    PDF (the workers' rights amendment).

No PDF parser is registered for the second and third shapes yet — they
have not recurred on a ballot this module has had to read. A page that
lists questions this module can't read returns None (ingest_failed),
never [], so a future cycle with a real question can't be rendered as
"this state has none". Re-checked every nightly run (an empty answer is
cached for only 6h — ballot_measures_pdf.CACHE_TTL_HOURS), so a question certified
late flips the state out of CONFIRMED_NONE on the next run.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_text_with_retry, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

HOME_URL = "https://votersguide.elections.il.gov/"
QUESTIONS_URL = "https://votersguide.elections.il.gov/Questions.aspx"
_ELECTION_FIELD = "ctl00$MainContent$ddlElection"

# The Board's own sentence for an election with no statewide question —
# matched loosely on whitespace, never on anything weaker. It is the only
# route to [] in this module.
_NONE_RE = re.compile(r"There\s+are\s+no\s+statewide\s+questions\s+of\s+public\s+policy\s+for\s+this\s+election", re.I)

_rate_limiter = RateLimiter(rps=1.0)


def election_option_value(home_html: str, year: int) -> str | None:
    """The dropdown value for `year`'s GENERAL election ("2026 - GENERAL
    ELECTION"), or None when the Board hasn't listed it (yet)."""
    tree = lxml_html.fromstring(home_html)
    for option in tree.xpath(f'//select[@name="{_ELECTION_FIELD}"]/option'):
        label = " ".join(option.text_content().split()).upper()
        if label == f"{year} - GENERAL ELECTION":
            return option.get("value")
    return None


def postback_form(home_html: str, option_value: str) -> dict[str, str]:
    """Every hidden WebForms field (view state, event validation, ...)
    the home page carries, plus the dropdown's own postback target —
    what the page's own onchange handler submits."""
    tree = lxml_html.fromstring(home_html)
    form = {
        el.get("name"): el.get("value") or ""
        for el in tree.xpath('//input[@type="hidden"][@name]')
    }
    form["__EVENTTARGET"] = _ELECTION_FIELD
    form["__EVENTARGUMENT"] = ""
    form[_ELECTION_FIELD] = option_value
    return form


def parse_questions_page(questions_html: str) -> list | None:
    """[] when the Board states there are no statewide questions; None for
    anything else — a listed question (no reader is registered for its
    PDF yet) or a page that isn't the questions page at all (e.g. the
    session lost the election choice and the app bounced to its home
    page). Never guesses a count."""
    tree = lxml_html.fromstring(questions_html)
    text = " ".join(tree.text_content().split())
    if "Questions of Public Policy" not in text:
        return None
    if _NONE_RE.search(text):
        return []
    links = tree.xpath('//div[@id="MainContent_pnlQuestionLink"]//a[@href]')
    if links:
        logger.warning(
            "IL Voters' Guide lists %d statewide question document(s) with no registered reader: %s",
            len(links), [a.get("href") for a in links],
        )
    return None


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    home_html = await fetch_text_with_retry(client, _rate_limiter, HOME_URL, "IL voters' guide home")
    if home_html is None:
        return None
    option_value = election_option_value(home_html, year)
    if option_value is None:
        logger.warning("IL voters' guide lists no %d general election yet", year)
        return None
    resp = await fetch_with_retry(
        client, _rate_limiter, "POST", HOME_URL, log_label="IL voters' guide election select",
        headers=BROWSER_HEADERS, data=postback_form(home_html, option_value),
    )
    if resp is None:
        return None
    questions_html = await fetch_text_with_retry(
        client, _rate_limiter, QUESTIONS_URL, "IL voters' guide questions",
    )
    if questions_html is None:
        return None
    parsed = parse_questions_page(questions_html)
    return None if parsed is None else []
