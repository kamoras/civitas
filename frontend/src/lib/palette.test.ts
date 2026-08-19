import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Source-level guards on the palette rules the design system states but
 * nothing enforced.
 *
 * Both of these regressed once already inside the change that introduced the
 * rules. The migration off opacity steps moved ~2,900 call sites by codemod,
 * and a codemod rewrites the tokens it knows — an arbitrary
 * `text-[#ffaa00]/50` looks like nothing it was taught to find, so it sailed
 * through, composited to 3.24:1 against #0D0208, and sat two lines under a
 * sibling the same pass had converted correctly. A grep is a blunt test, but
 * it is the only kind that can see a colour written in a form the type system
 * and the browser-level audits both read as intentional.
 */

const SRC = join(import.meta.dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css)$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
  });
}

const FILES = sourceFiles(SRC).map((path) => ({
  path: path.slice(SRC.length + 1),
  text: readFileSync(path, "utf8"),
}));

describe("palette discipline", () => {
  /**
   * An arbitrary hex with an opacity modifier is the exact shape the ink /
   * phos / signal ramps exist to remove: alpha over a near-black page is
   * invisible to review, and every one that shipped landed under the 4.5:1
   * floor. Solid tokens carry their measured ratio in tailwind.config.ts.
   */
  it("has no arbitrary hex colour utility carrying an opacity modifier", () => {
    const offenders = FILES.flatMap(({ path, text }) =>
      [...text.matchAll(/\b(?:text|bg|border|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]\/\d+/g)].map(
        (m) => `${path}: ${m[0]}`
      )
    );
    expect(offenders).toEqual([]);
  });

  /**
   * TerminalTitlebar labels what a panel HOLDS. Deriving that label from the
   * heading the panel already renders underneath prints the same words twice
   * and dresses the second copy up as a filename — the `senate_leaderboard.db`
   * habit this design pass set out to delete, rebuilt mechanically. /about,
   * /accessibility and /changelog each did it for every section.
   */
  it("never builds a panel label by snake-casing the heading beside it", () => {
    const offenders = FILES.filter(({ text }) =>
      /TerminalTitlebar[^>]*title=\{title\.toLowerCase\(\)\.replace\(/.test(text)
    ).map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  /**
   * A colour token with no call sites is an invitation to use it. The legacy
   * matrix/neon/crt families were retired with the clamp block that protected
   * them; VT323 went with the `terminal` family it backed.
   */
  it("does not define retired colour or font families", () => {
    const config = readFileSync(join(SRC, "..", "tailwind.config.ts"), "utf8");
    for (const retired of [
      "matrix-green",
      "matrix-dark-green",
      "neon-cyan",
      "neon-pink",
      "neon-yellow",
      "terminal-bg",
      "terminal-border",
      "crt-black",
      "font-vt323",
    ]) {
      expect(config).not.toContain(`"${retired}"`);
    }
  });
});
