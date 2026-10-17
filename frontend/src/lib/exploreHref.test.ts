import { describe, expect, it } from "vitest";
import { exploreHref, officialSourceForKey } from "./exploreHref";

describe("exploreHref", () => {
  it("links the permanent key, the row id only when there is none", () => {
    expect(exploreHref({ id: 8268, key: "fr-2026-17123" })).toBe("/explore/fr-2026-17123");
    expect(exploreHref({ id: 8268 })).toBe("/explore/8268");
  });
});

describe("officialSourceForKey", () => {
  it("names the Federal Register document or Record granule a key carries", () => {
    expect(officialSourceForKey("fr-2026-17123")?.url).toBe(
      "https://www.federalregister.gov/d/2026-17123"
    );
    expect(officialSourceForKey("crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94")?.url).toBe(
      "https://www.govinfo.gov/app/details/CREC-2026-09-16/CREC-2026-09-16-pt1-PgH5836-3"
    );
    expect(officialSourceForKey("scotus-2025-24-440")).toBeNull();
    expect(officialSourceForKey("8268")).toBeNull();
  });
});
