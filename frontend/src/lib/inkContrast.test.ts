import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The perceptual floor for text, in APCA rather than WCAG 2.x.
 *
 * A reader told us the grey was hard to read on a palette where every token
 * cleared WCAG's 4.5:1. Both things were true: WCAG 2.x is polarity-blind and
 * systematically overrates light text on dark, so `ink-min` sat at a
 * respectable 5.11:1 and an unreadable **Lc 35** — below APCA's hard floor of
 * 45 for any meaningful text — at 12px, in a thin monospace, on 61 elements of
 * the homepage.
 *
 * axe measures the ratio, so axe stayed silent. This measures what the reader
 * actually experiences. Both are kept: the ratio because it is what the law
 * and the tooling check, Lc because it is what is true.
 *
 * Thresholds (APCA / WCAG 3 draft, Bronze-ish simplified):
 *   Lc 90+  fluent body text
 *   Lc 75+  secondary prose, longer captions
 *   Lc 60+  short non-fluent labels, badges, metadata
 *   Lc 45   absolute floor — nothing meaningful ships below this
 */

/** APCA 0.98G-4g contrast, sRGB in, |Lc| out. */
function apcaLc(text: [number, number, number], bg: [number, number, number]): number {
  const EXP = 2.4,
    NTX = 0.57,
    NBG = 0.56,
    RTX = 0.62,
    RBG = 0.65,
    SCALE = 1.14,
    BLACK_CLAMP = 0.022,
    DELTA_MIN = 0.1,
    LOW_OFFSET = 0.027;
  const luminance = (p: [number, number, number]) => {
    const ch = (c: number) => Math.pow(c / 255, EXP);
    const y = 0.2126729 * ch(p[0]) + 0.7151522 * ch(p[1]) + 0.072175 * ch(p[2]);
    return y < BLACK_CLAMP ? y + Math.pow(BLACK_CLAMP - y, 1.414) : y;
  };
  const yTx = luminance(text);
  const yBg = luminance(bg);
  const contrast =
    yBg > yTx
      ? (Math.pow(yBg, NBG) - Math.pow(yTx, NTX)) * SCALE
      : (Math.pow(yBg, RBG) - Math.pow(yTx, RTX)) * SCALE;
  if (Math.abs(contrast) < DELTA_MIN) return 0;
  return Math.abs((contrast > 0 ? contrast - LOW_OFFSET : contrast + LOW_OFFSET) * 100);
}

const rgb = (hex: string): [number, number, number] => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
];

const CSS = readFileSync(join(import.meta.dirname, "../app/globals.css"), "utf8");

/** A theme's colours, from globals.css's `THEME <name>` block. */
function themeVars(name: string): Record<string, string> {
  const block = CSS.match(new RegExp(`/\\* THEME ${name} \\*/([\\s\\S]*?)/\\* THEME end \\*/`));
  if (!block) throw new Error(`no THEME ${name} block`);
  return Object.fromEntries(
    [...block[1].matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]])
  );
}

const mix = (top: string, alpha: number, bottom: string) =>
  rgb(top).map((v, i) => Math.round(alpha * v + (1 - alpha) * rgb(bottom)[i])) as [
    number,
    number,
    number,
  ];

/** Each ink token, and the smallest Lc it may reach on any ground. Dark text
 * on a warm paper tops out below Lc 90 on the sunk wash (APCA's scale
 * favours a near-white ground), so light's headings are held at 85. */
const INK_MIN = { "--ink-hi": 90, "--ink": 80, "--ink-lo": 68, "--ink-min": 58 } as const;
const FLOOR = { light: { ...INK_MIN, "--ink-hi": 85 }, dark: INK_MIN };

for (const theme of ["light", "dark"] as const) {
  const v = themeVars(theme);
  /**
   * Backgrounds text is composited on: the page, a box, the sunk wash, and a
   * 10% amber wash over a box (a tinted badge is a surface; measuring the
   * token alone is how an earlier floor passed its own check and failed in
   * the browser).
   */
  const SURFACES: Record<string, [number, number, number]> = {
    base: rgb(v["--surface-base"]),
    surface: rgb(v["--surface"]),
    raised: rgb(v["--surface-raised"]),
    amberWash: mix(v["--signal-amber"], 0.1, v["--surface"]),
  };

  describe(`${theme}: ink ramp, APCA legibility floor`, () => {
    for (const [name, min] of Object.entries(FLOOR[theme])) {
      it(`${name} stays at or above Lc ${min} on every surface`, () => {
        for (const [surfaceName, bg] of Object.entries(SURFACES)) {
          expect(apcaLc(rgb(v[name]), bg), `${name} on ${surfaceName}`).toBeGreaterThanOrEqual(min);
        }
      });
    }

    it("keeps the ramp's steps distinguishable", () => {
      // A ramp that is legible but flat is a different failure: hierarchy has
      // to survive lifting the floor.
      const onBase = Object.keys(INK_MIN).map((n) => ({ n, lc: apcaLc(rgb(v[n]), SURFACES.base) }));
      for (let i = 1; i < onBase.length; i++) {
        const gap = onBase[i - 1].lc - onBase[i].lc;
        expect(gap, `${onBase[i - 1].n} -> ${onBase[i].n}`).toBeGreaterThan(3);
      }
    });
  });

  describe(`${theme}: party hues, legible inside their own tinted badge`, () => {
    for (const name of ["--dem-blue", "--rep-red", "--ind-purple"]) {
      it(`${name} clears the label floor on a 10% wash of itself`, () => {
        expect(apcaLc(rgb(v[name]), mix(v[name], 0.1, v["--surface-base"]))).toBeGreaterThanOrEqual(
          52
        );
      });
    }
  });
}

describe("apcaLc", () => {
  it("is polarity aware, which is the whole reason it is here", () => {
    const light = rgb("#F2EEE7");
    const dark = rgb("#0E0C0A");
    // Same pair, opposite polarity — WCAG 2.x returns one number for both.
    expect(apcaLc(light, dark)).not.toBeCloseTo(apcaLc(dark, light), 1);
  });

  it("returns 0 for a pair with no usable difference", () => {
    expect(apcaLc(rgb("#808080"), rgb("#808080"))).toBe(0);
  });

  it("reproduces the failure that started this: ink-min at Lc 35", () => {
    // The pre-fix token on the pre-fix ground. 5.11:1 by WCAG, unreadable.
    expect(apcaLc(rgb("#979089"), rgb("#262010"))).toBeLessThan(45);
  });
});
