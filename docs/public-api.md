# Public API and MCP server

Moved from the README. AGENTS.md states the rules; this page keeps the longer description and the measurements behind them.

The generated reference is at [/developers](https://civitas-research.org/developers).


An open, read-only API at `/api/public/v1` — no key, no account, CORS open to
every origin — documented at [`/developers`](https://civitas-research.org/developers).

**Deprecated in v1 (2026-10), served through 2027-01-04 and not after** (`schemas.API_V1_SUNSET`): the composite fields, a member's `representationScore.overall` and `rank`, `overall` on a breakdown and on each history snapshot, and a president's `score` (the Presidential Score and its parts) and `rank`, with the matching `presidents.csv` columns. The OpenAPI schema marks them `deprecated` and their descriptions say so; every response that carries them sends `Deprecation` (RFC 9745), `Sunset` (RFC 8594) and `Link: <…/developers#deprecations>; rel="deprecation"`, exposed to browsers by CORS. Their replacements are a breakdown's `peerComparison` and each snapshot's `facts`, and for a president the record fields (`avgApproval`, `electionMargin`, `jobsCreatedMillions`, `gdpGrowthAvg`, `eoCount`, `historicalLegacyScore`), also columns of `presidents.csv`. From the day after the sunset the API stops serving them by itself: every response is passed through `public.strip_deprecated` (by the route's own response model) and the CSVs drop their deprecated columns (`_served`); every deprecated field defaults to null in its schema, so a response without it still matches the documentation, and `test_public_api_contract.py` checks both sides of the date. Deleting the code behind them is ordinary cleanup, not something the site waits on.
That page renders the OpenAPI spec at `/api/public/v1/openapi.json`, which
FastAPI generates from the routes and their response schemas
(`backend/app/api/public.py`, `app/schemas.py` "Public API v1"), so the
reference always describes the running code. `tests/test_public_api_contract.py`
validates every endpoint's real body against its documented schema (the
schemas forbid extra fields), so the two cannot drift apart. A CSV
download's entry shows its first paragraph (what the file is); the rest of
its docstring, the encoding, licence and every column (126 to 242 words),
is behind that entry's "More" (2026-10). Measured on an iPhone SE (375 px)
with the density audit's method: the page went from 44.7 screens to 39.8,
its longest block from 261 words to 77, and its reading grade from 11.2 to
8.8; the last CSV endpoint moved up from 17.4 screens to 14.6.

```
GET /api/public/v1/senators                      Serving senators, ranked by score
GET /api/public/v1/senators/{id}                 One senator's full record
GET /api/public/v1/senators/{id}/history         A senator's scores over time
GET /api/public/v1/senators/{id}/breakdown       How a senator's score is computed
GET /api/public/v1/senators/{id}/votes           Every position a senator recorded this Congress,
                                                 each with its roll call
GET /api/public/v1/representatives               Serving representatives, ranked by score
GET /api/public/v1/representatives/{id}          One representative's full record
GET /api/public/v1/representatives/{id}/history  A representative's scores over time
GET /api/public/v1/representatives/{id}/breakdown How a representative's score is computed
GET /api/public/v1/representatives/{id}/votes    Every position a representative recorded this
                                                 Congress, each with its roll call
GET /api/public/v1/bills/{id}/votes              The roll calls on a bill, with each party's split
GET /api/public/v1/search                        Floor speeches, presidential actions,
                                                 Supreme Court opinions and federal rules
GET /api/public/v1/states                        States and their member counts
GET /api/public/v1/district-lean                 Every House district's computed lean, both maps,
                                                 with Cook's PVI, method, inputs and licenses
GET /api/public/v1/presidential-support/{chamber}  How often each member votes with the
                                                 president's stated position, and the
                                                 rate expected for their seat
GET /api/public/v1/roll-calls?chamber=           A chamber's roll calls in a Congress, newest first
GET /api/public/v1/roll-calls/{chamber}/{congress}/{session}/{number}
                                                 One roll call, with every member's position
GET /api/public/v1/presidents                    Every president's record (score and rank deprecated)
GET /api/public/v1/presidents/{id}               One president's record
GET /api/public/v1/justices                      The sitting justices (not scored)
GET /api/public/v1/justices/{id}                 One justice's record
GET /api/public/v1/justices/{id}/cases           A justice's vote in each case
GET /api/public/v1/nominations                   Presidential nominations sent to the Senate
GET /api/public/v1/nominations/{congress}/{citation}  One nomination, its actions and votes
GET /api/public/v1/agencies                      Every federal agency, with its documents held
GET /api/public/v1/agencies/{slug}               One agency
GET /api/public/v1/comment-periods               Federal documents open for comment today,
                                                 soonest deadline first
GET /api/public/v1/senators.csv                  The Senate leaderboard as CSV, with each
                                                 member's scoring version and date
GET /api/public/v1/representatives.csv           The House leaderboard as CSV, likewise
GET /api/public/v1/presidents.csv                Every president's record as CSV
GET /api/public/v1/senators/{id}/votes.csv       Every position a member recorded, as CSV
GET /api/public/v1/representatives/{id}/votes.csv  (the same for a representative)
GET /api/public/v1/roll-calls/{chamber}/{congress}/{session}/{number}.csv
                                                 Every member's position on one roll call, as CSV
GET /api/public/v1/                              Index: every endpoint, weights, links
```

**License.** Civitas's own data (scores, breakdowns, classifications, the
computed lean, the API's answers and the CSVs) is CC BY 4.0; the code is
AGPL-3.0. Outputs built on a source with terms of its own carry them: the
Supreme Court record (the Supreme Court Database, CC BY-NC 3.0 US, and Oyez,
CC BY-NC 4.0) is non-commercial only, and Wikipedia-derived figures (the
election map's current polls and ranges, the district Cook PVI read from
pinned revisions) are share-alike. The statement is one component
(`frontend/src/components/about/DataLicense.tsx`) shown at `/about/data#license`
and `/developers#license`; the API names that page in the index's `license`,
the spec's `info.license` and every CSV's `Link: <...>; rel="license"` header
(`public.LICENSE_URL`). Each CSV route's columns are one list
(`_score_columns`, `_VOTE_COLUMNS`, `_POSITION_COLUMNS`, `_PRESIDENT_COLUMNS`)
that writes the file and documents it (`_csv_doc`), and a CSV is not an MCP
tool (its JSON twin is).

Conventions: lists are pages (`entries`, `total`, `page`, `perPage`,
`totalPages`); a member's `rank` is their place in the whole chamber whatever
the filters; an unknown id is a 404 (history included); every record has a
`siteUrl`; filter values (party, chamber, document type) are enumerated in
the spec and a value outside them is a 422. A score can be cited to its method: a
profile carries `algorithmVersion` and `scoredOn` (the newest history
snapshot's, the same two fields as `/breakdown`), and each `/history`
snapshot its own `algorithmVersion`, so a jump between two snapshots can be
read as a method change rather than a change in the record.

A member's `/votes` is every position they recorded on the chamber's roll
calls this Congress (`services/member_positions.py`, from `roll_call_positions`),
not the profile's selection of votes (`key_votes`, about a fifth of the
record). Each says whether it sided with the party, read as the scoring run
reads it, and `countedAsBreak` marks the breaks Constituent Alignment
counted, so the party-line figure can be checked against the record;
`chamberRollCalls` is how many roll calls the chamber held. Every roll call the API gives carries `siteUrl`, its page, and
`/roll-calls/{chamber}/{congress}/{session}/{number}` lists every member's
position on it with the id the chamber's record gives them (bioguide in the
House, LIS in the Senate) and their Civitas id where there is a page. A roll call of a Congress Civitas
doesn't hold is a 404 that says "Not held" with the Congresses held (never
"no roll call recorded"), and `/nominations` carries `backfill.complete`, false
while the daily read hasn't reached the whole Congress. The
justices' case records carry the Supreme Court Database's and Oyez's
non-commercial terms in their documentation (CC BY-NC).

