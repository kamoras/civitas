# Platform review, round 3 — 2026-07 (explore search, security, House-PTR/LLM stack)

Third audit pass over subsystems the first two rounds didn't reach:
the Explore semantic-search read path, the platform's security posture,
and the House-PTR / SEC-ticker / LDA / LLM-client stack. Same environment
caveat as before (live site + most external sources unreachable). Findings
that were **fixed in the accompanying PR** are marked ✅; those that need
deploy-topology verification, a data/ops migration, or live calibration
are marked ⏭ with the reason.

---

## Explore semantic search

- ✅ **Public `/search` chamber filter never matched (HIGH).** The pattern
  allowed only lowercase `senate|house`, but ChromaDB stores `Senate`,
  `House`, `Executive`, `Judicial`, `Regulatory` and its `where` equality
  is case-sensitive — so the filter always returned zero and three
  chambers were unreachable. Now maps input to canonical casing and
  accepts all five chambers.
- ✅ **`politician_id` post-filtered over top-k (HIGH).** A member-scoped
  search fetched the global top-k and intersected by politician in SQL, so
  a broad query whose top hits weren't that member returned near-empty.
  Now pushed into the vector `where` clause (both `/explore` and public
  `/search`).
- ✅ **Empty/missing index shown as "no matches" (HIGH).** After an admin
  reset (before the next pipeline run) the collection doesn't exist;
  `search_explore_documents` returned `[]`, and the UI told users "No
  results found — try a broader search" while the stats header claimed
  thousands of docs. The function now returns `None` for a missing index
  and both endpoints surface HTTP 503 `indexEmpty: true`.
- ✅ **Commentable filter starved (MEDIUM).** Only Regulatory docs have a
  comment URL, but the filter 3×-oversampled a mixed top-k and post-
  filtered. Now scopes the vector query to `chamber=Regulatory` when
  `commentable` is set with no explicit chamber.
- ✅ **Partial-reset orphans rendered broken cards (LOW).** Vector hits
  with no surviving DB row (DB cleared, Chroma reset swallowed) rendered
  as snippet-only cards whose detail link 404s. Both endpoints now drop
  hits absent from the SQL enrichment map.
- ✅ **Public `/search` doc_type docs advertised non-existent values
  (LOW).** The docstring listed `bill`/`lobbying`/`federal_register`, none
  of which are real `doc_type` values, so any consumer following the docs
  got silent empty results. Docstrings corrected and `doc_type` is now
  validated against the real value set (422 on unknown).
- ⏭ **No rate limit on `GET /explore` despite per-request model inference
  (MEDIUM).** Deliberately not fixed here: the app-level limiter collapses
  to a single global bucket in the Swarm deployment (see Security below),
  so adding one now would throttle all users, not the abuser. Gated on the
  IP-recovery fix.
- ⏭ **Implicit L2 distance space (MEDIUM/LOW).** Collections are created
  without `hnsw:space`, so ranking is cosine-correct only because the
  model normalizes; a future non-normalizing model swap would silently
  corrupt ranking. Fix (`metadata={"hnsw:space":"cosine"}`) requires a
  one-time reindex — an ops action, not a code-only change.
- ⏭ **Legacy duplicate rows (MEDIUM)** and **frozen comment-period metadata
  (LOW).** Both need a data migration / dedupe-hit update pass; documented
  for a dedicated ops PR.

Verified sound: asymmetric query-prompt encoding is used correctly; all
date fields are ISO strings so string comparisons are valid; ChromaDB
`where` values can't inject operators; the frontend deadline handling
re-checks close dates client- and server-side.

---

## Security posture

