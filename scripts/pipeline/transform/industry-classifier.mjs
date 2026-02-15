/**
 * Classify an organization/employer name into one of the IndustryCode values.
 * Uses a static lookup table for known organizations, with keyword fallbacks.
 *
 * For unknown organizations, this returns "OTHER". The LLM batch classifier
 * in analyze/llm-client.mjs can be used to reclassify unknowns.
 */

const INDUSTRY_KEYWORDS = {
  PHARMA: [
    "pharma",
    "pfizer",
    "merck",
    "johnson & johnson",
    "abbvie",
    "eli lilly",
    "bristol-myers",
    "novartis",
    "roche",
    "sanofi",
    "astrazeneca",
    "amgen",
    "gilead",
    "biogen",
    "regeneron",
    "moderna",
    "drug",
    "biotech",
    "pharmaceutical",
    "medicine",
    "health product",
  ],
  INSURANCE: [
    "insurance",
    "anthem",
    "cigna",
    "humana",
    "unitedhealth",
    "aetna",
    "blue cross",
    "blue shield",
    "metlife",
    "aflac",
    "progressive",
    "allstate",
    "state farm",
    "underwriter",
    "actuarial",
  ],
  OIL_GAS: [
    "oil",
    "gas",
    "petroleum",
    "exxon",
    "chevron",
    "conocophillips",
    "bp",
    "shell",
    "halliburton",
    "schlumberger",
    "marathon petroleum",
    "valero",
    "pipeline",
    "drilling",
    "fracking",
    "fossil fuel",
    "koch",
  ],
  DEFENSE: [
    "defense",
    "lockheed",
    "raytheon",
    "boeing",
    "northrop grumman",
    "general dynamics",
    "bae systems",
    "l3harris",
    "leidos",
    "military",
    "weapons",
    "aerospace",
    "missile",
    "naval",
    "army",
    "air force",
  ],
  FINANCE: [
    "goldman sachs",
    "morgan stanley",
    "jpmorgan",
    "jp morgan",
    "citigroup",
    "bank of america",
    "wells fargo",
    "blackrock",
    "citadel",
    "hedge fund",
    "investment",
    "securities",
    "capital",
    "financial",
    "banking",
    "credit suisse",
    "deutsche bank",
    "merrill lynch",
    "fidelity",
    "vanguard",
    "private equity",
    "venture capital",
    "wall street",
  ],
  REAL_ESTATE: [
    "real estate",
    "realtor",
    "realty",
    "property",
    "housing",
    "mortgage",
    "homebuilder",
    "construction developer",
    "reit",
    "national association of realtors",
  ],
  TECH: [
    "google",
    "alphabet",
    "meta",
    "facebook",
    "apple",
    "microsoft",
    "amazon",
    "oracle",
    "salesforce",
    "adobe",
    "intel",
    "nvidia",
    "qualcomm",
    "ibm",
    "cisco",
    "software",
    "internet",
    "silicon valley",
    "data",
    "cloud",
    "ai ",
    "artificial intelligence",
    "computing",
  ],
  TELECOM: [
    "at&t",
    "verizon",
    "t-mobile",
    "comcast",
    "charter",
    "telecommunications",
    "telecom",
    "wireless",
    "broadband",
    "cable",
    "dish network",
    "sprint",
  ],
  AGRIBUSINESS: [
    "agribusiness",
    "agriculture",
    "farm",
    "monsanto",
    "bayer crop",
    "cargill",
    "archer daniels",
    "deere",
    "john deere",
    "crop",
    "cattle",
    "dairy",
    "grain",
    "livestock",
    "rancher",
  ],
  ENERGY: [
    "energy",
    "utility",
    "utilities",
    "electric",
    "power",
    "duke energy",
    "southern company",
    "dominion",
    "exelon",
    "nextera",
    "solar",
    "wind",
    "renewable",
    "nuclear",
    "coal",
  ],
  CONSTRUCTION: [
    "construction",
    "building",
    "contractor",
    "engineering",
    "cement",
    "infrastructure",
    "architect",
  ],
  TRANSPORT: [
    "transport",
    "airline",
    "aviation",
    "railroad",
    "shipping",
    "trucking",
    "logistics",
    "ups",
    "fedex",
    "delta",
    "united airlines",
    "american airlines",
    "southwest airlines",
  ],
  LAWYERS: [
    "law firm",
    "attorney",
    "lawyer",
    "legal",
    "litigation",
    "skadden",
    "jones day",
    "kirkland",
    "latham",
    "sidley",
    "sullivan & cromwell",
    "davis polk",
  ],
  LOBBYISTS: [
    "lobbying",
    "lobbyist",
    "government relations",
    "public affairs",
    "advocacy",
    "akin gump",
    "brownstein",
  ],
  GAMBLING: [
    "casino",
    "gambling",
    "gaming",
    "las vegas sands",
    "mgm resorts",
    "wynn",
    "caesars",
    "lottery",
    "sports betting",
  ],
  GUNS: [
    "firearm",
    "gun",
    "rifle",
    "nra",
    "national rifle",
    "smith & wesson",
    "remington",
    "ammunition",
    "weapons manufacturer",
  ],
  TOBACCO: [
    "tobacco",
    "cigarette",
    "altria",
    "philip morris",
    "reynolds",
    "vaping",
    "juul",
    "e-cigarette",
  ],
  CRYPTO: [
    "crypto",
    "bitcoin",
    "blockchain",
    "coinbase",
    "binance",
    "digital currency",
    "web3",
    "defi",
  ],
  PRIVATE_PRISON: [
    "prison",
    "corrections",
    "corecivic",
    "geo group",
    "detention",
    "incarceration",
    "correctional",
  ],
};

// Pre-compile lowercase keywords for faster matching
const COMPILED_RULES = Object.entries(INDUSTRY_KEYWORDS).map(([industry, keywords]) => ({
  industry,
  keywords: keywords.map((k) => k.toLowerCase()),
}));

/**
 * Classify an organization name into an IndustryCode.
 * @param {string} orgName - Organization or employer name
 * @returns {string} IndustryCode enum value
 */
export function classifyIndustry(orgName) {
  if (!orgName) return "OTHER";
  const lower = orgName.toLowerCase();

  for (const { industry, keywords } of COMPILED_RULES) {
    for (const keyword of keywords) {
      if (lower.includes(keyword)) {
        return industry;
      }
    }
  }

  return "OTHER";
}

/**
 * Batch classify organizations. Returns a Map of orgName -> IndustryCode.
 * This is the static classifier; for unknown orgs, use the LLM batch classifier.
 * @param {string[]} orgNames
 * @returns {Map<string, string>}
 */
export function classifyBatch(orgNames) {
  const results = new Map();
  const unknowns = [];

  for (const name of orgNames) {
    const industry = classifyIndustry(name);
    results.set(name, industry);
    if (industry === "OTHER") {
      unknowns.push(name);
    }
  }

  if (unknowns.length > 0) {
    log.debug(`Industry classifier: ${unknowns.length} unknowns out of ${orgNames.length}`);
  }

  return results;
}

// Import log at module level to avoid circular deps
import { log } from "../config.mjs";
