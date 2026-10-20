import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { OG_COLOR } from "./ogTheme";

const CSS = readFileSync(join(import.meta.dirname, "../app/globals.css"), "utf8");
const LIGHT = Object.fromEntries(
  [
    ...(CSS.match(/\/\* THEME light \*\/([\s\S]*?)\/\* THEME end \*\//)?.[1] ?? "").matchAll(
      /(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g
    ),
  ].map((m) => [m[1], m[2].toLowerCase()])
);

const FROM_CSS: Partial<Record<keyof typeof OG_COLOR, string>> = {
  parchment: "--surface-base",
  paper: "--surface",
  inkHi: "--ink-hi",
  ink: "--ink",
  inkLo: "--ink-lo",
  inkMin: "--ink-min",
  accent: "--phos",
  amber: "--signal-amber",
  orange: "--signal-orange",
  red: "--signal-red",
  dem: "--dem-blue",
  rep: "--rep-red",
  ind: "--ind-purple",
};

function luminance(hex: string): number {
  const lin = (i: number) => {
    const s = parseInt(hex.slice(i, i + 2), 16) / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(1) + 0.7152 * lin(3) + 0.0722 * lin(5);
}
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe("OG image palette", () => {
  it.each(Object.entries(FROM_CSS))("%s equals globals.css's light %s", (key, cssVar) => {
    expect(OG_COLOR[key as keyof typeof OG_COLOR]).toBe(LIGHT[cssVar]);
  });

  // Every colour a card sets text in, on both grounds a card uses: AA for
  // body text, which a thumbnail shrinks to small text.
  const TEXT = [
    "inkHi",
    "ink",
    "inkLo",
    "inkMin",
    "accent",
    "amber",
    "orange",
    "red",
    "dem",
    "rep",
    "ind",
  ] as const;
  it.each(TEXT)("%s text clears 4.5:1 on parchment and paper", (key) => {
    expect(contrast(OG_COLOR[key], OG_COLOR.parchment)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(OG_COLOR[key], OG_COLOR.paper)).toBeGreaterThanOrEqual(4.5);
  });

  it("rules clear 3:1 on parchment (non-text, SC 1.4.11)", () => {
    expect(contrast(OG_COLOR.rule, OG_COLOR.parchment)).toBeGreaterThanOrEqual(3);
  });
});
