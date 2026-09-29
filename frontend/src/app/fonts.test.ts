import { describe, expect, it, vi } from "vitest";

// next/font/local is compiled away by Next; under vitest it is replaced by a
// stand-in that names each call after its first file, and gives it a
// fallback face only when next/font would generate one.
vi.mock("next/font/local", () => ({
  default: (options: { src: string | { path: string }[]; adjustFontFallback?: false }) => {
    const path = typeof options.src === "string" ? options.src : options.src[0].path;
    const name = path.replace(/^.*\//, "").replace(/\.woff2$/, "");
    const fontFamily =
      options.adjustFontFallback === false ? `'${name}'` : `'${name}', '${name} Fallback'`;
    return { style: { fontFamily }, variable: `${name}-variable`, className: name };
  },
}));

const { familyStack, fontVariables } = await import("./fonts");

describe("font family stacks", () => {
  it("puts every subset face ahead of the primary face's fallback", () => {
    expect(
      familyStack(
        { style: { fontFamily: "'latin', 'latin Fallback'" } },
        { style: { fontFamily: "'ext'" } },
        { style: { fontFamily: "'viet'" } }
      )
    ).toBe("'latin', 'ext', 'viet', 'latin Fallback'");
  });

  it("builds Archivo from its three subsets, latin first, fallback last", () => {
    const stack = (fontVariables as Record<string, string>)["--font-archivo"].split(", ");
    expect(stack).toEqual([
      "'archivo-latin-wght-normal'",
      "'archivo-latin-ext-wght-normal'",
      "'archivo-vietnamese-wght-normal'",
      "'archivo-latin-wght-normal Fallback'",
    ]);
  });

  it("builds Press Start 2P from all five of Google's subsets", () => {
    const stack = (fontVariables as Record<string, string>)["--font-press-start"].split(", ");
    expect(stack).toHaveLength(6);
    expect(stack[0]).toBe("'press-start-2p-latin-400-normal'");
    expect(stack.at(-1)).toBe("'press-start-2p-latin-400-normal Fallback'");
  });
});
