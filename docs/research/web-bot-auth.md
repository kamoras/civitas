# Signed requests: being a verified bot (Web Bot Auth)

Researched 2026-10-25. Why: Akamai Bot Manager has refused Civitas's
Senate eFD terms agreement since 2026-10-23 (httpx, curl and headless
Chromium alike), and Cloudflare walls several state election sites
(ohiosos.gov among them). A wall is the site saying no (AGENTS.md §7), and
nothing here gets past one. What a site *can* do is allow a bot it can
identify, and the way bot managers identify a bot now is a cryptographic
signature, not a User-Agent anyone can copy. This page is what that takes,
what Civitas now does (`backend/app/web_bot_auth.py`), and the owner's
remaining steps.

## The standard

- **draft-ietf-webbotauth-httpsig-protocol-00** (IETF webbotauth working
  group, Standards Track, 2026-09-01; replaces the individual
  draft-meunier-webbotauth-httpsig-protocol-02 and
  draft-meunier-web-bot-auth-architecture-05).
  <https://datatracker.ietf.org/doc/draft-ietf-webbotauth-httpsig-protocol/>
  Built on RFC 9421 (HTTP Message Signatures).
- A request carries `Signature`, `Signature-Input` and `Signature-Agent`.
  It MUST cover `@authority` (or `@target-uri`) and the `signature-agent`
  member, with `created`, `expires` (24 hours at most recommended),
  `keyid` = the key's JWK SHA-256 thumbprint (RFC 7638; RFC 8037 A.3 for
  Ed25519) and `tag="web-bot-auth"`. `nonce` is optional (RFC 9421
  §7.2.2); `@method`/`@path` may narrow it.
- `Signature-Agent` is a Dictionary in -00 (`sig1="https://…"`); the bare
  String (`"https://…"`) is the legacy form verifiers MAY accept.
- The key directory is a JWKS at
  `/.well-known/http-message-signatures-directory`, over HTTPS, as
  `application/http-message-signatures-directory+json`, with no redirect.
  Recommended (Appendix B): one signature per key on the response, covering
  `"@authority";req` and `content-digest`, `tag="http-message-signatures-directory"`.
