import { describe, expect, it } from "vitest";
import { sectionPreview } from "./sectionPreviews";
import { SECTIONS } from "./routes";
import type { SectionFigures } from "@/types/sections";

const MAP_OFF = "The election map is shown during the campaign only.";

const FIGURES: SectionFigures = {
  action: {
    today: { issues: 3, date: "2026-10-15" },
    ongoing: { monitors: 0 },
    archive: { days: 40, since: "2026-07-01" },
  },
  congress: {
    reports: { date: "2026-10-15", recordVotes: 0 },
    week: { start: "2026-10-12", recordVotes: 12 },
    month: { month: "2026-10", recordVotes: 30 },
    committees: { count: 40, metThisWeek: 7 },
    bills: { inMotion: 512 },
    president: { house: { rate: 0.5612, rollCalls: 21 }, senate: null },
  },
  agencies: {
    directory: { count: 0 },
    budget: null,
    comments: { open: 185 },
  },
  politicians: { directory: { senators: 100, representatives: 433 } },
  elections: {
    ballots: { states: 51, date: "2026-11-03", phase: "campaign", daysUntil: 18 },
    map: { available: false, reason: MAP_OFF },
    redistricting: { states: 9, cycle: "2026", upcoming: true },
  },
  explore: { search: { documents: 12000 } },
};

describe("sectionPreview", () => {
  it("words the backend's figures", () => {
    expect(sectionPreview(FIGURES, "congress", "president")).toEqual({
      figure: "56%",
      caption:
        "of House members' votes went the president's way, on 21 roll calls with a stated position",
    });
    expect(sectionPreview(FIGURES, "elections", "redistricting")?.caption).toBe(
      "states vote on new House lines in 2026"
    );
  });

  it("never shows a bare zero for something not read or not happening", () => {
    expect(sectionPreview(FIGURES, "agencies", "directory")?.figure).toBe("Not yet");
    expect(sectionPreview(FIGURES, "agencies", "budget")?.figure).toBe("Not yet");
    expect(sectionPreview(FIGURES, "action", "ongoing")?.figure).toBe("None");
    expect(sectionPreview(FIGURES, "congress", "reports")?.caption).toMatch(/^no record votes/);
    // The map's own reason, out of its phase.
    expect(sectionPreview(FIGURES, "elections", "map")?.caption).toBe(MAP_OFF);
  });

  it("is null without figures or for a page that has none", () => {
    expect(sectionPreview(null, "congress", "bills")).toBeNull();
    expect(sectionPreview(FIGURES, "politicians", "leaderboard")).toBeNull();
  });

  it("names only real section pages", () => {
    // A preview keyed to a tab SECTIONS doesn't have would never show.
    for (const [section, tabs] of Object.entries({
      action: ["today", "ongoing", "archive"],
      congress: ["reports", "week", "month", "bills", "committees", "president"],
      agencies: ["directory", "budget", "comments"],
      politicians: ["directory"],
      elections: ["ballots", "map", "redistricting"],
      explore: ["search"],
    })) {
      const keys = SECTIONS[section as keyof typeof SECTIONS].tabs.map((t) => t.key as string);
      for (const t of tabs) {
        expect(keys).toContain(t);
        expect(sectionPreview(FIGURES, section, t)).not.toBeNull();
      }
    }
  });
});
