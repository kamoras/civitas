import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ABOUT_CHAPTERS,
  LEGACY_ABOUT_ANCHORS,
  adjacentChapters,
  legacyAboutTarget,
} from "./aboutPages";
import { LEGACY_REF_NUMBERS, REFERENCES } from "@/components/about/references";

const APP = join(import.meta.dirname, "..", "app");

/** Source of the page file behind an /about route. */
function pageSource(href: string): string {
  return readFileSync(join(APP, ...href.split("/").filter(Boolean), "page.tsx"), "utf8");
}

/** Every `id` a page renders: `<Section id="x">`, `<Sub id="x">`, raw `id="x"`. */
function idsOn(href: string): Set<string> {
  return new Set([...pageSource(href).matchAll(/\bid="([a-z0-9-]+)"/g)].map((m) => m[1]));
}

describe("ABOUT_CHAPTERS", () => {
  it("has a page for every chapter, and a chapter for every page", () => {
    for (const c of ABOUT_CHAPTERS)
      expect(existsSync(join(APP, ...c.href.split("/").filter(Boolean), "page.tsx"))).toBe(true);
    const onDisk = readdirSync(join(APP, "about"), { withFileTypes: true })
      .filter((d) => d.isDirectory() && existsSync(join(APP, "about", d.name, "page.tsx")))
      .map((d) => `/about/${d.name}`)
      .sort();
    expect(onDisk).toEqual(ABOUT_CHAPTERS.map((c) => c.href).sort());
  });

  it("gives each chapter its neighbours", () => {
    const first = ABOUT_CHAPTERS[0];
    const last = ABOUT_CHAPTERS[ABOUT_CHAPTERS.length - 1];
    expect(adjacentChapters(first.href).prev).toBeNull();
    expect(adjacentChapters(first.href).next).toBe(ABOUT_CHAPTERS[1]);
    expect(adjacentChapters(last.href).next).toBeNull();
    expect(adjacentChapters("/about")).toEqual({ prev: null, next: null });
  });
});

describe("legacy /about anchors", () => {
  it("forwards every old section id to a page that has the fragment it names", () => {
    for (const [hash, target] of Object.entries(LEGACY_ABOUT_ANCHORS)) {
      expect(legacyAboutTarget(`#${hash}`, LEGACY_REF_NUMBERS)).toBe(target);
      const [path, frag] = target.split("#");
      if (frag) expect(idsOn(path).has(frag)).toBe(true);
    }
  });

  it("forwards numbered references to their slug, and leaves unknown ones alone", () => {
    expect(legacyAboutTarget("#ref-5", LEGACY_REF_NUMBERS)).toBe("/about/references#stratmann2005");
    expect(legacyAboutTarget("#ref-2", LEGACY_REF_NUMBERS)).toBeNull();
    expect(legacyAboutTarget("#principles", LEGACY_REF_NUMBERS)).toBeNull();
    expect(legacyAboutTarget("", LEGACY_REF_NUMBERS)).toBeNull();
    for (const slug of Object.values(LEGACY_REF_NUMBERS)) expect(REFERENCES).toHaveProperty(slug);
  });
});

describe("links between About pages", () => {
  // A chapter's `#fragment` link that names no id lands at the top of the
  // page, silently. Pages are server components with static ids, so the
  // source is enough to check.
  it("only point at fragments that exist", () => {
    const pages = ["/about", ...ABOUT_CHAPTERS.map((c) => c.href)];
    const broken: string[] = [];
    for (const from of pages) {
      for (const m of pageSource(from).matchAll(/href=(?:"|\{`)(\/about[a-z/-]*)?#([a-z0-9-]+)/g)) {
        const to = m[1] ?? from;
        if (m[2].includes("$")) continue;
        if (!pages.includes(to) || !idsOn(to).has(m[2])) broken.push(`${from} → ${to}#${m[2]}`);
      }
    }
    expect(broken).toEqual([]);
  });

  it("cites only references that are listed, and lists only references that are cited", () => {
    const cited = new Set(
      ABOUT_CHAPTERS.flatMap((c) =>
        [...pageSource(c.href).matchAll(/<Cite id="([a-z0-9]+)"/g)].map((m) => m[1])
      )
    );
    expect([...cited].filter((id) => !(id in REFERENCES))).toEqual([]);
    expect(Object.keys(REFERENCES).filter((id) => !cited.has(id))).toEqual([]);
  });
});
