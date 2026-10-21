import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CALENDAR_HREF,
  currentSection,
  recordTrail,
  issuesUrl,
  monitorHref,
  SECTIONS,
  sectionSubpages,
} from "./routes";
import { ELECTION_MAP_HREF } from "./electionMap";
import { REDISTRICTING_HREF } from "./redistricting";
import { presidentSupportHref } from "./presidentialSupport";
import { NOMINATIONS_HREF } from "./nominations";

describe("issuesUrl", () => {
  it("is bare /action for the live view with nothing expanded", () => {
    expect(issuesUrl(null, null)).toBe("/action");
  });

  it("keeps the day when an issue is expanded on it", () => {
    // Writing ?issue= alone reopened on the latest day, where that issue
    // isn't, so a reload or a shared link expanded nothing.
    expect(issuesUrl("2026-09-22", "i3c6ef362")).toBe("/action?date=2026-09-22&issue=i3c6ef362");
  });

  it("carries either value alone", () => {
    expect(issuesUrl("2026-09-22", null)).toBe("/action?date=2026-09-22");
    expect(issuesUrl(null, "abc")).toBe("/action?issue=abc");
  });
});

describe("monitorHref", () => {
  it("names the monitors tab and the monitor", () => {
    expect(monitorHref("government-funding")).toBe(
      "/action?tab=monitors&monitor=government-funding"
    );
  });
});

describe("SECTIONS", () => {
  it("names the same pages as the modules that own them", () => {
    const href = (section: keyof typeof SECTIONS, key: string) =>
      SECTIONS[section].tabs.find((t) => t.key === key)?.href;
    expect(href("elections", "map")).toBe(ELECTION_MAP_HREF);
    expect(href("elections", "redistricting")).toBe(REDISTRICTING_HREF);
    expect(href("elections", "calendar")).toBe(CALENDAR_HREF);
    expect(href("congress", "president")).toBe(presidentSupportHref("house"));
    expect(href("congress", "nominations")).toBe(NOMINATIONS_HREF);
  });
});

// Pages that are no section's: the footer's own pages, the admin, and
// /congress/president (which sends to the House's page, a tab).
const NOT_IN_A_SECTION = new Set([
  "/",
  "/accessibility",
  "/admin",
  "/changelog",
  "/congress/president",
  "/developers",
  "/environmental",
  "/feedback",
  "/feeds",
  // Reached from the header's SEARCH on every page, not from a section.
  "/search",
  // A federal document's page: found by search (its Documents filter) and
  // from every page that cites it; documents span all three branches.
  "/explore/[id]",
]);

/** Every page route under app/: route groups dropped. */
function pageRoutes(dir: string, root: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return pageRoutes(path, root);
    if (entry !== "page.tsx") return [];
    const segments = relative(root, dir)
      .split(sep)
      .filter((s) => s && !/^\(.*\)$/.test(s));
    return [`/${segments.join("/")}`];
  });
}

describe("SECTIONS covers the site", () => {
  const sectionPaths = new Set(
    Object.values(SECTIONS).flatMap((s) =>
      [s.href, ...s.tabs.map((t) => t.href)].map((h) => h.split(/[?#]/)[0])
    )
  );

  it("lists every page that isn't a record, so no page is reachable only by an inline link", () => {
    const app = join(import.meta.dirname, "..", "app");
    // A route with a [segment] is a record (a member, a bill, a day), found
    // from its section's pages.
    const missing = pageRoutes(app, app).filter(
      (r) => !r.includes("[") && !sectionPaths.has(r) && !NOT_IN_A_SECTION.has(r)
    );
    expect(missing).toEqual([]);
  });

  it("puts every record route inside a section", () => {
    const app = join(import.meta.dirname, "..", "app");
    const orphans = pageRoutes(app, app)
      .filter((r) => r.includes("["))
      .filter((r) => !NOT_IN_A_SECTION.has(r))
      .filter((r) => currentSection(r) === null);
    expect(orphans).toEqual([]);
  });

  it("menus leave out the hub, which their section's own link already is", () => {
    expect(sectionSubpages("elections").map((t) => t.key)).toEqual([
      "map",
      "redistricting",
      "calendar",
      "archive",
    ]);
  });

  it("counts a page as in its section by path, not by prefix text", () => {
    expect(currentSection("/compare/presidents")).toBe("executive");
    expect(currentSection("/compare")).toBe("congress");
    expect(currentSection("/agencies/epa")).toBe("executive");
    expect(currentSection("/congressional")).toBe(null);
    expect(currentSection("/explore")).toBe(null);
  });

  it("puts every page in exactly one section: no prefix is claimed twice", () => {
    // currentSection takes the longest match, so two sections can share a
    // page only by naming the same prefix.
    const prefixes = Object.values(SECTIONS).flatMap((s) => [...s.match]);
    expect(prefixes.length).toBe(new Set(prefixes).size);
    const app = join(import.meta.dirname, "..", "app");
    for (const r of pageRoutes(app, app)) {
      if (NOT_IN_A_SECTION.has(r)) continue;
      expect(currentSection(r), r).not.toBeNull();
    }
  });

  it("takes the longest matching prefix", () => {
    // A stand-in table is not needed: every nested prefix in SECTIONS
    // resolves to the section naming the deeper one.
    for (const [key, s] of Object.entries(SECTIONS)) {
      for (const p of s.match) expect(currentSection(`${p}/x`), p).toBe(key);
    }
  });
});

describe("recordTrail", () => {
  it("files a member under Congress and a justice under the Court", () => {
    expect(recordTrail("senate").section).toBe("congress");
    expect(recordTrail("house").crumbs.map((c) => c.label)).toEqual(["Congress", "Members"]);
    expect(recordTrail("scotus").section).toBe("court");
    expect(recordTrail("president").crumbs.map((c) => c.label)).toEqual([
      "Executive",
      "Every president",
    ]);
  });

  it("leads every crumb to a page in the record's own section", () => {
    for (const branch of ["senate", "house", "president", "scotus"] as const) {
      const { section, crumbs } = recordTrail(branch);
      for (const c of crumbs) expect(currentSection(c.href.split("?")[0]), c.href).toBe(section);
    }
  });
});