- ✅ **Feedback form injected unsanitized visitor text into GitHub issue
  bodies (MEDIUM).** Markdown rendering meant `@mentions` notified
  arbitrary users, `[text](url)` embedded phishing links, and
  `![](tracker)` acted as a load-on-render beacon in the maintainer's own
  tracker. The message is now wrapped in a fenced code block (fence sized
  past any backtick run so it can't be broken out of); the page URL has
  `@`/`#` neutralized. The email is left verbatim (regex-validated; its
  `@` isn't at a word boundary, so GitHub doesn't linkify it).
- ✅ **`/track-visit` disk-fill via User-Agent rotation (LOW-MED).** The
  daily-dedup key mixed in the User-Agent, so one IP rotating the header
  produced unlimited distinct rows. Dropped UA from the dedup key —
  daily rows are now bounded by distinct source IPs (the actual
  unique-visitor unit); browser/OS/device breakdowns still come from the
  raw UA. (An app-level rate limit was considered and rejected for the
  same Swarm IP-collapse reason as the explore endpoint.)
- ⏭ **Per-IP rate limiting collapses to one global bucket in Swarm
  (MEDIUM).** In production nginx reaches the backend over the overlay
  network, so `request.client.host` is nginx's overlay IP for every
  request and `_TRUSTED_PROXIES = {127.0.0.1, ::1}` never trusts
  `X-Forwarded-For`. Both limiters key on that one value → global, not
  per-IP. No spoofing bypass exists (XFF is ignored), but the documented
  per-IP model isn't in effect and nginx's own `limit_req` is the only
  real per-IP layer. **Recommended fix (needs deploy verification, so not
  shipped blind):** add nginx `set_real_ip_from <overlay CIDR>` +
  `real_ip_header X-Forwarded-For`, and extend the backend's trusted-proxy
  set to the overlay range. This is the prerequisite for the two skipped
  rate-limit items above.
- ⏭ **`chromadb==0.4.24` outdated pin (LOW).** The only 2024-vintage pin
  amid otherwise-current deps and the likely subject of the open
  Dependabot high-severity alert. Bump once the target version is
  confirmed against the alert (dependency change, verify build).
- ⏭ **Visitor-hash salt falls back to constant `"civitas"` (LOW).** Only
  when both `ADMIN_TOKEN` and `PIPELINE_TRIGGER_TOKEN` are unset (contrary
  to `.env.example`, which requires `ADMIN_TOKEN`). Low impact; a
  require-a-real-secret change is a config-policy decision.

Verified sound (no action): admin/pipeline auth uses `secrets.compare_digest`
and fails **closed** (503 when no token configured, never open); no
internal leakage on errors (no debug mode, generic 500s, admin details are
fixed strings); SSRF surface is low (`_resolve_url` follows redirects only
for pipeline-fetched Google-News RSS URLs, not user input); CORS is
conservative (`allow_credentials=False`, explicit origins, GET/POST only);
containers drop root and under Swarm only nginx publishes a host port; DB
access is fully ORM-parameterized; nginx `/api/admin/` is IP-restricted on
top of the token check.

---

## House-PTR / SEC-ticker / LDA / LLM stack

- ✅ **LDA lobbying spend never followed pagination (HIGH).** Only the
  first 25 filings were summed, systematically **undercounting the
  heaviest-lobbying orgs** (dozens–hundreds of filings/year) — and the
  figure is shown verbatim in user-facing text. Now follows the API's
  `next` links (bounded to 10 pages, logging if the cap is hit so a
  truncation can't masquerade as a complete total). Also fixed the
  cache-key collision where two orgs sharing an 80-char prefix mapped to
  one cached figure (now suffixed with a hash of the full key).
- ✅ **Explore doc-summary LLM cache silently disabled (HIGH).** The
  endpoint passed a versioned `cache_key` but no `db_session`, and
  `call_llm` caches only when both are set — so every summary view past
  the 30 s cooldown re-ran a full generation and serialized on the single
  LLM backend. Now passes `db_session`.
- ✅ **House filer→member matching ignored the district (MEDIUM-HIGH).**
  It filtered on state only, leaving same-state same-surname pairs to a
  fragile first-name substring match that silently skipped the filing
  every run when the formal name differed from the display name ("Michael"
  vs "Mike"). Now filters on the full district the FD index already
  supplies, making the match exact for all 435 seats.
- ✅ **Stock pipeline lacked a rollback between chambers (LOW-MED).** A
  House flush error left the session in a failed-transaction state, so the
  Senate phase's first query raised `PendingRollbackError` — the
  "best-effort per chamber" design failed **both**. Each chamber's except
  block now rolls back.
- ✅ **`extract_json` greedy-alternation poison prefix (LOW-MED).** A stray
  `[` before a real JSON object made the match run from that bracket to
  the last `]` inside the object and fail with no fallback. Now tries the
  object and array spans independently. Also strips an unterminated
  trailing `<think>` block (truncated reasoning traces).
- ✅ **SEC ticker map frozen for the process lifetime (LOW-MED).** The
  module-global cache bypassed its own 7-day DB TTL, so in the long-lived
  scheduler process new tickers resolved to nothing until restart. Now
  re-consults the DB path after 7 days.
- ✅ **Dead promise-* prompts removed (LOW).** Three prompt builders with
  no callers (promise tracking was removed) still interpolated raw scraped
  platform text, re-exposing an injection surface to any future caller.
  Deleted.
- ⏭ **`num_ctx` is a no-op on the default llama-server backend, and the
  overflow "mitigation" logs a bump that never happens (MEDIUM).** The fix
  (truncate the prompt to the token budget on that branch) needs a token
  count against the real tokenizer to size correctly — deferred to avoid a
  guessed constant.
- ⏭ **Parse-failure retries re-issue an identical temperature-0 request
  (MEDIUM).** Deterministic backends return the same unparseable output
  three times (~worst-case 30 min blocked). The clean fix (don't retry
  parse failures at temp 0, or perturb the seed) interacts with the
  caching/determinism contract; flagged for a focused change.

Verified sound: House PTR shares **none** of the Senate permanent-loss
anchors (DB-derived filing_id dedup, failed fetches retried not cached,
empty-payload cache protection); SEC UA compliance; LDA 429s deliberately
not cached; `cross_reference`'s narrative/promise LLM paths were already
removed (2026-07), so that platform-text injection surface no longer
exists.
