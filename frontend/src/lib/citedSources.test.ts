import { describe, expect, it } from "vitest";
import { congressGovCitedSource, exploreCitedSource, rollCallCitedSource } from "./citedSources";

const base = { source: "", chamber: "", agencyName: "", politicianName: "", docType: "" };

describe("exploreCitedSource", () => {
  it("cites a rule to its agency, in the Federal Register, by citation and document number", () => {
    expect(
      exploreCitedSource(
        {
          ...base,
          docType: "Proposed Rule",
          source: "Federal Register",
          chamber: "Regulatory",
          agencyName: "Internal Revenue Service",
          frCitation: "91 FR 54280",
          frDocNumber: "2026-17123",
        },
        "https://www.federalregister.gov/documents/2026/08/21/2026-17123/x"
      )
    ).toEqual({
      author: "Internal Revenue Service",
      container: "Federal Register, 91, 54280 (FR Doc. 2026-17123)",
      url: "https://www.federalregister.gov/documents/2026/08/21/2026-17123/x",
    });
  });

  it("cites a presidential document to the Executive Office of the President", () => {
    const s = exploreCitedSource(
      { ...base, docType: "Executive Order", source: "Federal Register", chamber: "Executive" },
      "https://www.federalregister.gov/d/2026-07907"
    );
    expect(s?.author).toBe("Executive Office of the President");
  });

  it("cites a floor speech to its speaker, in the Congressional Record", () => {
    expect(
      exploreCitedSource(
        {
          ...base,
          docType: "Senate Floor Speech",
          source: "Congressional Record (GovInfo)",
          politicianName: "Hale",
          speakerName: "Jordan Hale",
          crCitation: "172 Cong. Rec. S5190",
        },
        "https://www.govinfo.gov/x"
      )
    ).toEqual({
      author: "Jordan Hale",
      container: "Congressional Record, 172, S5190 (daily ed.)",
      url: "https://www.govinfo.gov/x",
    });
  });

  it("cites an opinion to the Court, and nothing without an official copy", () => {
    expect(
      exploreCitedSource({ ...base, docType: "Supreme Court Opinion" }, "https://x.gov/o.pdf")
        ?.author
    ).toBe("Supreme Court of the United States");
    expect(exploreCitedSource({ ...base, docType: "Supreme Court Opinion" }, "")).toBeNull();
  });
});

describe("roll calls, bills and nominations", () => {
  it("cites the chamber's own record", () => {
    expect(rollCallCitedSource("house", "https://clerk.house.gov/Votes/2025002")?.author).toBe(
      "Office of the Clerk, U.S. House of Representatives"
    );
    expect(rollCallCitedSource("senate", null)).toBeNull();
    expect(congressGovCitedSource("U.S. Congress", "https://www.congress.gov/bill/x")).toEqual({
      author: "U.S. Congress",
      container: "Congress.gov",
      url: "https://www.congress.gov/bill/x",
    });
  });
});
