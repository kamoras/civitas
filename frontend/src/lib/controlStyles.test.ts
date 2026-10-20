import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { LONG_LABEL, labelCase } from "./controlStyles";

describe("labelCase", () => {
  it("keeps a label of three words or fewer at text-xs", () => {
    expect(labelCase("Top 5 donors")).not.toBe(LONG_LABEL);
    expect(labelCase("Sponsor")).toContain("");
  });

  it("sets a longer label at 14px on a phone, and never in capitals", () => {
    expect(labelCase("Also on record, not part of the score")).toBe(LONG_LABEL);
    expect(LONG_LABEL).not.toMatch(/uppercase/);
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

  it("tightens the tracked capitals left (eyebrow, logotype) to 0.08em", () => {
    for (const em of ["0\\.12em", "0\\.14em", "0\\.18em"])
      expect(phone).toContain(`.tracking-\\[${em}\\]`);
  });
});
