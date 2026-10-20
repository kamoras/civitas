import { describe, expect, it } from "vitest";
import { leadOrder, sectionPreview, seasonLead } from "./sectionPreviews";
import { SECTIONS } from "./routes";
import type { SectionFigures } from "@/types/sections";

const MAP_OFF = "The election map is shown during the campaign only.";

const FIGURES: SectionFigures = {
  congress: {
    week: { start: "2026-10-12", recordVotes: 12 },
    month: { month: "2026-10", recordVotes: 30 },
    recess: null,
    committees: { count: 40, metThisWeek: 7 },
    bills: { inMotion: 512 },
    president: { house: { rate: 0.5612, rollCalls: 21 }, senate: null },
  },
  agencies: {
    budget: null,
    comments: { open: 185 },
  },
  elections: {
    ballots: { date: "2026-11-03" },
    map: { available: false, reason: MAP_OFF },
    redistricting: { states: 9, cycle: "2026", upcoming: true },
    archive: null,
  },
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
    expect(sectionPreview(FIGURES, "executive", "budget")?.figure).toBe("Not yet");
    expect(
      sectionPreview(
        {
          ...FIGURES,
          congress: { ...FIGURES.congress, week: { start: "2026-10-12", recordVotes: 0 } },
        },
        "congress",
        "week"
      )?.caption
    ).toMatch(/^no record votes/);
    // The map's own reason, out of its phase.
    expect(sectionPreview(FIGURES, "elections", "map")?.caption).toBe(MAP_OFF);
  });

  it("gives the last day of business, not None, in a recess", () => {
    const recess = { lastBusinessDay: "2026-09-30" };
    const f = {
      ...FIGURES,
      congress: { ...FIGURES.congress, week: { start: "2026-10-12", recordVotes: 0 }, recess },
    };
    expect(sectionPreview(f, "congress", "week")).toEqual({
      figure: "Sep 30",
      caption: "the last day either chamber voted, passed or confirmed anything",
    });
    expect(sectionPreview(f, "congress", "month")?.figure).toBe("30");
  });

  it("words the archive's map check in every state, never a bare zero", () => {
    const at = (archive: SectionFigures["elections"]["archive"]) =>
      sectionPreview(
        { ...FIGURES, elections: { ...FIGURES.elections, archive } },
        "elections",
        "archive"
      );
    expect(at(null)).toEqual({
      figure: "Not yet",
      caption:
        "the 2026 election is kept here from its day, its map's ranges checked against the count",
    });
    const summary = {
      ranged: 40,
      certified: 0,
      official: 0,
      graded: 0,
      held80: 0,
      held95: 0,
      swingSeats: 0,
    };
    expect(at({ cycle: 2026, stillCounting: true, accountability: summary })?.figure).toBe(
      "Counting"
    );
    expect(at({ cycle: 2026, stillCounting: false, accountability: null })?.caption).toMatch(
      /no pre-election map/
    );
    expect(
      at({
        cycle: 2026,
        stillCounting: true,
        accountability: {
          ...summary,
          certified: 31,
          official: 30,
          graded: 30,
          held80: 25,
          held95: 29,
        },
      })
    ).toEqual({
      figure: "25",
      caption:
        "of 30 official 2026 margins inside the map's 80% ranges · 29 inside the 95% · swing map · still counting",
    });
    // An older backend's answer without the field: no figure, not a crash.
    expect(at(undefined)).toBeNull();
  });

  it("is null without figures or for a page that has none", () => {
    expect(sectionPreview(null, "congress", "bills")).toBeNull();
    expect(sectionPreview(FIGURES, "executive", "president")).toBeNull();
    // The hub's own page has no card.
    expect(sectionPreview(FIGURES, "congress", "reports")).toBeNull();
  });

  it("names only real section pages", () => {
    // A preview keyed to a tab SECTIONS doesn't have would never show.
    for (const [section, tabs] of Object.entries({
      congress: ["week", "month", "bills", "committees", "president"],
      executive: ["budget", "comments"],
      elections: ["map", "redistricting", "archive"],
    })) {
      const keys = SECTIONS[section as keyof typeof SECTIONS].tabs.map((t) => t.key as string);
      for (const t of tabs) {
        expect(keys).toContain(t);
        expect(sectionPreview(FIGURES, section, t)).not.toBeNull();
      }
    }
  });
});

describe("leadOrder", () => {
  const tabs = SECTIONS.elections.tabs.map((t) => ({ key: t.key as string }));
  it("orders by the season's lead and drops nothing", () => {
    expect(leadOrder(tabs, ["calendar", "archive"]).map((t) => t.key)).toEqual([
      "calendar",
      "archive",
      "ballots",
      "map",
      "redistricting",
    ]);
    expect(leadOrder(tabs, undefined)).toEqual(tabs);
  });
  it("reads the season's lead for the elections section only", () => {
    expect(seasonLead(FIGURES, "congress")).toBeUndefined();
    expect(seasonLead(null, "elections")).toBeUndefined();
  });
});
