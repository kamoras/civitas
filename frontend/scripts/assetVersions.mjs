// Content versions for the site's icon and its cards (/api/og).
//
// Feed readers, chat bots and their image proxies (MonitoRSS, Discord) cache
// a picture by its URL for good: after the new brand (#1147) Discord kept
// the old icon for /apple-icon.png and the old dark card for
// /api/og?state=MT. Every such URL the site emits carries `v=<hash>` of the
// files that draw it, so a new design is a new URL.
//
// next.config.mjs computes these at build time (OG_CARD_VERSION,
// ICON_VERSION). The backend can't see the frontend's files, so it reads the
// copy `npm run asset-versions` writes to backend/app/data/asset_versions.json;
// src/lib/assetVersions.test.ts fails until that copy matches.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const BACKEND_COPY = path.resolve(FRONTEND, "../backend/app/data/asset_versions.json");

// The files whose bytes decide what each picture looks like.
export const ASSET_FILES = {
  ogCard: [
    "src/app/api/og/route.tsx",
    "src/app/opengraph-image.tsx",
    "src/lib/ogLockup.tsx",
    "src/lib/ogTheme.ts",
    "src/lib/ogFonts.ts",
    "src/lib/ogSections.ts",
  ],
  icon: ["src/app/apple-icon.png", "src/app/icon.svg", "public/icon-512.png"],
};

/** { ogCard, icon }: the first 10 hex digits of a sha256 over each set. */
export function assetVersions() {
  return Object.fromEntries(
    Object.entries(ASSET_FILES).map(([name, files]) => {
      const hash = createHash("sha256");
      for (const file of files) {
        hash.update(`${file}\0`);
        hash.update(readFileSync(path.join(FRONTEND, file)));
        hash.update("\0");
      }
      return [name, hash.digest("hex").slice(0, 10)];
    })
  );
}

export function backendCopy() {
  return {
    _source:
      "Written by frontend/scripts/assetVersions.mjs (npm run asset-versions): a sha256 of the files that draw the site's cards and icon, the same value the frontend computes at build. Read by backend/app/site_assets.py. Regenerate when those files change; frontend/src/lib/assetVersions.test.ts fails until this matches.",
    ...assetVersions(),
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  writeFileSync(BACKEND_COPY, `${JSON.stringify(backendCopy(), null, 2)}\n`);
  console.log(`Wrote ${BACKEND_COPY}`);
}
