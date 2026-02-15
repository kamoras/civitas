import {
  FEC_API_BASE,
  FEC_RPS,
  DATA_GOV_API_KEY,
  MAX_RETRIES,
  RETRY_BACKOFF_MS,
  log,
} from "../config.mjs";
import { cacheGet, cacheSet } from "../cache/store.mjs";

const MIN_INTERVAL_MS = 1000 / FEC_RPS;
let lastRequestTime = 0;

async function throttle() {
  const now = Date.now();
  const elapsed = now - lastRequestTime;
  if (elapsed < MIN_INTERVAL_MS) {
    await new Promise((r) => setTimeout(r, MIN_INTERVAL_MS - elapsed));
  }
  lastRequestTime = Date.now();
}

async function fetchWithRetry(url, retries = MAX_RETRIES) {
  await throttle();
  const separator = url.includes("?") ? "&" : "?";
  const fullUrl = `${url}${separator}api_key=${DATA_GOV_API_KEY}`;

  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      log.debug(`FEC API: ${url} (attempt ${attempt})`);
      const res = await fetch(fullUrl, { signal: AbortSignal.timeout(30000) });

      if (res.status === 429) {
        const wait = RETRY_BACKOFF_MS * attempt * 2; // FEC rate limits are tighter
        log.warn(`FEC rate limited, waiting ${wait}ms...`);
        await new Promise((r) => setTimeout(r, wait));
        continue;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      return await res.json();
    } catch (e) {
      if (attempt === retries) {
        log.error(`FEC API failed after ${retries} attempts: ${url}`, e.message);
        return null;
      }
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS * attempt));
    }
  }
  return null;
}

/**
 * Search for a Senate candidate in FEC data
 * @param {string} name - Senator name
 * @param {string} state - Two-letter state code
 * @returns {Object|null} Best matching candidate record
 */
export async function findCandidate(name, state) {
  const cacheKey = `candidate-search-${name.replace(/\s+/g, "_")}-${state}`;
  const cached = cacheGet("fec", cacheKey);
  if (cached) return cached;

  // FEC search uses last name
  const nameParts = name.split(/\s+/);
  const lastName = nameParts[nameParts.length - 1];

  const data = await fetchWithRetry(
    `${FEC_API_BASE}/candidates/search/?name=${encodeURIComponent(lastName)}&state=${state}&office=S&per_page=20`
  );

  if (!data?.results?.length) {
    log.warn(`No FEC candidate found for ${name} (${state})`);
    cacheSet("fec", cacheKey, null);
    return null;
  }

  // Try to match by full name
  const nameUpper = name.toUpperCase();
  const match =
    data.results.find((c) => {
      const cName = c.name?.toUpperCase() || "";
      // FEC uses "LASTNAME, FIRSTNAME" format
      return nameParts.every((part) => cName.includes(part.toUpperCase()));
    }) || data.results[0]; // Fallback to first result

  log.debug(`FEC candidate match for ${name}: ${match?.name} (${match?.candidate_id})`);
  cacheSet("fec", cacheKey, match);
  return match;
}

/**
 * Fetch candidate financial totals
 * @param {string} candidateId - FEC candidate ID
 */
export async function fetchCandidateFinancials(candidateId) {
  const cacheKey = `candidate-financials-${candidateId}`;
  const cached = cacheGet("fec", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${FEC_API_BASE}/candidate/${candidateId}/totals/?sort=-cycle&per_page=4`
  );

  const results = data?.results || [];
  cacheSet("fec", cacheKey, results);
  return results;
}

/**
 * Fetch the candidate's principal campaign committee
 * @param {string} candidateId
 */
export async function fetchCandidateCommittees(candidateId) {
  const cacheKey = `candidate-committees-${candidateId}`;
  const cached = cacheGet("fec", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${FEC_API_BASE}/candidate/${candidateId}/committees/?designation=P&per_page=5`
  );

  const results = data?.results || [];
  cacheSet("fec", cacheKey, results);
  return results;
}

/**
 * Fetch individual contribution receipts to a committee
 * @param {string} committeeId - Campaign committee ID
 */
export async function fetchCommitteeReceipts(committeeId) {
  const cacheKey = `committee-receipts-indiv-${committeeId}`;
  const cached = cacheGet("fec", cacheKey);
  if (cached) return cached;

  // Get individual contributions only (for employer grouping)
  const data = await fetchWithRetry(
    `${FEC_API_BASE}/schedules/schedule_a/?committee_id=${committeeId}&sort=-contribution_receipt_amount&per_page=100&is_individual=true`
  );

  const results = data?.results || [];
  cacheSet("fec", cacheKey, results);
  return results;
}

/**
 * Fetch PAC/committee contributions to a candidate's campaign committee.
 * These are contributions from PACs, party committees, and other committees
 * directly to the senator's campaign — the core corporate money flow.
 * @param {string} committeeId - Campaign committee ID
 */
export async function fetchPACReceipts(committeeId) {
  const cacheKey = `committee-receipts-pac-${committeeId}`;
  const cached = cacheGet("fec", cacheKey);
  if (cached) return cached;

  // is_individual=false returns committee-to-committee contributions (PACs)
  const data = await fetchWithRetry(
    `${FEC_API_BASE}/schedules/schedule_a/?committee_id=${committeeId}&sort=-contribution_receipt_amount&per_page=100&is_individual=false`
  );

  const results = data?.results || [];
  cacheSet("fec", cacheKey, results);
  return results;
}

/**
 * Fetch aggregated totals by contributor for a committee
 * @param {string} committeeId
 */
export async function fetchAggregatedContributors(committeeId) {
  const cacheKey = `aggregated-contributors-${committeeId}`;
  const cached = cacheGet("fec", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id=${committeeId}&sort=-total&per_page=20`
  );

  const results = data?.results || [];
  cacheSet("fec", cacheKey, results);
  return results;
}
