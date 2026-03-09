# Data Quality Analysis: Missing Campaign Promises, Sponsored Bills, and Platform Summaries

This document analyzes why ~25/100 senators lack campaign promises, ~25/100 lack sponsored bills, and ~12/100 lack platform summaries. All code references use absolute paths under `/mnt/nvme/modern-punk/backend/app/pipeline/`.

---

## 1. Campaign Promises (25/100 senators missing)

### Data Flow

Campaign promises are **not** fetched from an external API. They are **derived** from two sources via embedding-based classification:

1. **Sponsored bills** (primary) → `_positions_from_sponsored_bills()`
2. **Scraped platform text** (supplementary) → `_positions_from_platform_text()`

**Source:** `analyze/cross_reference.py`

### Why Promises Are Missing

#### 1a. No sponsored bills AND no usable platform text

`_compute_promise_alignments()` (lines 385–408) combines both sources:

```python
positions = _positions_from_sponsored_bills(sponsored_bills or [], all_votes)
platform_positions = _positions_from_platform_text(platform_text, all_votes, existing_topics)
positions.extend(platform_positions)
return positions
```

If both return empty, the senator gets **zero campaign promises**.

#### 1b. Sponsored bills filtered out as non-substantive

`_positions_from_sponsored_bills()` (lines 262–318) applies strict filters:

| Filter | Location | Effect |
|--------|----------|--------|
| `b.get("title")` | Line 283 | Bills with no title skipped |
| `(policyArea or "").upper() not in ("PROCEDURAL", "")` | Line 284 | Procedural bills skipped |
| `len(b.get("title", "")) > 15` | Line 285 | Short titles (e.g. "S.123", "Jaime's Law") skipped |
| Deduplication by policy area | Lines 291–295 | Only one bill per policy area kept |
| `max_positions=6` | Line 298 | At most 6 positions from sponsored bills |

**Code reference:** `analyze/cross_reference.py:281–298`

Senators who only sponsor simple resolutions (naming post offices, commemorative bills, etc.) or bills with short/empty titles will have **no positions from sponsored bills**.

#### 1c. Platform text fails quality checks

`_positions_from_platform_text()` (lines 321–380) requires:

| Check | Location | Effect |
|-------|----------|--------|
| Non-empty `platform_text` | Line 333 | Empty → return [] |
| No error page signatures | Line 336 | 404/error pages → return [] |
| Scrape artifact cleanup leaves ≥200 chars | Lines 339–343 | Heavy nav/boilerplate → return [] |
| `_extract_platform_topics()` returns topics | Lines 344–346 | No topics → return [] |
| `max_positions=4` | Line 325 | At most 4 from platform text |

`_extract_platform_topics()` (lines 118–143) further filters:

- Lines must be >20 chars, >4 words
- Excluded by `_TOPIC_SKIP_RE`: "Home", "About", "Contact", "Committee Assignments", etc.
- Excluded by `_NAV_JUNK_RE`: nav-like patterns
- Excluded by `_ERROR_PAGE_SIGS`
- `max_topics=6` (or `max_positions+2` when called from platform text)

**Code reference:** `analyze/cross_reference.py:95–143, 321–346`

Senators whose platform text is empty, error pages, or mostly boilerplate get **no positions from platform text**.

#### 1d. Summary: Campaign promise gaps

- **~25 senators** likely have:
  - No sponsored bills (see Section 2), **or**
  - Only non-substantive sponsored bills (procedural/short titles), **and**
  - No usable platform text (scrape failed or failed quality checks)

---

## 2. Sponsored Bills (25/100 senators missing)

### Data Flow

Sponsored bills are fetched from **Congress.gov** via `fetch_member_sponsored()`.

**Source:** `fetch/congress.py:214–228`

### Why Sponsored Bills Are Missing

#### 2a. Hardcoded limit: 50 bills per senator

```python
f"{CONGRESS_API_BASE}/member/{bioguide_id}/sponsored-legislation?limit=50"
```

**Code reference:** `fetch/congress.py:225`

- Only the first **50** sponsored bills are returned.
- Senators with >50 sponsorships are truncated, but still have bills.
- This limit does **not** explain senators with **zero** sponsored bills.

#### 2b. API returns empty for some senators

The Congress.gov API can return an empty list when:

- The senator has **never sponsored** any legislation (e.g. very new members).
- The member record has no sponsored legislation in the API’s index.
- Transient API errors (we cache results, so errors can persist).

**Code reference:** `fetch/congress.py:226–227`

```python
results = (data or {}).get("sponsoredLegislation", [])
api_cache_set(db, "congress", cache_key, results)
```

Empty results are cached; a failed or empty response will persist until cache invalidation.

#### 2c. Missing bioguideId

If `bioguideId` is missing, the fetch is skipped:

```python
bio_id = senator.get("bioguideId", "")
if bio_id:
    raw_sponsored = await fetch_member_sponsored(client, db, bio_id)
    if raw_sponsored:
        sponsored_map[bio_id] = raw_sponsored
```

**Code reference:** `orchestrator.py:931–936`

