import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import config from "../../tailwind.config";
import { SERIES, STATUS } from "@/components/admin/charts/palette";
import { BIN_OPACITY } from "./electionMap";
import { FAINT, UNKNOWN_SHARE } from "./results";
import { chartMark, fade, partyMark } from "./chartColors";
import { THEMES, resolve, ratio as ratioOf, type Theme } from "./themePalette.testutil";

/**
 * WCAG 2.2 AA contrast (SC 1.4.3), computed from the palette itself.
 *
 * inkContrast.test.ts holds the ink ramp to an APCA floor, which is what a
 * reader experiences on a dark ground; this holds every colour pair the site
 * actually paints to the ratio axe and the law measure. Both read the hexes,
 * never a number someone typed beside one: the config's comments quoted
 * 9.06:1 for a hue that measures 8.03:1, and 9.74:1 for one at 4.89:1.
 *
 * Three checks:
 *   1. every text token on every ground the site has, solid and tinted;
 *   2. every `text-*` / `bg-*` pair written together in one class string
 *      (the same variant: `hover:`, `aria-selected:` ...), composited the
 *      way the browser does;
 *   3. the ink ramp on every translucent wash a class string uses, since
 *      text inside a tinted box inherits its colour from further up;
 *   4. chart and map marks (WCAG 1.4.11, 3:1) on both grounds of each
 *      theme: the party marks and every strength the maps fade them to,
 *      the categorical series, and every colour the API sends for a chart
 *      (backend config_definitions.py) as chartMark draws it. The Court's
 *      heat ramp and split shades are in court.test.ts, the results maps'
 *      textures in results.test.ts. A chart takes its colours from the
 *      theme (lib/chartColors), never a dark panel of its own.
 *
 * jsdom does no layout, so the browser half of this (text drawn over a chart,
 * a map tile, an image) is the production-build sweep, scripts/axe-routes.mjs.
 */

type RGB = [number, number, number];

const colors = (config.theme?.extend?.colors ?? {}) as Record<
  string,
  string | Record<string, string>
>;

const CSS = readFileSync(join(import.meta.dirname, "../app/globals.css"), "utf8");

/** The palette flattened to Tailwind's class names (`ink`, `ink-hi`,
 * `phos-mid`), each resolved to its hex in `theme`. */
function tokens(theme: keyof typeof THEMES): Record<string, string> {
  const vars = THEMES[theme];
  const resolve = (v: string) => {
    const name = v.match(/^var\((--[\w-]+)\)$/)?.[1];
    if (!name || !vars[name]) throw new Error(`${v} is not a ${theme} theme variable`);
    return vars[name];
  };
  const out: Record<string, string> = { black: "#000000" };
  for (const [name, value] of Object.entries(colors)) {
    if (typeof value === "string") out[name] = resolve(value);
    else
      for (const [step, v] of Object.entries(value))
        out[step === "DEFAULT" ? name : `${name}-${step}`] = resolve(v);
  }
  return out;
}

const rgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB;

