"""The address Civitas gives the sources it fetches from.

SEC's fair-access policy asks every automated client to name a working
contact email, and a site that wants to report a problem with our traffic
needs one that is read. Every User-Agent that names a contact takes it
from here: the pipeline's fetch modules and the maintenance scripts in
backend/scripts both import it directly (or BOT_USER_AGENT, built from it). Not every request names one: a
few state sites' firewalls refuse the "(+contact)" comment, so those
requests drop it (ballot_measures_state_common.HEADERS_NO_CONTACT and
its peers).

Standard library only, and outside app/pipeline/, on purpose: several of
those scripts run on a bare `python3` with none of the backend's packages
installed, and nothing here is analysis code.
"""

CONTACT_EMAIL = "mack.ryanm@gmail.com"

# The User-Agent for requests that name Civitas outright rather than send
# BROWSER_HEADERS' full browser shape — the "compatible; bot; +contact"
# form crawlers use, so a site's logs show who it is and how to reach us.
BOT_USER_AGENT = f"Mozilla/5.0 (compatible; Civitas/1.0; +{CONTACT_EMAIL})"

# Civitas fetching its own pages (the Bluesky link-card reader, the
# healthchecks): named as itself in the logs. It runs no page script, so it
# is never counted as a visit or an issue view (api/visits.py).
SELF_FETCH_USER_AGENT = f"Civitas-Bot/1.0 (+{CONTACT_EMAIL})"