Senators without `bioguideId` never get sponsored bills fetched.

#### 2d. Bills dropped during prepare (title required)

During prepare, bills without a title are skipped:

```python
for sp in raw_sponsored:
    title = sp.get("title", "")
    if not title:
        continue
```

**Code reference:** `orchestrator.py:1367–1369`

If the API returns bills with empty/missing titles, they are dropped. This is uncommon but possible.

#### 2e. No pagination

The fetch uses `limit=50` only. There is **no `offset`** or pagination. Senators with more than 50 sponsored bills only have the first 50 represented.

**Code reference:** `fetch/congress.py:223–226`

#### 2f. Summary: Sponsored bill gaps

- **~25 senators** likely have:
  - No sponsored legislation in Congress.gov (new or inactive sponsors), **or**
  - Missing `bioguideId`, **or**
  - Cached empty/error responses from the API.

---

## 3. Platform Summaries (12/100 senators missing)

### Data Flow

Platform summaries are produced by the **LLM** in `_narrative_analysis()`, not by a separate fetch.

**Source:** `analyze/cross_reference.py:434–535`

### Why Platform Summaries Are Missing

#### 3a. LLM never called when `has_data` is false

```python
has_data = len(donors) > 0 or len(key_votes) > 0

if has_data:
    llm_result = await _narrative_analysis(...)
else:
    llm_result = {}
```

**Code reference:** `analyze/cross_reference.py:216, 231–243`

When `has_data` is false, the LLM is **not** called, so `platformSummary` is never generated.

`has_data` is false when **both**:

- `len(donors) == 0` (no FEC match or no top donors)
- `len(key_votes) == 0` (no votes on tracked bills)

**Code reference:** `analyze/cross_reference.py:216`

#### 3b. When key_votes is empty

`keyVotes` come from `voting_record["keyVotes"]`, built in `normalize_votes()` from:

- `classified_bills` (significant bills)
- `classified_recent` (recent roll calls)

**Code reference:** `orchestrator.py:1338–1339`; `transform/normalize_votes.py:234–276`

A senator has no key votes when:

- They have **no votes** on any of the tracked bills (new members, missed votes, etc.).
- **Vote matching fails** (e.g. multi-word last names, accents) — `extract_senator_vote()` returns `None`.

**Code reference:** `orchestrator.py:1310–1321`

#### 3c. When donors is empty

`donors` come from `funding.get("topDonors", [])`. Funding is empty when:

- No FEC candidate match for the senator.
- No committee or receipts.
- `normalize_finance()` produces no top donors.

**Code reference:** `orchestrator.py:1284–1296`; `_build_analysis_input` in `orchestrator.py:554`

#### 3d. Platform summary omitted from prompt

Even when the LLM is called, `platformSummary` is only requested if platform data exists:

```python
if platform_topics or (platform_text and not _ERROR_PAGE_SIGS.search(platform_text)):
    prompt += ',"platformSummary":"1 sentence summary of platform"'
```

**Code reference:** `analyze/cross_reference.py:509–510`

If both `platform_topics` and `platform_text` are empty (or error pages), the prompt does **not** ask for a platform summary. The model may still return one, but it is not guaranteed.

#### 3e. LLM failure or empty response

```python
if not result or not isinstance(result, dict):
    logger.warning("Narrative analysis failed for %s", senator["name"])
    return {}
```

**Code reference:** `analyze/cross_reference.py:537–539`

On failure, an empty dict is returned, so `platformSummary` is empty.

#### 3f. Summary: Platform summary gaps

- **~12 senators** likely have:
  - **No donors and no key votes** → LLM never called (most likely), **or**
  - LLM called but no platform data in prompt → summary not requested, **or**
  - LLM failure or empty response.

---

## Summary Table

| Data Type        | Primary Cause of Missing Data                          | Key Code Locations                          |
|------------------|--------------------------------------------------------|---------------------------------------------|
| Campaign promises| No sponsored bills + no usable platform text          | `cross_reference.py:385–408, 262–318, 321–346` |
| Sponsored bills   | API returns empty; no pagination; limit=50            | `congress.py:214–228`; `orchestrator.py:931–936` |
| Platform summary  | `has_data` false (no donors, no key votes) → no LLM    | `cross_reference.py:216, 231–243, 509–510`  |

---

## Recommendations

1. **Campaign promises**
   - Add fallback sources (e.g. floor speeches, committee assignments) when sponsored bills and platform text are both empty.
   - Relax or document the substantive-bill filters if certain bill types should count.

2. **Sponsored bills**
   - Add pagination (`offset`) for senators with >50 sponsored bills.
   - Add a Congress filter if only current-Congress bills are desired.
   - Log and monitor senators with empty sponsored-legislation responses.
   - Consider cache invalidation or retries for empty responses.

3. **Platform summaries**
   - Call the LLM for platform summary even when `has_data` is false, if platform text exists.
   - Or add a separate lightweight path that only generates a platform summary when platform text is available.
   - Improve vote matching for edge cases (multi-word names, accents) to reduce empty `keyVotes`.
