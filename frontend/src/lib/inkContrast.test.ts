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

/**
 * Backgrounds text is composited on, worst (lightest) last. `#262010` is not a
 * surface token — it is a 10% amber wash sitting on `surface.raised`, and it
 * is the lightest thing any text in the app is actually painted over. A tinted
 * badge is a surface; measuring against the token alone is how the previous
 * floor passed its own check and still failed in the browser.
 */
const SURFACES = {
  base: "#0E0C0A",
  surface: "#14110E",
  raised: "#191512",
  amberWash: "#262010",
} as const;

/** Each token, and the smallest Lc it is allowed to reach on ANY surface. */
const INK = {
  "ink-hi": { hex: "#F2EEE7", min: 90 },
  ink: { hex: "#E3DCD1", min: 80 },
  "ink-lo": { hex: "#CDC7BC", min: 68 },
  "ink-min": { hex: "#BBB5AC", min: 58 },
} as const;

/**
 * Party hues are deliberately lighter than a party's "real" colour: they are
 * set at 12px inside a badge tinted with the same hue, which is the worst
 * case in the app. Held to the label floor, not the body floor — pushing them
 * to Lc 60+ turned red into pink, which costs more legibility (of the party)
 * than it buys.
 */
const PARTY = {
  "dem-blue": "#82ACFF",
  "rep-red / signal-red": "#FF8989",
  "ind-purple": "#C995FF",
} as const;

/** A 10% wash of a colour over the page ground — how a party badge renders. */
function ownWash(hex: string): [number, number, number] {
  const c = rgb(hex);
  const base = rgb(SURFACES.base);
  return c.map((v, i) => Math.round(0.1 * v + 0.9 * base[i])) as [number, number, number];
}

describe("ink ramp — APCA legibility floor", () => {
  for (const [name, { hex, min }] of Object.entries(INK)) {
    it(`${name} stays at or above Lc ${min} on every surface`, () => {
      for (const [surfaceName, surfaceHex] of Object.entries(SURFACES)) {
        const lc = apcaLc(rgb(hex), rgb(surfaceHex));
        expect(lc, `${name} on ${surfaceName}`).toBeGreaterThanOrEqual(min);
      }
    });
  }

  it("keeps the ramp's steps distinguishable", () => {
    // A ramp that is legible but flat is a different failure: hierarchy has to
    // survive lifting the floor.
    const onBase = Object.entries(INK).map(([n, { hex }]) => ({
      n,
      lc: apcaLc(rgb(hex), rgb(SURFACES.base)),
    }));
    for (let i = 1; i < onBase.length; i++) {
      const gap = onBase[i - 1].lc - onBase[i].lc;
      expect(gap, `${onBase[i - 1].n} -> ${onBase[i].n}`).toBeGreaterThan(6);
    }
  });
});

describe("party hues — legible inside their own tinted badge", () => {
  for (const [name, hex] of Object.entries(PARTY)) {
    it(`${name} clears the label floor on a 10% wash of itself`, () => {
      expect(apcaLc(rgb(hex), ownWash(hex))).toBeGreaterThanOrEqual(52);
    });
  }
});

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