function luminance([r, g, b]: RGB): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function ratio(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `top` at `alpha` over an opaque `bottom`, as the browser composites it. */
const over = (top: RGB, alpha: number, bottom: RGB): RGB =>
  top.map((c, i) => Math.round(alpha * c + (1 - alpha) * bottom[i])) as RGB;

/** Class names a token can take; the same in both themes. */
const TOKEN_NAMES = Object.keys(tokens("light"));

const AA = 4.5;
const AA_LARGE = 3;

/** Every check, for one theme: its tokens, its grounds, and the worst ground
 * a translucent fill can sit on (a wash over the page, a box or a sunk row). */
function suite(theme: keyof typeof THEMES) {
  const TOKENS = tokens(theme);
  // A translucent fill sits on the page or on a box, never on the sunk
  // wash (itself a fill), so those two are what it is composited over.
  const BASES = (["surface-base", "surface"] as const).map((g) => rgb(TOKENS[g]));
  /** Text tokens: everything but the grounds, the overlay and black. */
  const TEXT = Object.keys(TOKENS).filter(
    (t) => !t.startsWith("surface") && t !== "white" && t !== "black"
  );
  /** Every ground text sits on. A tinted badge is a ground too. */
  const GROUNDS: Record<string, RGB> = {
    "surface-base": BASES[0],
    surface: BASES[1],
    "surface-raised": rgb(TOKENS["surface-raised"]),
    ...Object.fromEntries(
      BASES.map((b, i) => [
        `signal-amber/10 on ground ${i}`,
        over(rgb(TOKENS["signal-amber"]), 0.1, b),
      ])
    ),
  };
  /** The lowest ratio of `fg` over `bg`, each composited over every base. */
  const worst = (fg: { token: string; alpha: number }, bg: { token: string; alpha: number }) =>
    Math.min(
      ...BASES.map((base) => {
        const ground = over(rgb(TOKENS[bg.token]), bg.alpha, base);
        return ratio(over(rgb(TOKENS[fg.token]), fg.alpha, ground), ground);
      })
    );

  describe(`${theme}: palette: every text token on every ground`, () => {
    for (const token of TEXT) {
      it(`${token} clears ${AA}:1`, () => {
        for (const [ground, bg] of Object.entries(GROUNDS)) {
          expect(ratio(rgb(TOKENS[token]), bg), `${token} on ${ground}`).toBeGreaterThanOrEqual(AA);
        }
      });
    }

    it("keeps the system-dark block equal to the chosen-dark block", () => {
      const chosen = CSS.match(/:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/)?.[1] ?? "";
      const vars = Object.fromEntries(
        [...chosen.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()])
      );
      // No other selector borrows the dark palette: a chart on a dark plate
      // in the light theme is what this file exists to rule out.
      expect(CSS).not.toMatch(/data-theme="dark"\],\s*\./);
      expect(vars).toEqual(THEMES.dark);
      expect(Object.keys(THEMES.dark).sort()).toEqual(Object.keys(THEMES.light).sort());
    });
  });

  // ── Pairs in use ────────────────────────────────────────────────────────────

  const SRC = join(import.meta.dirname, "..");

  function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((entry) => {
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) return sourceFiles(path);
      return /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
    });
  }

  /** Every string a class list can live in: quoted strings, and the static
   * parts of template literals (each arm of a `${a ? "x" : "y"}` is a quoted
   * string of its own, so arms are never read as one list). */
  function classStrings(src: string): { text: string; line: number }[] {
    const out: { text: string; line: number }[] = [];
    const re = /"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g;
    for (const m of src.matchAll(re)) {
      const raw = m[1] ?? m[2] ?? (m[3] ?? "").replace(/\$\{[^}]*\}/g, " ");
      if (/\b(text|bg)-/.test(raw))
        out.push({ text: raw, line: src.slice(0, m.index).split("\n").length });
    }
    return out;
  }

  const TOKEN_RE = TOKEN_NAMES.sort((a, b) => b.length - a.length)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&"))
    .join("|");
  const CLASS_RE = new RegExp(
    `^((?:[a-z-]+(?:\\[[^\\]]+\\])?:)*)(text|bg)-(${TOKEN_RE})(?:/(\\[[0-9.]+\\]|[0-9]+))?$`
  );

  type Paint = { token: string; alpha: number };

  function parseAlpha(a: string | undefined): number {
    if (!a) return 1;
    return a.startsWith("[") ? Number(a.slice(1, -1)) : Number(a) / 100;
  }

  /** Variant prefix ("" for the resting state) -> its text and background. */
  function paints(classes: string[]): Map<string, { text?: Paint; bg?: Paint }> {
    const byVariant = new Map<string, { text?: Paint; bg?: Paint }>();
    for (const c of classes) {
      const m = c.match(CLASS_RE);
      if (!m) continue;
      const [, variant, kind, token, alpha] = m;
      const entry = byVariant.get(variant) ?? {};
      entry[kind as "text" | "bg"] = { token, alpha: parseAlpha(alpha) };
      byVariant.set(variant, entry);
    }
    return byVariant;
  }

  /** Variants whose colours never reach a reader as text: an inactive control
   * (WCAG exempts it) and selection/scrollbar chrome. */
  const EXEMPT_VARIANT = /(^|:)(disabled|selection|file|marker):$/;

  const isLarge = (classes: string[]) =>
    classes.some((c) => /^text-([2-9]xl|xl)$/.test(c)) ||
    (classes.includes("text-lg") && classes.some((c) => /^font-(bold|semibold)$/.test(c)));

  describe(`${theme}: pairs in use: text and background written together`, () => {
    it(`clear ${AA}:1 (${AA_LARGE}:1 for large text) in every state`, () => {
      const failures: string[] = [];
      for (const file of sourceFiles(SRC)) {
        for (const { text, line } of classStrings(readFileSync(file, "utf8"))) {
          const classes = text.split(/\s+/).filter(Boolean);
          const byVariant = paints(classes);
          const rest = byVariant.get("") ?? {};
          for (const [variant, own] of byVariant) {
            if (EXEMPT_VARIANT.test(variant)) continue;
            const fg = own.text ?? rest.text;
            const bg = own.bg ?? rest.bg;
            if (!fg || !bg) continue;
            const r = worst(fg, bg);
            const floor = isLarge(classes) ? AA_LARGE : AA;
            if (r < floor)
              failures.push(
                `${file.slice(SRC.length + 1)}:${line} ${variant}text-${fg.token} on bg-${bg.token}` +
                  `${bg.alpha < 1 ? `/${bg.alpha}` : ""}: ${r.toFixed(2)}:1`
              );
          }
        }
      }
      expect(failures).toEqual([]);
    });

    it("finds the pairs it is meant to (the scan is not silently empty)", () => {
      const found = paints("bg-phos text-surface-base hover:bg-signal-cyan".split(" "));
      expect(found.get("")).toEqual({
        text: { token: "surface-base", alpha: 1 },
        bg: { token: "phos", alpha: 1 },
      });
      expect(found.get("hover:")?.bg?.token).toBe("signal-cyan");
      // A failing pair is caught: phosphor text on its own fill.
      expect(worst({ token: "phos", alpha: 1 }, { token: "phos", alpha: 1 })).toBeLessThan(AA);
    });
  });

  describe(`${theme}: the ink ramp on every translucent wash in use`, () => {
    it(`clears ${AA}:1`, () => {
      const washes = new Map<string, RGB>();
      for (const file of sourceFiles(SRC))
        for (const { text } of classStrings(readFileSync(file, "utf8")))
          for (const c of text.split(/\s+/)) {
            const m = c.match(CLASS_RE);
            if (!m || m[2] !== "bg" || !m[4]) continue;
            const alpha = parseAlpha(m[4]);
            // A wash text sits in. Denser fills are bars and swatches, and a
            // pair written with one is checked above.
            if (alpha > 0.2 || m[3].startsWith("surface") || m[3] === "black") continue;
            BASES.forEach((base, i) =>
              washes.set(`bg-${m[3]}/${alpha} on ground ${i}`, over(rgb(TOKENS[m[3]]), alpha, base))
            );
          }
      expect(washes.size).toBeGreaterThan(5);
      const failures: string[] = [];
      for (const ink of ["ink-hi", "ink", "ink-lo", "ink-min"])
        for (const [wash, bg] of washes) {
          const r = ratio(rgb(TOKENS[ink]), bg);
          if (r < AA) failures.push(`${ink} on ${wash}: ${r.toFixed(2)}:1`);
        }
      expect(failures).toEqual([]);
    });
  });

  describe(`${theme}: a search match (<mark>, globals.css)`, () => {
    it(`ink-hi on --highlight clears ${AA}:1, and the highlight is opaque`, () => {
      const hl = THEMES[theme]["--highlight"];
      // Opaque, so no card it sits in can lower the ratio (the old
      // bg-signal-cyan/20 wash measured 4.34:1 inside a sunk card).
      expect(hl).toMatch(/^#[0-9a-f]{6}$/i);
      expect(ratio(rgb(TOKENS["ink-hi"]), rgb(hl))).toBeGreaterThanOrEqual(AA);
      expect(CSS).toMatch(/\bmark \{\s*background: var\(--highlight\);\s*color: var\(--ink-hi\);/);
    });
  });

  describe(`${theme}: party hues inside their own tinted badge`, () => {
    for (const hue of ["dem-blue", "rep-red", "ind-purple"]) {
      it(`${hue} clears ${AA}:1 on a 10% wash of itself`, () => {
        expect(worst({ token: hue, alpha: 1 }, { token: hue, alpha: 0.1 })).toBeGreaterThanOrEqual(
          AA
        );
      });
    }
  });
  describe(`${theme}: chart and map marks clear ${AA_LARGE}:1 on every ground`, () => {
    const grounds = ["var(--surface-base)", "var(--surface)"].map((g) => resolve(g, theme));
    /** The lowest ratio of `mark`, composited over each ground, to it. */
    const markWorst = (mark: string) =>
      Math.min(
        ...grounds.map((g) => {
          const [r, gr, b, a] = resolve(mark, theme as Theme);
          const drawn = [0, 1, 2].map((i) => a * [r, gr, b][i] + (1 - a) * g[i]).concat(1);
          return ratioOf(drawn as typeof g, g);
        })
      );
    const check = (marks: string[]) => {
      const failures = marks
        .map((m) => [m, markWorst(m)] as const)
        .filter(([, r]) => r < AA_LARGE)
        .map(([m, r]) => `${m}: ${r.toFixed(2)}:1`);
      expect(failures).toEqual([]);
    };

    it("the party marks, at every strength the maps draw them", () => {
      // The seat map's bins, and the results maps' faintest-to-solid steps
      // (resultFill / aggregateOpacity in lib/results).
      const strengths = [...Object.values(BIN_OPACITY), FAINT, UNKNOWN_SHARE, 0.675, 0.9];
      check(
        ["DEM", "REP", "IND"].flatMap((p) => [
          partyMark(p),
          ...strengths.map((s) => fade(partyMark(p), s)),
        ])
      );
    });

    it("the categorical series and run status", () => {
      check([...SERIES, ...Object.values(STATUS)]);
    });

    it("every colour the API sends for a chart, as chartMark draws it", () => {
      const py = readFileSync(
        join(import.meta.dirname, "../../../backend/app/config_definitions.py"),
        "utf8"
      );
      const sent = [
        ...new Set([...py.matchAll(/"color":\s*"(#[0-9a-fA-F]{6})"/g)].map((m) => m[1])),
      ];
      expect(sent.length).toBeGreaterThan(20);
      check(sent.map(chartMark));
    });
  });
}

suite("light");
suite("dark");
