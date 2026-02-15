// API base URLs
export const CONGRESS_API_BASE = "https://api.congress.gov/v3";
export const FEC_API_BASE = "https://api.open.fec.gov/v1";
export const GOVINFO_API_BASE = "https://api.govinfo.gov";

// Current congress number (119th: 2025-2027)
export const CURRENT_CONGRESS = 119;

// Rate limits (requests per second, conservative to stay under hourly caps)
export const CONGRESS_RPS = 1.2; // ~4320/hr, under 5000/hr
export const FEC_RPS = 0.25; // ~900/hr, under 1000/hr
export const GOVINFO_RPS = 1.0;

// Retry config
export const MAX_RETRIES = 3;
export const RETRY_BACKOFF_MS = 2000;

// Cache TTL
export const CACHE_TTL_HOURS = parseInt(process.env.PIPELINE_CACHE_TTL_HOURS || "72", 10);

// LLM config (Gemini)
export const GEMINI_MODEL = process.env.PIPELINE_GEMINI_MODEL || "gemini-2.5-flash-lite";

// Log level
export const LOG_LEVEL = process.env.PIPELINE_LOG_LEVEL || "info";

// API keys (loaded from .env via dotenv)
export const DATA_GOV_API_KEY = process.env.DATA_GOV_API_KEY;
export const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

// Key bills to analyze (curated list of significant legislation)
// These will be fetched from Congress.gov and analyzed by the LLM
export const KEY_BILLS = [
  { congress: 117, type: "hr", number: 5376, name: "Inflation Reduction Act" },
  { congress: 117, type: "hr", number: 3684, name: "Infrastructure Investment and Jobs Act" },
  { congress: 118, type: "s", number: 2281, name: "National Defense Authorization Act FY2024" },
  { congress: 117, type: "hr", number: 3, name: "Elijah E. Cummings Lower Drug Costs Now Act" },
  { congress: 118, type: "hr", number: 2670, name: "National Defense Authorization Act FY2024" },
  { congress: 117, type: "s", number: 2093, name: "CHIPS and Science Act" },
  { congress: 118, type: "s", number: 2073, name: "Bipartisan Safer Communities Act" },
  { congress: 117, type: "hr", number: 1319, name: "American Rescue Plan Act" },
  {
    congress: 118,
    type: "hr",
    number: 7024,
    name: "Tax Relief for American Families and Workers Act",
  },
  { congress: 117, type: "s", number: 3580, name: "Consolidated Appropriations Act 2022" },
  { congress: 118, type: "s", number: 3853, name: "FAA Reauthorization Act" },
  { congress: 117, type: "hr", number: 2471, name: "Consolidated Appropriations Act 2022" },
  { congress: 118, type: "s", number: 1, name: "For the People Act" },
  { congress: 117, type: "s", number: 1, name: "For the People Act" },
  { congress: 117, type: "hr", number: 1, name: "For the People Act" },
  { congress: 118, type: "s", number: 686, name: "RESTRICT Act" },
  { congress: 117, type: "s", number: 2938, name: "Bipartisan Safer Communities Act" },
  { congress: 118, type: "hr", number: 3746, name: "Fiscal Responsibility Act" },
  { congress: 119, type: "s", number: 5, name: "Laken Riley Act" },
  {
    congress: 118,
    type: "hr",
    number: 8580,
    name: "Continuing Appropriations and Extensions Act 2025",
  },
];

// Logging helper
const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const currentLevel = LEVELS[LOG_LEVEL] ?? 1;

export const log = {
  debug: (...args) => currentLevel <= 0 && console.log("[DEBUG]", ...args),
  info: (...args) => currentLevel <= 1 && console.log("[INFO]", ...args),
  warn: (...args) => currentLevel <= 2 && console.warn("[WARN]", ...args),
  error: (...args) => currentLevel <= 3 && console.error("[ERROR]", ...args),
};
