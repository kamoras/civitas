import { readFile } from "node:fs/promises";
import { join } from "node:path";

/** The site's own faces as TrueType, for every Satori-rendered image
 *  (opengraph-image.tsx, apple-icon.tsx, api/og/route.tsx). Self-hosted
 *  like the page fonts (§8): these images used to fetch Archivo from
 *  Google Fonts on every request. Satori can't read the woff2 files
 *  next/font serves, so backend/scripts/fetch_site_fonts.py derives these
 *  from them (latin + latin-ext merged, Source Serif 4 at its display size).
 *
 *  Read from the working directory, which `next build`'s file tracing
 *  copies them into for the standalone server (outputFileTracingIncludes
 *  in next.config.mjs). Node runtime only. */
const DIR = join(process.cwd(), "src", "app", "fonts", "og");

/** The families as an image's style objects name them. */
export const OG_FAMILY = {
  /** The wordmark only, as in the header lockup. */
  wordmark: "Archivo",
  /** Titles. */
  serif: "Source Serif 4",
  /** Everything else. */
  sans: "Fira Sans",
  /** Record identifiers only (bill numbers, roll calls, the domain). */
  mono: "DM Mono",
} as const;

type Weight = 400 | 600 | 800;
const FILES: [string, string, Weight][] = [
  [OG_FAMILY.wordmark, "archivo-800.ttf", 800],
  [OG_FAMILY.serif, "source-serif-4-600.ttf", 600],
  [OG_FAMILY.sans, "fira-sans-400.ttf", 400],
  [OG_FAMILY.sans, "fira-sans-600.ttf", 600],
  [OG_FAMILY.mono, "dm-mono-400.ttf", 400],
];

export type OgFont = { name: string; data: Buffer; weight: Weight; style: "normal" };

let loaded: Promise<OgFont[]> | null = null;

/** Every face, read once per process. A failed read is not cached, so the
 *  next request tries again rather than rendering without fonts for good. */
export function ogFonts(): Promise<OgFont[]> {
  loaded ??= Promise.all(
    FILES.map(async ([name, file, weight]) => ({
      name,
      data: await readFile(join(DIR, file)),
      weight,
      style: "normal" as const,
    }))
  ).catch((err) => {
    loaded = null;
    throw err;
  });
  return loaded;
}