- Test vectors: Appendix E.2 (Ed25519, RFC 9421's test-key-ed25519).
  `backend/tests/test_web_bot_auth.py` reproduces E.2.1, E.2.2 and E.2.3
  byte for byte.

## Vendors

**Cloudflare** verifies it at the edge.
<https://developers.cloudflare.com/bots/reference/bot-verification/web-bot-auth/>
Its page (read 2026-10-25) differs from -00 in one place: `Signature-Agent`
must be the bare String, "not dictionary form", which is why Civitas sends
the String (`web_bot_auth.AGENT_MEMBER`; crawltest.com parsed both on
2026-10-25). Ed25519 only; `@query-params`, `@status` and the `sf`, `bs`,
`key`, `req`, `name` parameters on a request fail; a minute's expiry is
"often sufficient". Test endpoint: `https://crawltest.com/cdn-cgi/web-bot-auth`
(400 malformed, 401 unknown key, 200 verified). Measured 2026-10-25 with a
throwaway key: unsigned 400 "missing signature…", Civitas's signer 401
"unknown public key", so the format is accepted.

**Cloudflare Verified Bots.**
<https://developers.cloudflare.com/bots/concepts/bot/verified-bots/policy/>
Requirements: honest, deterministic self-identification (Web Bot Auth, or a
published IP list with a stable User-Agent, or reverse DNS), robots.txt and
crawl directives obeyed, reasonable rates, a declared purpose that matches
the traffic. No minimum traffic and no public page are stated, though a
category can be reassigned when documentation and behaviour disagree.
Verification methods for the submission: Web Bot Auth or IP validation.
"Signed agents" became Verified on 2026-07-01 (no separate tier; BotBase
labels a bot Direct or Intermediary). Categories:
<https://developers.cloudflare.com/bots/reference/verified-bot-categories/>.
None is "civic records"; the closest are **Data Collection** (current
taxonomy) and **Academic Research** or **Aggregator** (legacy, still usable
in WAF rules). Whether a given site lets Verified bots through is its own
setting; historically they were excluded from the default bot rules.

**Akamai** verifies Web Bot Auth in Bot Manager and classifies a verified
request as validated-bot traffic in its bot directory, on which customers
set allow, challenge, delay or block.
<https://www.akamai.com/blog/security/2025/nov/redefine-trust-web-bot-authentication>
A bot operator registers at <https://www.akamai.com/lp/bot-agent-registration>
("declare your agent identity, provide your User-Agent string and public
key"). The directory has about 17 categories (a September 2026 post split
"AI Bots" into training, search and fetchers/agents:
<https://www.akamai.com/blog/security/introducing-more-granular-controls-ai-bot-traffic>);
Akamai's validated known bots "usually follow robots.txt"
(<https://techdocs.akamai.com/cloud-security/docs/detection-methods>). Its
full category list is not public; a monitoring category ("Site Monitoring
and Web Development Bots") is attested by a vendor's docs. Whether a
customer (the Senate) allows a category is the customer's choice, and none
of this is visible from outside.

Others: HUMAN (Verified AI Agents), AWS Bedrock AgentCore and Tencent
EdgeOne implement it; the draft's Appendix F lists implementations.

## Static IP and reverse DNS

Not required. Both programmes accept Web Bot Auth alone, and the key, not
the address, identifies the bot. The Pi's egress is a Verizon Business
address. A forward-confirmed reverse DNS name is still worth having, as a
second identifier some site operators check by hand and a fallback for
Cloudflare's IP validation:

1. Confirm with Verizon Business that the address is static (business
   static IP service), and note it.
2. Ask Verizon Business support for a PTR record on it, for example
   `crawler.civitas-research.org`. Most business accounts allow one PTR per
   static IP by ticket.
3. Add the matching `A` record for that name pointing at the address, in
   the domain's DNS (Cloudflare DNS, unproxied, grey cloud), so the name
   resolves forward to the same address.
4. Check: `dig -x <ip> +short` gives the name and `dig crawler.civitas-research.org +short`
   gives the address.

## What Civitas sends

Every https request the backend makes (`make_async_client`, each redirect
hop; the `signed_headers` source fetches; the Playwright eFD browser):

```
Signature-Agent: "https://civitas-research.org"
Signature-Input: sig1=("@authority" "signature-agent");created=…;keyid="<thumbprint>";alg="ed25519";expires=<created+60>;nonce="…";tag="web-bot-auth"
Signature: sig1=:…:
```

The directory is served by the backend at the well-known path (nginx routes
it), signed for the site's own domain. Setup and rotation:
`docs/operations.md` ("Signed requests").

Rates, for the applications: at most one request a second to any state or
federal site except Congress.gov (1.2/s, its published limit), the SEC (5/s,
under its 10/s policy), Voteview and congress-legislators (2/s) and the
source crawler's landing-page probes (5/s, a few per site); the FEC 0.25/s;
Senate eFD 0.5/s; the Internet Archive 0.25/s. Nightly, plus hourly news
feeds.

## Measured on eFD (2026-10-25)

One GET of `https://efdsearch.senate.gov/search/home/`, signed with a
throwaway key no one has registered: 200 from the origin (`Server:
gunicorn`), the same as unsigned. Akamai's refusal is on the terms
agreement, which was not submitted (a probe stays read-only). Nothing is
expected to change until the key is registered with Akamai and the Senate's
policy allows the category it lands in.

## Owner's checklist

Before applying: generate the key and deploy (docs/operations.md), then
confirm `https://civitas-research.org/.well-known/http-message-signatures-directory`
answers 200 with the JWK, and that `https://civitas-research.org/about/crawler`
(the crawler page, a separate change) is live. The description below says
the fetcher obeys robots.txt and backs off per host, which that same change
brings to every fetcher: submit only once it is deployed.

### 1. Cloudflare Verified Bots

1. Log in at dash.cloudflare.com (the account holding civitas-research.org).
2. Manage Account › Configurations › **Bot Submission Form** (BotBase).
3. Fill in:
   - **Bot name:** Civitas
   - **Operator:** Civitas Research (civitas-research.org), an open-source,
     noncommercial civic transparency project
   - **Category:** Data Collection (or Academic Research where offered)
   - **Verification method:** Request Signature
   - **Validation instructions / key directory URL:**
     `https://civitas-research.org/.well-known/http-message-signatures-directory`
   - **User-Agent:** the crawler's User-Agent as the crawler page states it
     (today `Mozilla/5.0 (compatible; Civitas/1.0; +<contact>)`; match
     pattern `Civitas/`)
   - **Documentation URL:** `https://civitas-research.org/about/crawler`
   - **Contact:** the instance's CONTACT_EMAIL
   - **Description** (paste):

     > Civitas fetches public government records (Congress.gov, the Senate's
     > and House's financial disclosures, the FEC, the Federal Register,
     > state election offices' candidate lists, ballot measures and results)
     > for civitas-research.org, a free, open-source site that shows how
     > members of Congress vote and are funded. Every request is signed
     > (Web Bot Auth, Ed25519) with the key at the directory above. It
     > obeys robots.txt, sends at most one request a second to a site
     > (0.5/s to Senate eFD), backs off on errors, never retries a bot
     > challenge, and runs from one machine. It does not train AI models
     > and does not resell data. Source code: github.com/kamoras/civitas.

4. Submit; follow it under BotBase › Submission history.
5. After acceptance, check `https://crawltest.com/cdn-cgi/web-bot-auth`
   answers 200 to a signed request.

### 2. Akamai bot directory

1. Open <https://www.akamai.com/lp/bot-agent-registration>.
2. Fill in the same identity: name Civitas, operator Civitas Research,
   purpose (the description above), User-Agent, public key: paste the JWK
   the key script printed (or the directory URL where a field takes one),
   documentation URL `https://civitas-research.org/about/crawler`, contact
   CONTACT_EMAIL. Ask for a research / civic-data category, not an AI one.
3. Once listed, a Senate eFD refusal may still stand: the Senate decides
   which categories it allows. If it does, the place to ask is the Senate
   Office of Public Records (the eFD site's contact page), naming the
   listing.

### 3. ISP (optional, recommended)

To Verizon Business support (paste, with the address filled in):

> Please set the reverse DNS (PTR) record for our static IP <address> to
> crawler.civitas-research.org. The forward A record for that name points
> to the same address.

Then add the A record in DNS (unproxied) and check with `dig` as above.
