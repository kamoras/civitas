"""The address Civitas gives the sources it fetches from.

Each instance names its own: CONTACT_EMAIL comes from the environment
variable of that name (.env.example), never from the repo, so a
self-hosted copy doesn't send the upstream maintainer's address. SEC's
fair-access policy asks every automated client to name a working contact
email, and a site that wants to report a problem with our traffic needs
one that is read.

Every User-Agent that names a contact takes it from here: the pipeline's
fetch modules and the maintenance scripts in backend/scripts both import
it directly (or BOT_USER_AGENT and its peers, built from it). Not every
request names one: a few state sites' firewalls refuse the "(+contact)"
comment, so those requests drop it
(ballot_measures_state_common.HEADERS_NO_CONTACT and its peers).

Unset (or not an address), the User-Agents name Civitas with no contact,
and a source whose policy requires one refuses to be fetched
(require_contact, ContactUnset) rather than be sent a request without it.

Standard library only, and outside app/pipeline/, on purpose: several of
those scripts run on a bare `python3` with none of the backend's packages
installed, and nothing here is analysis code.
"""

import logging
import os

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

# The contact as a User-Agent comment, or nothing when none is set: " (+a@b)"
# after a product token, "; contact: a@b" inside a comment that has a
# purpose of its own.
CONTACT_COMMENT = f" (+{CONTACT_EMAIL})" if CONTACT_EMAIL else ""
CONTACT_NOTE = f"; contact: {CONTACT_EMAIL}" if CONTACT_EMAIL else ""

# The User-Agent for requests that name Civitas outright rather than send
# BROWSER_HEADERS' full browser shape — the "compatible; bot; +contact"
# form crawlers use, so a site's logs show who it is and how to reach us.
BOT_USER_AGENT = f"Mozilla/5.0 (compatible; Civitas/1.0{'; +' + CONTACT_EMAIL if CONTACT_EMAIL else ''})"

# Civitas fetching its own pages (the Bluesky link-card reader, the
# healthchecks): named as itself in the logs. It runs no page script, so it
# is never counted as a visit or an issue view (api/visits.py).
SELF_FETCH_USER_AGENT = f"Civitas-Bot/1.0{CONTACT_COMMENT}"

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
