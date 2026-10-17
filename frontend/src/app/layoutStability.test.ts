import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * globals.css reserves the first screen for `#main-content`, which is what
 * keeps the footer from painting in view while a page's content loads and
 * then jumping away (the layout shift that comment measures). The rule only
 * reaches a page whose <main> carries that id.
 */

const SRC = join(import.meta.dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry) ? [path] : [];
  });
}

describe("layout stability", () => {
  it("reserves the first screen for the main content", () => {
    const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
    expect(css).toMatch(/#main-content\s*\{[^}]*min-height:\s*100vh/);
  });

  it("gives every page's <main> the id the rule targets", () => {
    const missing = sourceFiles(SRC).flatMap((file) =>
      (readFileSync(file, "utf8").match(/<main\b[^>]*>/g) ?? [])
        .filter((tag) => !/\bid="main-content"/.test(tag))
        .map((tag) => `${file.slice(SRC.length + 1)}: ${tag}`)
    );
    expect(missing).toEqual([]);
  });

  it("scrolls focus and anchors clear of the fixed header with one rule", () => {
    // WCAG 2.4.11: a control focused by Shift+Tab, or an #anchor, is scrolled
    // to below the header, not under it. A scroll-mt-* on a target would add
    // to the padding and land it a header's height too low.
    const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
    expect(css).toMatch(/html\s*\{[^}]*scroll-padding-top:\s*var\(--header-clearance\)/);
    const offenders = sourceFiles(SRC).filter((file) =>
      /\bscroll-mt-/.test(readFileSync(file, "utf8"))
    );
    expect(offenders).toEqual([]);
  });

  it("lays out page columns with fr tracks, never a 12-column grid", () => {
    // A 12-column grid has eleven gaps; at a doubled text size the rem gaps
    // alone (11 x 4.5rem for gap-9) are wider than a 768 px screen, so the
    // page scrolled sideways (the homepage masthead and footer, 2026-10).
    // Two tracks, e.g. grid-cols-[minmax(0,2fr)_minmax(0,1fr)], have one.
    const offenders = sourceFiles(SRC).filter((file) =>
      /\bgrid-cols-12\b/.test(readFileSync(file, "utf8"))
    );
    expect(offenders).toEqual([]);
  });
});