A score can be reproduced from the API alone. `/breakdown` serves, for each
dimension, the stored score, the components the run that stored it worked
out (`basis: "scored"`; a score stored before they were kept is recomputed,
`basis: "recomputed"`), each with its weight, score, how it combines
(`mean`: part of the weighted mean; `points`: adds weight × (score − 50),
as Constituent approval does, with the `points` it adds) and the scorer's
sentence on how it was measured, the components' `sum` before rounding and
the 0-100 clamp and whether the clamp applied (`clamped`), and
`sinceScored` when today's records would give a different score; beside them the three weights, the scoring version and date, and
the reference values the last run measured and compared against: the
chamber's funding, Constituent Alignment and Legislative Effectiveness
references (`/data/*_reference.json`, `population_reference`), the
ideal-point fit and this member's position, the seat's Cook PVI and
district lines, and where the LES score's bills came from (`legislativeBills`:
fetched, carried from an earlier read, or unavailable). `tests/test_public_score_breakdown.py` stores fixture
members' scores with the pipeline's `calculate_scores` and recomputes every
dimension and the overall from the response: the overall exactly, each
dimension to within the one-decimal rounding of its components (on the
2026-10-14 production copy, 533 of 533 overalls exactly). The same file stores each fixture's breakdown as a run does, then marks a
bill law after the "run" and checks the served components still give the
stored score while `sinceScored` reports the new one.

