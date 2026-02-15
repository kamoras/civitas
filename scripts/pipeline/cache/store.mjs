import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from "fs";
import { join, dirname } from "path";
import { createHash } from "crypto";
import { CACHE_TTL_HOURS, log } from "../config.mjs";

const CACHE_ROOT = new URL("../../.cache", import.meta.url).pathname;

let hits = 0;
let misses = 0;

function ensureDir(filePath) {
  const dir = dirname(filePath);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
}

function cachePath(tier, key) {
  // Sanitize key for filesystem
  const safeKey = key.replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(CACHE_ROOT, tier, `${safeKey}.json`);
}

function isExpired(filePath, ttlHours = CACHE_TTL_HOURS) {
  if (ttlHours <= 0) return false; // TTL of 0 means never expire
  try {
    const stat = statSync(filePath);
    const ageHours = (Date.now() - stat.mtimeMs) / (1000 * 60 * 60);
    return ageHours > ttlHours;
  } catch {
    return true;
  }
}

/**
 * Get cached data. Returns null if not found or expired.
 * @param {string} tier - Cache tier (e.g. "congress", "fec", "analysis")
 * @param {string} key - Cache key
 * @param {number} [ttlHours] - Override TTL for this specific get
 */
export function cacheGet(tier, key, ttlHours) {
  const path = cachePath(tier, key);
  if (!existsSync(path) || isExpired(path, ttlHours ?? CACHE_TTL_HOURS)) {
    misses++;
    return null;
  }
  try {
    const raw = readFileSync(path, "utf-8");
    const parsed = JSON.parse(raw);
    hits++;
    log.debug(`Cache hit: ${tier}/${key}`);
    return parsed.data;
  } catch (e) {
    log.warn(`Cache read error for ${tier}/${key}:`, e.message);
    misses++;
    return null;
  }
}

/**
 * Store data in cache.
 * @param {string} tier - Cache tier
 * @param {string} key - Cache key
 * @param {*} data - Data to cache
 */
export function cacheSet(tier, key, data) {
  const path = cachePath(tier, key);
  ensureDir(path);
  const envelope = {
    cachedAt: new Date().toISOString(),
    key,
    data,
  };
  writeFileSync(path, JSON.stringify(envelope, null, 2));
  log.debug(`Cache set: ${tier}/${key}`);
}

/**
 * Get cached LLM analysis result, keyed by hash of prompt + input.
 * LLM cache never expires by TTL — only invalidated by prompt version changes.
 * @param {string} promptVersion - Version string for the prompt template
 * @param {*} inputData - The input data sent to the LLM
 */
export function analysisGet(promptVersion, inputData) {
  const hash = createHash("sha256")
    .update(promptVersion)
    .update(JSON.stringify(inputData))
    .digest("hex")
    .slice(0, 16);
  return cacheGet("analysis", `${promptVersion}_${hash}`, 0); // TTL 0 = never expire
}

/**
 * Store LLM analysis result.
 */
export function analysisSet(promptVersion, inputData, result) {
  const hash = createHash("sha256")
    .update(promptVersion)
    .update(JSON.stringify(inputData))
    .digest("hex")
    .slice(0, 16);
  cacheSet("analysis", `${promptVersion}_${hash}`, result);
}

/**
 * Get cache statistics.
 */
export function getCacheStats() {
  return {
    hits,
    misses,
    hitRate: hits + misses > 0 ? ((hits / (hits + misses)) * 100).toFixed(1) + "%" : "N/A",
  };
}
