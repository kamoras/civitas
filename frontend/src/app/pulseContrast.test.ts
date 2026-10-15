import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Tailwind's animate-pulse fades to half opacity, which takes text below
 * 4.5:1 for part of every cycle (WCAG 1.4.3): "Analyzing document…" on an
 * Explore document fell to 3.86:1, and a summary can take minutes. Text
 * pulses with globals.css's pulse-subtle (never below 8:1 on the site's
 * text colours); animate-pulse is for decoration with no text in it (a lamp,
 * a skeleton block).
 */

const SRC = join(import.meta.dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry) ? [path] : [];
  });
}

describe("pulsing text", () => {
  it("never fades text with animate-pulse", () => {
    const offenders = sourceFiles(SRC).flatMap((file) =>
      readFileSync(file, "utf8")
        .split("\n")
        .map((line, i) => ({ line, at: `${file.slice(SRC.length + 1)}:${i + 1}` }))
        .filter(
          ({ line }) =>
            /(?<!motion-safe:)\banimate-pulse\b/.test(line) &&
            /\btext-(ink|signal|phos|white)/.test(line)
        )
        .map(({ at }) => at)
    );
    expect(offenders).toEqual([]);
  });
});
