import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { BACKEND_COPY, assetVersions } from "../../scripts/assetVersions.mjs";
import { DEFAULT_OG_IMAGE, ICON_PATH, ogCardUrl } from "./site";
import { cardImage } from "./seo";

// Feed readers and chat proxies cache a picture by URL for good, so every
// card and icon URL carries a hash of the files that draw it
// (scripts/assetVersions.mjs).
describe("asset versions", () => {
  it("the backend's copy matches the frontend's files (run `npm run asset-versions`)", () => {
    const copy = JSON.parse(readFileSync(BACKEND_COPY, "utf8"));
    const live = assetVersions();
    expect({ ogCard: copy.ogCard, icon: copy.icon }).toEqual(live);
  });

  it("puts the card version on every card URL the site emits", () => {
    const v = assetVersions().ogCard;
    expect(ogCardUrl("state=MT")).toBe(`/api/og?state=MT&v=${v}`);
    expect(cardImage("bill=119/HR.1", "alt")[0].url).toMatch(
      new RegExp(`/api/og\\?bill=119/HR\\.1&v=${v}$`)
    );
    expect(DEFAULT_OG_IMAGE.url).toBe(`/opengraph-image?v=${v}`);
  });

  it("puts the icon version on the icon", () => {
    expect(ICON_PATH).toBe(`/apple-icon.png?v=${assetVersions().icon}`);
  });
});
