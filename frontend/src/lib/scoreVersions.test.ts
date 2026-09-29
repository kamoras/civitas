import { describe, expect, it } from "vitest";
import { SCORE_VERSIONS } from "./scoreVersions";

describe("SCORE_VERSIONS", () => {
  it("is newest first", () => {
    for (let i = 1; i < SCORE_VERSIONS.length; i++) {
      const [newer, older] = [SCORE_VERSIONS[i - 1], SCORE_VERSIONS[i]];
      expect(newer.date >= older.date, `${newer.version} before ${older.version}`).toBe(true);
    }
  });

  it("dates each version by its release, which can't be in the future", () => {
    // Entries used to be dated by a first run that hadn't happened yet.
    const today = new Date().toISOString().slice(0, 10);
    for (const v of SCORE_VERSIONS) {
      expect(v.date, v.version).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(v.date <= today, `${v.version} dated ${v.date}`).toBe(true);
    }
  });

  it("names each version once", () => {
    const names = SCORE_VERSIONS.map((v) => v.version);
    expect(new Set(names).size).toBe(names.length);
  });
});
