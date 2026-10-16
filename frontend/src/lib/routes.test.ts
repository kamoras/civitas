import { describe, expect, it } from "vitest";
import { issuesUrl, monitorHref, SECTIONS } from "./routes";
import { ELECTION_MAP_HREF } from "./electionMap";
import { REDISTRICTING_HREF } from "./redistricting";
import { presidentSupportHref } from "./presidentialSupport";

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
    expect(href("congress", "president")).toBe(presidentSupportHref("house"));
  });
});
