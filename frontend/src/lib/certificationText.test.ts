import { describe, expect, it } from "vitest";
import { certificationText } from "@/lib/results";
import type { RaceCertification } from "@/types/election";

const base: RaceCertification = {
  basis: "certified",
  status: "certified",
  certifiedOn: "2026-12-04",
  certifiedOnStated: true,
  recountUntil: null,
  statute: "Md. Election Law 12-101",
  sourceName: "Maryland State Board of Elections",
  sourceUrl: null,
  votesCounted: 100000,
  winner: { name: "Dana Smith", party: "DEM", candidateId: null },
  candidates: [],
};

describe("certificationText", () => {
  it("names a winner only when the backend does", () => {
    expect(certificationText(base, "Maryland")).toBe(
      "Certified winner: Dana Smith (D). Maryland certified the count on December 4, 2026, and no runoff or recount is pending under state law."
    );
  });

  it("says when a recount can still be asked for, and names no one", () => {
    const text = certificationText(
      { ...base, status: "recount_possible", recountUntil: "2026-12-07", winner: null },
      "Maryland"
    );
    expect(text).toContain("until December 7, 2026");
    expect(text).not.toContain("Dana Smith");
  });

  it("dates an undated certification by its first read, and says so", () => {
    expect(certificationText({ ...base, certifiedOnStated: false }, "Kentucky")).toContain(
      "Kentucky has certified the count (first read here December 4, 2026)"
    );
  });

  it("words a runoff and the sworn-in floor", () => {
    expect(certificationText({ ...base, status: "runoff", winner: null }, "Georgia")).toContain(
      "goes to a runoff"
    );
    expect(
      certificationText(
        { ...base, basis: "sworn_in", status: "sworn_in", certifiedOn: null },
        "Ohio"
      )
    ).toBe("Dana Smith (D) was sworn in for the seat, per the member roster.");
  });
});
