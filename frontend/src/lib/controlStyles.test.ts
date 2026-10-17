import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { LONG_LABEL, labelCase } from "./controlStyles";

describe("labelCase", () => {
  it("keeps caps for a label of three words or fewer", () => {
    expect(labelCase("Top 5 donors")).not.toBe(LONG_LABEL);
    expect(labelCase("Sponsor")).toContain("uppercase");
  });

  it("sets a longer label in sentence case at 14px on a phone, caps from 640px up", () => {
    expect(labelCase("Also on record, not part of the score")).toBe(LONG_LABEL);
    expect(LONG_LABEL).toMatch(/\bnormal-case\b/);
    expect(LONG_LABEL).toMatch(/\bsm:uppercase\b/);
    expect(LONG_LABEL).toContain("text-[0.875rem]");
  });
});

describe("a phone's type scale (globals.css)", () => {
  const css = readFileSync(join(import.meta.dirname, "..", "app", "globals.css"), "utf8");
  const phone = css.slice(css.indexOf("A PHONE'S TYPE SCALE"));

  it("sets text-xs and the small one-off sizes at 13px under 640px", () => {
    expect(phone).toMatch(
      /@media \(max-width: 639\.98px\)\s*\{\s*:root\s*\{\s*--text-xs: 0\.8125rem;/
    );
    for (const px of [9, 10, 11, 12]) expect(phone).toContain(`.text-\\[${px}px\\]`);
  });

  it("drops wide tracking to 0.08em", () => {
    expect(phone).toContain("--tracking-widest: 0.08em;");
    for (const em of ["0\\.1em", "0\\.12em", "0\\.14em", "0\\.16em"])
      expect(phone).toContain(`.tracking-\\[${em}\\]`);
  });
});
