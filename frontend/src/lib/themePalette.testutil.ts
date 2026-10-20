import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Test-only: the two themes' custom properties, read from globals.css, and
 * a resolver for the colour strings charts and maps paint with
 * (lib/chartColors): a hex, rgb()/rgba(), var(--x), or a color-mix of one
 * of those toward transparent or toward another colour.
 */

export type RGBA = [number, number, number, number];
export type Theme = "light" | "dark";

const CSS = readFileSync(join(import.meta.dirname, "../app/globals.css"), "utf8");

/** A theme's custom properties, from the block globals.css marks
 * `THEME <name>` ... `THEME end`. */
export function themeVars(name: Theme): Record<string, string> {
  const block = CSS.match(new RegExp(`/\\* THEME ${name} \\*/([\\s\\S]*?)/\\* THEME end \\*/`));
  if (!block) throw new Error(`no THEME ${name} block in globals.css`);
  return Object.fromEntries(
    [...block[1].matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()])
  );
}

export const THEMES: Record<Theme, Record<string, string>> = {
  light: themeVars("light"),
  dark: themeVars("dark"),
};

/** Substitute every var(--x) in `s` with the theme's value. */
function substitute(s: string, theme: Theme): string {
  return s.replace(/var\((--[\w-]+)\)/g, (_, name: string) => {
    const v = THEMES[theme][name];
    if (v === undefined) throw new Error(`${name} is not a ${theme} theme variable`);
    return substitute(v, theme);
  });
}

/** A percentage as written in a color-mix: "45%", or calc() arithmetic of
 * numbers and one trailing "* 100%". */
function percent(s: string): number {
  const expr = s.replace(/^calc\((.*)\)$/, "$1").replace(/(\d)%/g, "$1/100");
  if (!/^[\d.\s+\-*/()]+$/.test(expr)) throw new Error(`unreadable percentage ${s}`);
  return Function(`return (${expr})`)() as number;
}

/** Split at top-level commas (not inside parentheses). */
function args(s: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "(") depth++;
    else if (s[i] === ")") depth--;
    else if (s[i] === "," && depth === 0) {
      out.push(s.slice(start, i).trim());
      start = i + 1;
    }
  }
  out.push(s.slice(start).trim());
  return out;
}

/** "colour [pct]" -> [colour, pct or null]. */
function colorAndPercent(s: string): [string, number | null] {
  const m = s.match(/^(.*\))\s+(calc\(.*\)|[\d.]+%)$/) ?? s.match(/^(\S+)\s+(calc\(.*\)|[\d.]+%)$/);
  return m ? [m[1], percent(m[2])] : [s, null];
}

export function resolve(color: string, theme: Theme): RGBA {
  const c = substitute(color.trim(), theme);
  if (c === "transparent") return [0, 0, 0, 0];
  if (/^#[0-9a-fA-F]{6}$/.test(c)) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
    return [r, g, b, 1];
  }
  const fn = c.match(/^rgba?\((.*)\)$/);
  if (fn) {
    const [r, g, b, a = 1] = fn[1]
      .split(/[\s,/]+/)
      .filter(Boolean)
      .map(Number);
    return [r, g, b, a];
  }
  const mix = c.match(/^color-mix\(in srgb,\s*(.*)\)$/);
  if (mix) {
    const [a, b] = args(mix[1]);
    const [ca, pa] = colorAndPercent(a);
    const [cb, pb] = colorAndPercent(b);
    const p = pa ?? (pb == null ? 0.5 : 1 - pb);
    const A = resolve(ca, theme);
    const B = resolve(cb, theme);
    // Premultiplied interpolation, as CSS Color 4 mixes.
    const alpha = A[3] * p + B[3] * (1 - p);
    if (alpha === 0) return [0, 0, 0, 0];
    const ch = (i: number) => (A[i] * A[3] * p + B[i] * B[3] * (1 - p)) / alpha;
    return [ch(0), ch(1), ch(2), alpha];
  }
  throw new Error(`unreadable colour ${color}`);
}

/** `top` composited over an opaque `bottom`. */
export function over(top: RGBA, bottom: RGBA): RGBA {
  const a = top[3];
  return [0, 1, 2].map((i) => a * top[i] + (1 - a) * bottom[i]).concat(1) as RGBA;
}

function luminance([r, g, b]: RGBA): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** WCAG 2.x contrast of two opaque colours. */
export function ratio(a: RGBA, b: RGBA): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The contrast of `fg` drawn over `bg` (each composited over `ground`)
 * against `bg`, all in `theme`. */
export function contrastOn(fg: string, bg: string, theme: Theme, ground = "var(--surface)") {
  const base = over(resolve(bg, theme), resolve(ground, theme));
  return ratio(over(resolve(fg, theme), base), base);
}
