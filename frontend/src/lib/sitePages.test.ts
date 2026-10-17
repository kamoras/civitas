import { existsSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { SECTIONS } from "./routes";
import { ABOUT_CHAPTERS, ABOUT_OVERVIEW_HREF } from "./aboutPages";

// Site search's page list and section anchors are backend data
// (backend/app/data/site_pages.json, site_search_sections.json); these hold
// them to the frontend's own navigation and to the ids the pages render.
const ROOT = resolve(__dirname, "../../..");
const pages: { label: string; href: string; aliases: string[] }[] = JSON.parse(
  readFileSync(resolve(ROOT, "backend/app/data/site_pages.json"), "utf8")
).pages;
const sections: Record<string, { anchor: string; file: string; headings: string[] }[]> = JSON.parse(
  readFileSync(resolve(ROOT, "backend/app/data/site_search_sections.json"), "utf8")
).kinds;

const listed = new Set(pages.map((p) => `${p.label}\u0000${p.href}`));

describe("site search's page list", () => {
  it("has every section hub and tab, with the navigation's label and href", () => {
    for (const s of Object.values(SECTIONS)) {
      expect(listed, s.label).toContain(`${s.label}\u0000${s.href}`);
      for (const t of s.tabs) expect(listed, t.label).toContain(`${t.label}\u0000${t.href}`);
    }
  });

  it("has every About chapter", () => {
    expect(pages.some((p) => p.href === ABOUT_OVERVIEW_HREF)).toBe(true);
    for (const c of ABOUT_CHAPTERS) expect(listed, c.title).toContain(`${c.title}\u0000${c.href}`);
  });

  it("lists only pages the app has", () => {
    // A path resolves through the app directory as Next routes it: a
    // folder of the segment's name, else a dynamic one ([x]), with route
    // groups ((x)) passed through.
    function resolves(dir: string, segments: string[]): boolean {
      const groups = readdirSync(dir).filter((d) => /^\(.+\)$/.test(d));
      if (segments.length === 0) {
        return (
          existsSync(resolve(dir, "page.tsx")) || groups.some((g) => resolves(resolve(dir, g), []))
        );
      }
      const [head, ...rest] = segments;
      const dynamic = readdirSync(dir).filter((d) => /^\[[^.].*\]$/.test(d));
      return [head, ...dynamic, ...groups].some((name) => {
        const next = resolve(dir, name);
        if (!existsSync(next)) return false;
        return groups.includes(name) ? resolves(next, segments) : resolves(next, rest);
      });
    }
    for (const p of pages) {
      const segments = p.href.split(/[?#]/)[0].split("/").filter(Boolean);
      expect(resolves(resolve(ROOT, "frontend/src/app"), segments), p.href).toBe(true);
    }
  });
});

describe("site search's section anchors", () => {
  it("are ids the page renders, under headings the page prints", () => {
    for (const rows of Object.values(sections)) {
      for (const s of rows) {
        const source = readFileSync(resolve(ROOT, s.file), "utf8");
        const anchor = new RegExp(
          `(id|shareId)=["{]?["\`]?${s.anchor}["\`]|SectionHead id="${s.anchor}"|shareSection\\("${s.anchor}"\\)`
        );
        expect(source, `${s.file} #${s.anchor}`).toMatch(anchor);
        for (const h of s.headings) {
          expect(source.toLowerCase(), `${s.file}: ${h}`).toContain(h.toLowerCase());
        }
      }
    }
  });
});
