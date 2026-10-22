import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("a phone's type scale (globals.css)", () => {
  const css = readFileSync(join(import.meta.dirname, "..", "app", "globals.css"), "utf8");
  const phone = css.slice(css.indexOf("A PHONE'S TYPE SCALE"));

  it("tightens the tracked capitals left (eyebrow, logotype) to 0.08em", () => {
    for (const em of ["0\\.12em", "0\\.14em", "0\\.18em"])
      expect(phone).toContain(`.tracking-\\[${em}\\]`);
  });
});
