import { describe, expect, it } from "vitest";
import { reportsRecess } from "./SectionsBand";
import type { SectionFigures } from "@/types/sections";

const recess = {
  line: "No record votes or floor business since Wednesday, September 30",
  lastBusinessDay: "2026-09-30",
  week: "2026-09-28",
};

function figures(c: Partial<SectionFigures["congress"]>): SectionFigures {
  return {
    congress: {
      reports: { date: "2026-10-15", recordVotes: 0 },
      week: { start: "2026-10-12", recordVotes: 0 },
      month: { month: "2026-10", recordVotes: 0 },
      recess,
      ...c,
    },
  } as SectionFigures;
}

describe("reportsRecess", () => {
  it("folds the three reports into the backend's line when none has a vote", () => {
    expect(reportsRecess(figures({}))).toEqual(recess);
  });

  it("keeps the figures when any report has votes to count, or Congress isn't out", () => {
    expect(reportsRecess(figures({ month: { month: "2026-10", recordVotes: 4 } }))).toBeNull();
    expect(reportsRecess(figures({ recess: null }))).toBeNull();
    expect(reportsRecess(null)).toBeNull();
  });
});
