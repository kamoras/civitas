/** FIPS county-code → USPS state code, and the derived code list.
 *
 * Deliberately NOT exported from RaceMap.tsx, where this used to live.
 * RaceMap is a `"use client"` module, and importing a plain value from a
 * client module into a SERVER module (sitemap.ts) yields a client
 * reference proxy rather than the object — `Object.values()` on it
 * silently returns nothing, so the sitemap shipped with zero state URLs
 * and the build reported success. Only reproduces under `next build`.
 *
 * Includes DC (FIPS 11): the map renders it as a clickable, focusable
 * region, so every surface keyed off this list — routes, sitemap — has to
 * cover it or the site links somewhere that 404s.
 */

export const FIPS_TO_STATE: Record<string, string> = {
  "01": "AL",
  "02": "AK",
  "04": "AZ",
  "05": "AR",
  "06": "CA",
  "08": "CO",
  "09": "CT",
  "10": "DE",
  "11": "DC",
  "12": "FL",
  "13": "GA",
  "15": "HI",
  "16": "ID",
  "17": "IL",
  "18": "IN",
  "19": "IA",
  "20": "KS",
  "21": "KY",
  "22": "LA",
  "23": "ME",
  "24": "MD",
  "25": "MA",
  "26": "MI",
  "27": "MN",
  "28": "MS",
  "29": "MO",
  "30": "MT",
  "31": "NE",
  "32": "NV",
  "33": "NH",
  "34": "NJ",
  "35": "NM",
  "36": "NY",
  "37": "NC",
  "38": "ND",
  "39": "OH",
  "40": "OK",
  "41": "OR",
  "42": "PA",
  "44": "RI",
  "45": "SC",
  "46": "SD",
  "47": "TN",
  "48": "TX",
  "49": "UT",
  "50": "VT",
  "51": "VA",
  "53": "WA",
  "54": "WV",
  "55": "WI",
  "56": "WY",
};

/** Unique USPS codes, A→Z. */
export const STATE_CODES: string[] = Array.from(
  new Set(Object.values(FIPS_TO_STATE)),
).sort();
