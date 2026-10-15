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
});
