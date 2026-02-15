import {
  GOVINFO_API_BASE,
  GOVINFO_RPS,
  DATA_GOV_API_KEY,
  MAX_RETRIES,
  RETRY_BACKOFF_MS,
  log,
} from "../config.mjs";
import { cacheGet, cacheSet } from "../cache/store.mjs";

const MIN_INTERVAL_MS = 1000 / GOVINFO_RPS;
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
      log.debug(`GovInfo API: ${url} (attempt ${attempt})`);
      const res = await fetch(fullUrl, { signal: AbortSignal.timeout(30000) });

      if (res.status === 429) {
        const wait = RETRY_BACKOFF_MS * attempt;
        log.warn(`GovInfo rate limited, waiting ${wait}ms...`);
        await new Promise((r) => setTimeout(r, wait));
        continue;
      }

      if (res.status === 404) {
        log.debug(`GovInfo 404: ${url}`);
        return null;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      return await res.json();
    } catch (e) {
      if (attempt === retries) {
        log.error(`GovInfo API failed after ${retries} attempts: ${url}`, e.message);
        return null;
      }
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS * attempt));
    }
  }
  return null;
}

/**
 * Fetch bill text/summary from GovInfo
 * @param {number} congress - Congress number (e.g. 117)
 * @param {string} billType - "hr", "s", etc.
 * @param {number} billNumber
 * @returns {Object|null} Bill package info with text links
 */
export async function fetchBillPackage(congress, billType, billNumber) {
  const cacheKey = `bill-package-${congress}-${billType}-${billNumber}`;
  const cached = cacheGet("govinfo", cacheKey);
  if (cached) return cached;

  // GovInfo uses a specific package ID format for bills
  // BILLS-{congress}{type}{number}{version}
  // Try enrolled version first, then engrossed, then introduced
  const versions = ["enr", "eas", "es", "eh", "is", "rs"];
  const typeMap = { hr: "hr", s: "s", hjres: "hjres", sjres: "sjres" };
  const govInfoType = typeMap[billType] || billType;

  for (const version of versions) {
    const packageId = `BILLS-${congress}${govInfoType}${billNumber}${version}`;
    const data = await fetchWithRetry(`${GOVINFO_API_BASE}/packages/${packageId}/summary`);
    if (data) {
      cacheSet("govinfo", cacheKey, data);
      return data;
    }
  }

  log.warn(`No GovInfo package found for ${congress}-${billType}-${billNumber}`);
  cacheSet("govinfo", cacheKey, null);
  return null;
}

/**
 * Fetch the plain text content of a bill from GovInfo
 * @param {number} congress
 * @param {string} billType
 * @param {number} billNumber
 * @returns {string|null} Bill text content
 */
export async function fetchBillText(congress, billType, billNumber) {
  const cacheKey = `bill-text-${congress}-${billType}-${billNumber}`;
  const cached = cacheGet("govinfo", cacheKey);
  if (cached) return cached;

  const pkg = await fetchBillPackage(congress, billType, billNumber);
  if (!pkg?.packageId) return null;

  // Try to get the HTML version and extract text
  await throttle();
  const htmUrl = `${GOVINFO_API_BASE}/packages/${pkg.packageId}/htm?api_key=${DATA_GOV_API_KEY}`;

  try {
    const res = await fetch(htmUrl, { signal: AbortSignal.timeout(30000) });
    if (res.ok) {
      let text = await res.text();
      // Strip HTML tags for a rough plain text version
      text = text
        .replace(/<[^>]+>/g, " ")
        .replace(/&nbsp;/g, " ")
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/\s+/g, " ")
        .trim();

      // Truncate to ~8000 chars to keep LLM costs reasonable
      if (text.length > 8000) {
        text = text.slice(0, 8000) + "\n[TRUNCATED]";
      }

      cacheSet("govinfo", cacheKey, text);
      return text;
    }
  } catch (e) {
    log.warn(`Failed to fetch bill text for ${pkg.packageId}: ${e.message}`);
  }

  return null;
}
