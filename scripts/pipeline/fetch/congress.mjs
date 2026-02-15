import {
  CONGRESS_API_BASE,
  CONGRESS_RPS,
  DATA_GOV_API_KEY,
  CURRENT_CONGRESS,
  MAX_RETRIES,
  RETRY_BACKOFF_MS,
  log,
} from "../config.mjs";
import { cacheGet, cacheSet } from "../cache/store.mjs";

const MIN_INTERVAL_MS = 1000 / CONGRESS_RPS;
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
  const fullUrl = `${url}${separator}api_key=${DATA_GOV_API_KEY}&format=json`;

  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      log.debug(`Congress API: ${url} (attempt ${attempt})`);
      const res = await fetch(fullUrl, { signal: AbortSignal.timeout(30000) });

      if (res.status === 429) {
        const wait = RETRY_BACKOFF_MS * attempt;
        log.warn(`Rate limited, waiting ${wait}ms...`);
        await new Promise((r) => setTimeout(r, wait));
        continue;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      return await res.json();
    } catch (e) {
      if (attempt === retries) {
        log.error(`Congress API failed after ${retries} attempts: ${url}`, e.message);
        return null;
      }
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS * attempt));
    }
  }
  return null;
}

/**
 * Fetch all current senators from Congress.gov
 * @returns {Array} Array of member objects
 */
export async function fetchSenators() {
  const cached = cacheGet("congress", "senators-list");
  if (cached) return cached;

  log.info("Fetching senators from Congress.gov...");
  const members = [];
  let offset = 0;
  const limit = 250;

  while (true) {
    const data = await fetchWithRetry(
      `${CONGRESS_API_BASE}/member?currentMember=true&chamber=Senate&limit=${limit}&offset=${offset}`
    );
    if (!data?.members) break;
    members.push(...data.members);
    if (data.members.length < limit) break;
    offset += limit;
  }

  log.info(`Fetched ${members.length} senators`);
  cacheSet("congress", "senators-list", members);
  return members;
}

/**
 * Fetch detailed member info including terms and sponsored legislation
 * @param {string} bioguideId - Bioguide ID of the member
 */
export async function fetchMemberDetail(bioguideId) {
  const cacheKey = `member-detail-${bioguideId}`;
  const cached = cacheGet("congress", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(`${CONGRESS_API_BASE}/member/${bioguideId}`);
  if (data?.member) {
    cacheSet("congress", cacheKey, data.member);
    return data.member;
  }
  return null;
}

/**
 * Fetch a member's sponsored legislation
 * @param {string} bioguideId
 */
export async function fetchMemberSponsored(bioguideId) {
  const cacheKey = `member-sponsored-${bioguideId}`;
  const cached = cacheGet("congress", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${CONGRESS_API_BASE}/member/${bioguideId}/sponsored-legislation?limit=50`
  );
  const results = data?.sponsoredLegislation || [];
  cacheSet("congress", cacheKey, results);
  return results;
}

/**
 * Fetch bill details
 * @param {number} congress
 * @param {string} billType - "hr", "s", etc.
 * @param {number} billNumber
 */
export async function fetchBill(congress, billType, billNumber) {
  const cacheKey = `bill-${congress}-${billType}-${billNumber}`;
  const cached = cacheGet("congress", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${CONGRESS_API_BASE}/bill/${congress}/${billType}/${billNumber}`
  );
  if (data?.bill) {
    cacheSet("congress", cacheKey, data.bill);
    return data.bill;
  }
  return null;
}

/**
 * Fetch roll call votes for a specific bill
 * @param {number} congress
 * @param {string} billType
 * @param {number} billNumber
 */
export async function fetchBillActions(congress, billType, billNumber) {
  const cacheKey = `bill-actions-${congress}-${billType}-${billNumber}`;
  const cached = cacheGet("congress", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${CONGRESS_API_BASE}/bill/${congress}/${billType}/${billNumber}/actions?limit=100`
  );
  const results = data?.actions || [];
  cacheSet("congress", cacheKey, results);
  return results;
}

/**
 * Fetch Senate roll call vote details from senate.gov XML feed.
 * Congress.gov API doesn't have Senate roll call votes — only senate.gov does.
 * @param {number} congress
 * @param {number} sessionNumber
 * @param {number} rollCallNumber
 */
export async function fetchRollCallVote(congress, sessionNumber, rollCallNumber) {
  const cacheKey = `rollcall-senate-${congress}-${sessionNumber}-${rollCallNumber}`;
  const cached = cacheGet("congress", cacheKey);
  if (cached) return cached;

  await throttle();
  const paddedRoll = String(rollCallNumber).padStart(5, "0");
  const url = `https://www.senate.gov/legislative/LIS/roll_call_votes/vote${congress}${sessionNumber}/vote_${congress}_${sessionNumber}_${paddedRoll}.xml`;

  try {
    log.debug(`Senate.gov vote: ${url}`);
    const res = await fetch(url, { signal: AbortSignal.timeout(30000) });
    if (!res.ok) {
      log.warn(
        `Senate roll call not found: ${congress}-${sessionNumber}-${rollCallNumber} (${res.status})`
      );
      return null;
    }

    const xml = await res.text();
    const result = parseSenateVoteXML(xml, congress, sessionNumber, rollCallNumber);
    if (result) {
      cacheSet("congress", cacheKey, result);
    }
    return result;
  } catch (e) {
    log.error(
      `Failed to fetch Senate roll call ${congress}-${sessionNumber}-${rollCallNumber}:`,
      e.message
    );
    return null;
  }
}

/**
 * Parse Senate.gov roll call vote XML into a structured object.
 * Extracts each senator's vote keyed by last_name + state for matching.
 */
function parseSenateVoteXML(xml, congress, session, rollNumber) {
  const members = [];
  const memberRegex = /<member>([\s\S]*?)<\/member>/g;
  let match;

  while ((match = memberRegex.exec(xml)) !== null) {
    const block = match[1];
    const getValue = (tag) => {
      const m = block.match(new RegExp(`<${tag}>([^<]*)</${tag}>`));
      return m ? m[1].trim() : "";
    };

    members.push({
      firstName: getValue("first_name"),
      lastName: getValue("last_name"),
      party: getValue("party"),
      state: getValue("state"),
      voteCast: getValue("vote_cast"),
      lisId: getValue("lis_member_id"),
    });
  }

  if (members.length === 0) return null;

  return {
    congress,
    session,
    rollNumber,
    members,
  };
}

/**
 * Fetch bill summary text
 * @param {number} congress
 * @param {string} billType
 * @param {number} billNumber
 */
export async function fetchBillSummaries(congress, billType, billNumber) {
  const cacheKey = `bill-summaries-${congress}-${billType}-${billNumber}`;
  const cached = cacheGet("congress", cacheKey);
  if (cached) return cached;

  const data = await fetchWithRetry(
    `${CONGRESS_API_BASE}/bill/${congress}/${billType}/${billNumber}/summaries`
  );
  const results = data?.summaries || [];
  cacheSet("congress", cacheKey, results);
  return results;
}