A member the latest run couldn't score keeps an earlier run's score, and
`representationScore.held` says which run and why (the profile, leaderboard,
compare and the public API all carry it).

A member's id is their `first-last` name (see Phase 2 — TRANSFORM), and an id a member had
before a rename still works: the record comes back under the current id
(`id`, `siteUrl`), so a client holding an old one is not broken.

The same API is an **MCP server** at `/api/public/v1/mcp` (streamable HTTP,
stateless, JSON responses — `backend/app/api/public_mcp.py`). Its tools are
generated from the same spec, one per endpoint, named by operation id
(`list_senators`, `get_senator`, `search_documents`…), and a tool call runs
the real route in-process from the caller's IP, so answers, errors and the
rate limit are the API's own.

Rate limit: 60 requests/minute per IP (`rate_limit.PUBLIC_READ_LIMIT`), shared
by the API, MCP tool calls and the site's own Explore search. Headers:
`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (a Unix time).

CORS is open on every answer under `/api/public/v1`, errors included, and
exposes the rate-limit headers and `Retry-After` to a page's script
(`public.PublicApiPreflight`, which also answers the preflights). An MCP tool
argument sent as `null` counts as not given, as its input schema allows. An argument the
tool's input schema doesn't name is refused as a tool error naming it and the
accepted ones (`additionalProperties: false`): the route alone would drop it,
and a misnamed filter (`politician` for `politician_id`) came back unfiltered.

Use is counted per day, endpoint, channel (HTTP or MCP) and status, with
nothing about the caller (`ApiRequestCount`); a request refused as invalid
(422) also counts which parameter broke which rule (`ApiRejectionCount`),
never the value sent. The admin dashboard's API tab (`GET
/api/admin/api-usage`) shows rejected requests (the caller's mistake: an
invalid parameter, an unknown id), server errors (5xx) and rate-limit
refusals separately, and why requests were rejected — apart from the
visitor figures, since a program calling the API is not a visitor.

The Traffic tab leads with the two figures that measure people. **Confirmed
visitors** are `SiteVisit` rows from 2026-10-11 (`CONFIRMED_VISITS_SINCE`),
the first whole day a visit counted only once the browser that opened the page
had run it; earlier days show no figure, since the stored count was then mostly
automation. **First-paint loads** are cold page loads whose browser painted the
page while it was in view, from the load-time histogram; a browser under
automation (`navigator.webdriver`) reports none. That histogram is also split by
a device class the page decides for itself (desktop, tablet or mobile, from its
pointer and screen size) and sends as one of those three words, so the endpoint
still reads no header about its caller (`PageLoadTiming`).

A parameter an endpoint doesn't take is refused (422) rather than ignored,
since an ignored misspelt filter returned unfiltered results that looked
like an answer; a choice is read in any case and with hyphens or underscores
for its spaces (`Senate`, `senate-floor-speech`), a party may be named
(`Republican`) and a state written out (`Georgia`). Search's
`politician_id` takes a Civitas id or a bioguide id, and one that is neither
is a 404 rather than an empty result.

Score weights, industry codes and policy areas are also at `GET /api/config`,
the lighter endpoint the frontend itself uses.
