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

/**
 * Comments name the retired values on purpose — they are the record of what
 * was wrong, and a scanner that flagged them would push the next person to
 * delete the reason rather than the problem. Strip them once here rather than
 * having each test invent its own line filter (a `//`-and-`*` filter missed
 * the interior lines of a JSX `{/* … *\/}` block, which is how this comment
 * came to exist).
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

const FILES = sourceFiles(SRC).map((path) => ({
  path: path.slice(SRC.length + 1),
  text: stripComments(readFileSync(path, "utf8")),
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

  /**
   * Tailwind's own palette is not this design system. The migration replaced
   * the `matrix-*` / `neon-*` families and the audit checked for exactly
   * those, so 156 stock utilities across 20 hues — `emerald-400`, `blue-400`,
   * `teal-400`, `green-400`, `purple-400`, `red-500` … — went straight
   * through untouched, several of them inside the same ternary whose other
   * arm had been converted. Three of the text ones measured under the 4.5:1
   * floor (`text-emerald-400/50` at 3.13, `/60` at 4.12, `text-blue-400/70`
   * at 4.18) on views the browser sweep never reached: collapsed `<details>`
   * sections and dynamic routes that need a live backend.
   */
  it("uses no stock-Tailwind colour utilities", () => {
    const STOCK =
      "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|" +
      "blue|indigo|violet|purple|fuchsia|pink|rose";
    const utility = new RegExp(
      String.raw`\b(?:text|bg|border|border-[lrtb]|fill|stroke|ring|divide|from|to|via|decoration|outline|shadow)-(?:${STOCK})-(?:50|[1-9]00|950)(?:/\d+)?\b`,
      "g"
    );
    const offenders = FILES.flatMap(({ path, text }) =>
      [...text.matchAll(utility)].map((m) => `${path}: ${m[0]}`)
    );
    expect(offenders).toEqual([]);
  });

  /**
   * `text-signal-red bg-signal-red` renders red on red. The migration rewrote
   * `bg-red-500/10` to `bg-signal-red` and dropped the `/10` with it, which
   * made every SELL and EXCHANGE stock-trade badge, the monitor alert pill,
   * and every highlighted search term on /explore a solid block of colour
   * with its own label invisible inside it.
   */
  it("never paints text and its background with the same token", () => {
    const TOKENS =
      String.raw`(phos(?:-mid|-dim)?|signal-(?:cyan|amber|orange|red|magenta)|ink(?:-hi|-lo|-min)?|` +
      String.raw`dem-blue|rep-red|ind-purple)`;
    // Per string literal, not per line: a party map naming
    // `color: "text-dem-blue"` beside `rule: "bg-dem-blue"` on one line is
    // two classes for two different elements, not text on its own fill.
    const offenders: string[] = [];
    for (const { path, text } of FILES) {
      for (const literal of text.match(/"[^"\n]*"|`[^`]*`/g) ?? []) {
        for (const m of literal.matchAll(new RegExp(String.raw`\btext-` + TOKENS + String.raw`\b`, "g"))) {
          // The same token as a background at full opacity, i.e. with no /NN.
          if (new RegExp(String.raw`\bbg-` + m[1] + String.raw`\b(?!/)`).test(literal)) {
            offenders.push(`${path}: text-${m[1]} on bg-${m[1]}`);
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  /**
   * Hard corners are named as part of the identity the rebrand keeps. Two
   * avatars and one chart bar kept a radius while the other four avatars did
   * not.
   */
  it("keeps corners hard", () => {
    const offenders = FILES.flatMap(({ path, text }) =>
      // className values only — `scoreVersions.ts` uses the English word
      // "rounded" describing a scoring change, which is not a corner.
      [...text.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)]
        .flatMap((attr) => [...(attr[1] ?? attr[2] ?? "").matchAll(/\brounded(?:-[a-z]+)?(?:-\[[^\]]+\])?\b/g)])
        .map((m) => `${path}: ${m[0]}`)
    );
    expect(offenders).toEqual([]);
  });
});
