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

describe("certificationText at a certification deadline", () => {
  it("says the deadline passed with the count unchanged, never that the state certified", () => {
    const text = certificationText(
      { ...base, basis: "deadline", certifiedOn: "2026-11-30" },
      "Colorado"
    );
    expect(text).toBe(
      "Certified winner: Dana Smith (D). Colorado's deadline to certify the count passed on November 30, 2026, with the count unchanged since, and no runoff or recount is pending under state law."
    );
  });
});

describe("certificationText for a seat elected unopposed", () => {
  it("names the law and says there is no count", () => {
    const text = certificationText(
      {
        ...base,
        basis: "unopposed",
        status: "unopposed",
        certifiedOn: null,
        votesCounted: null,
        statute: "Fla. Stat. 101.151(7)",
      },
      "Florida"
    );
    expect(text).toBe(
      "Dana Smith (D) was elected unopposed: Florida law keeps an unopposed candidate off the ballot (Fla. Stat. 101.151(7)), so there is no count."
    );
  });
});
