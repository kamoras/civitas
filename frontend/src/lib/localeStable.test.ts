import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { formatUtcDate } from "./formatting";

/**
 * Text the server renders must come out the same in the browser that
 * hydrates it. A number or date formatted in the runtime's own locale
 * ("1.234" in a German browser, "4 July 2026" in a British one) differs from
 * the server's and fails hydration (React #418). So every toLocale*String
 * names its locale.
 */

const SRC = join(import.meta.dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
  });
}

describe("locale-stable formatting", () => {
  it("names a locale in every toLocale*String call", () => {
    const offenders = sourceFiles(SRC).flatMap((file) =>
      (
        readFileSync(file, "utf8").match(/toLocale(?:Date|Time)?String\((?:\)|undefined)/g) ?? []
      ).map((call) => `${file.slice(SRC.length + 1)}: ${call}`)
    );
    expect(offenders).toEqual([]);
  });

  it("formats a calendar date the same in every zone", () => {
    expect(formatUtcDate("2026-07-04")).toBe("July 4, 2026");
    expect(formatUtcDate("2026-01-01", { month: "short", day: "numeric" })).toBe("Jan 1");
  });
});
