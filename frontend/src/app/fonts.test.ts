import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

type Src = string | { path: string; weight?: string }[];
type Options = {
  src: Src;
  weight?: string;
  preload?: boolean;
  adjustFontFallback?: false;
  declarations?: { prop: string; value: string }[];
};

// next/font/local is compiled away by Next; under vitest a stand-in records
// every call's options and answers the way next/font does: a family named
// after the call, with a " Fallback" family unless adjustFontFallback is off.
const calls = vi.hoisted(() => [] as Options[]);
vi.mock("next/font/local", () => ({
  default: (options: Options) => {
    calls.push(options);
    const name = `face${calls.length}`;
    const fontFamily =
      options.adjustFontFallback === false ? `'${name}'` : `'${name}', '${name} Fallback'`;
    return { style: { fontFamily }, variable: `${name}-variable`, className: name };
  },
}));

const { ARCHIVO_FALLBACK, familyStack, fontVariables } = await import("./fonts");

const FONTS = join(__dirname, "fonts");
type Entry = {
  subset: string;
  file: string;
  weights: string[];
  unicodeRange: string;
  preload: boolean;
};
const manifest: { fonts: Record<string, Entry[]> } = JSON.parse(
  readFileSync(join(FONTS, "manifest.json"), "utf8")
);

function files(src: Src): string[] {
  return typeof src === "string" ? [src] : src.map((s) => s.path);
}

function weights(options: Options): string[] {
  return typeof options.src === "string"
    ? [options.weight ?? ""]
    : options.src.map((s) => s.weight ?? "");
}

function callFor(file: string): Options {
  const found = calls.filter((c) => files(c.src).every((f) => f === `./fonts/${file}`));
  expect(found, file).toHaveLength(1);
  return found[0];
}

describe("the fonts match what the fetch script recorded from Google", () => {
  const entries = Object.values(manifest.fonts).flat();

  it("loads every fetched file exactly once, and nothing else", () => {
    expect(calls.map((c) => files(c.src)[0]).sort()).toEqual(
      entries.map((e) => `./fonts/${e.file}`).sort()
    );
  });

  it.each(entries)("$file keeps Google's unicode-range, weights and preload", (entry) => {
    const options = callFor(entry.file);
    const range = options.declarations?.find((d) => d.prop === "unicode-range")?.value;
    expect(range).toBe(entry.unicodeRange);
    expect(weights(options)).toEqual(entry.weights);
    expect(options.preload ?? true).toBe(entry.preload);
  });
});

describe("font family stacks", () => {
  it("puts every subset face ahead of every fallback", () => {
    expect(
      familyStack(
        [
          { style: { fontFamily: "'latin', 'latin Fallback'" } },
          { style: { fontFamily: "'ext'" } },
          { style: { fontFamily: "'viet'" } },
        ],
        "'extra'"
      )
    ).toBe("'latin', 'ext', 'viet', 'latin Fallback', 'extra'");
  });

  it("ends Archivo's stack with the generated 400-weight fallback", () => {
    const stack = (fontVariables as Record<string, string>)["--font-archivo"].split(", ");
    expect(stack).toHaveLength(manifest.fonts.archivo.length + 1);
    expect(stack.at(-1)).toBe(`'${ARCHIVO_FALLBACK}'`);
    expect(stack.slice(0, -1).every((name) => !name.includes("Fallback"))).toBe(true);
    const css = readFileSync(join(FONTS, "fallback.css"), "utf8");
    expect(css).toContain(`font-family: "${ARCHIVO_FALLBACK}"`);
  });

  it("builds Press Start 2P from every subset, fallback last", () => {
    const stack = (fontVariables as Record<string, string>)["--font-press-start"].split(", ");
    expect(stack).toHaveLength(manifest.fonts["press-start-2p"].length + 1);
    expect(stack.at(-1)).toMatch(/ Fallback'$/);
    expect(stack.slice(0, -1).every((name) => !name.includes("Fallback"))).toBe(true);
  });
});
