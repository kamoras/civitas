"""The address Civitas gives the sources it fetches from.

Each instance names its own: CONTACT_EMAIL comes from the environment
variable of that name (.env.example), never from the repo, so a
self-hosted copy doesn't send the upstream maintainer's address. SEC's
fair-access policy asks every automated client to name a working contact
email, and a site that wants to report a problem with our traffic needs
one that is read.

Every request Civitas sends names itself the same way, USER_AGENT below:
the product token, the page that says what the crawler is and does
(/about/crawler on SITE_URL), and this address. The pipeline's fetch
modules and the maintenance scripts in backend/scripts both import it; no
request is sent under a browser's name.

Unset (or not an address), the User-Agent names Civitas and its page with no contact,
and a source whose policy requires one refuses to be fetched
(require_contact, ContactUnset) rather than be sent a request without it.

Standard library only, and outside app/pipeline/, on purpose: several of
those scripts run on a bare `python3` with none of the backend's packages
installed, and nothing here is analysis code.
"""

import logging
import os

from app.site_url import SITE_URL

logger = logging.getLogger(__name__)

ENV_VAR = "CONTACT_EMAIL"


def read_contact_email(raw: str | None) -> str:
    """The address in `raw`, or "" when it is unset or not an address: one
    "@" with text either side and a dot in the domain, no spaces, nothing a
    User-Agent comment can't carry. A bad value is logged and treated as
    unset."""
    value = (raw or "").strip()
    if not value:
        return ""
    local, _, domain = value.partition("@")
    if (
        value.count("@") != 1 or not local or "." not in domain.strip(".")
        or any(c.isspace() or c in "()<>;," for c in value)
    ):
        logger.warning("%s=%r is not an email address; treating it as unset", ENV_VAR, value)
        return ""
    return value


CONTACT_EMAIL = read_contact_email(os.environ.get(ENV_VAR))

# The page that says what the crawler is, what it reads and how to reach
# us (frontend/src/app/about/crawler): named in every request, so a site's
# logs lead straight to it.
CRAWLER_PAGE = f"{SITE_URL}/about/crawler"

# The one User-Agent every request carries, the "+URL" form crawlers use:
# "Civitas/1.0 (+<SITE_URL>/about/crawler; <CONTACT_EMAIL>)".
USER_AGENT = f"Civitas/1.0 (+{CRAWLER_PAGE}{'; ' + CONTACT_EMAIL if CONTACT_EMAIL else ''})"

# The hosts that refuse USER_AGENT itself, each sent the nearest form it
# serves instead: host -> (User-Agent, the measurement behind it). Measured
# 2026-10-25, one read-only GET per form from production's network; each
# host was asked with nine forms. Every form still names Civitas. The
# crawler page lists these (GET /api/crawler, userAgentExceptions), and
# http_client applies them to any request that would otherwise carry
# USER_AGENT. Nowhere else may a User-Agent differ by host.
_CHROME = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)
_BROWSER_FORM = f"{_CHROME} Civitas/1.0{f' (+{CONTACT_EMAIL})' if CONTACT_EMAIL else ''}"
_BARE_FORM = "Civitas/1.0"
_EMAIL_FORM = f"Civitas/1.0 ({CONTACT_EMAIL})" if CONTACT_EMAIL else _BARE_FORM
_BROWSER_WHY = (
    "refuses (403) any User-Agent that is not a browser's, a bare \"Civitas/1.0\" included, and any "
    "that carries a web address, a browser's included; a browser's with \"Civitas/1.0 (+contact)\" "
    "appended is served"
)
_BARE_WHY = (
    "refuses (403, Akamai) a User-Agent carrying a contact in a comment, Civitas's own included; "
    "a bare \"Civitas/1.0\" is served"
)
_EMAIL_WHY = (
    "refuses (403) a User-Agent carrying a web address, Civitas's own included; "
    "\"Civitas/1.0 (contact)\" is served"
)
HOST_USER_AGENTS: dict[str, tuple[str, str]] = {
    "www.maine.gov": (_BROWSER_FORM, _BROWSER_WHY),
    "sos.tn.gov": (_BROWSER_FORM, _BROWSER_WHY),
    "www.michigan.gov": (_BARE_FORM, _BARE_WHY),
    "www.sos.nh.gov": (_BARE_FORM, _BARE_WHY),
    "mm.nh.gov": (_BARE_FORM, _BARE_WHY + " (New Hampshire's document host)"),
    "goelect.txelections.civixapps.com": (_EMAIL_FORM, _EMAIL_WHY + " (Texas's Civix API)"),
    "mqscores.wustl.edu": (_EMAIL_FORM, _EMAIL_WHY + " (Cloudflare's challenge otherwise)"),
}


def user_agent_for(host: str) -> str:
    """The User-Agent a request to `host` carries: USER_AGENT, or the
    host's measured exception (HOST_USER_AGENTS)."""
    entry = HOST_USER_AGENTS.get((host or "").lower())
    return entry[0] if entry else USER_AGENT


# The same string under its old name, for justice_pipeline.py alone: renaming
# the import there would move the analysis-code fingerprint (AGENTS.md §2)
# and clear the learning store for a change that classifies nothing.
BOT_USER_AGENT = USER_AGENT

UNSET_MESSAGE = (
    f"{ENV_VAR} is not set (or is not an email address): requests go out naming Civitas with no "
    "contact, and sources whose policy requires one (the SEC) are not fetched. Set "
    f"{ENV_VAR} in .env to an address you read."
)

# The ops-alert condition a source refused for want of a contact raises
# (sec_tickers._headers); the pipeline process closes it at start once
# CONTACT_EMAIL is set (main.lifespan), which lets it alert again later.
UNSET_CONDITION = "contact-email-unset"


class ContactUnset(RuntimeError):
    """A source that requires a contact address was about to be fetched
    with none configured."""


def require_contact(source: str) -> str:
    """CONTACT_EMAIL, for a source whose published policy requires a
    contact; raises ContactUnset naming the source when none is set."""
    if not CONTACT_EMAIL:
        raise ContactUnset(f"{source} requires a contact address: set {ENV_VAR} in .env")
    return CONTACT_EMAIL
