import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import { TYPE } from "./typeStyles";

/**
 * One style per role (lib/typeStyles.ts), held in the code. The browser half
 * (each h1, h2, h3 and th as computed, on real pages, at each width and at
 * 200% text) is scripts/axe-routes.mjs.
 */

const SRC = join(import.meta.dirname, "..");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

/** The source with its comments blanked: prose may name a class. */
const code = (p: string) =>
  readFileSync(p, "utf8").replace(/\/\*[\s\S]*?\*\/|(?<![:"'`\w\\])\/\/[^\n]*/g, "");

const SOURCES = files(SRC).map((p) => ({ path: relative(SRC, p), text: code(p) }));

function hits(re: RegExp, skip: (path: string) => boolean = () => false): string[] {
  return SOURCES.filter((f) => !skip(f.path)).flatMap((f) =>
    [...f.text.matchAll(re)].map((m) => `${f.path}: ${m[0]}`)
  );
}

// The opening tag of an h1 to h3, with its attributes (braces balanced
// enough for a className expression).
const HEADING = /<(h[123])\b((?:[^>{]|\{(?:[^{}]|\{[^{}]*\})*\})*)>/g;
const ALLOWED: Record<string, (keyof typeof TYPE)[]> = {
  h1: ["title", "display"],
  h2: ["section", "boxTitle", "kicker", "kickerToned"],
  h3: ["boxTitle", "section", "kicker", "kickerToned", "label"],
};
const SCALE_SIZE = /\btext-(small|body|dek|box|section|figure|figure-lg|title|display)\b/;

describe("the type scale", () => {
  it("has no one-off font size: no text-[Npx] or text-[Nrem] (an em, relative to its line, is fine)", () => {
    expect(hits(/\btext-\[[0-9.]+(px|rem)\]/g)).toEqual([]);
  });

  it("has none of Tailwind's own sizes: the theme switches them off", () => {
    expect(hits(/(?<![\w-])(?:[\w@\[\]&>:-]+:)?text-(xs|sm|base|lg|[2-9]?xl)(?![\w-])/g)).toEqual(
      []
    );
    const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
    expect(css).toMatch(/@theme \{[^}]*--text-\*: initial;/);
    for (const role of [
      "display",
      "title",
      "section",
      "box",
      "dek",
      "body",
      "small",
      "figure",
      "figure-lg",
    ])
      expect(css, role).toContain(`--text-${role}:`);
  });

  it("sets the serif only through TYPE", () => {
    expect(hits(/\bfont-display\b/g, (p) => p === "lib/typeStyles.ts")).toEqual([]);
  });

  it("sizes an h1 to h3 only from TYPE, with a role its level allows", () => {
    const bad: string[] = [];
    for (const f of SOURCES) {
      for (const m of f.text.matchAll(HEADING)) {
        const [tag, attrs] = [m[1], m[2]];
        const roles = [...attrs.matchAll(/TYPE\.(\w+)/g)].map((r) => r[1]);
        if (roles.some((r) => !ALLOWED[tag].includes(r as keyof typeof TYPE)))
          bad.push(`${f.path}: <${tag}> as ${roles.join(", ")}`);
        else if (!roles.length && (SCALE_SIZE.test(attrs) || /font-(semibold|normal)/.test(attrs)))
          bad.push(`${f.path}: <${tag}${attrs.slice(0, 80)}>`);
      }
    }
    expect(bad).toEqual([]);
  });
});
