import { describe, expect, it } from "vitest";
import { apaDate, citation } from "./CiteThis";

describe("citation", () => {
  it("is APA's dataset reference, built from the record and the reader's date", () => {
    expect(
      citation("A record", "2026-10-13", "/politicians/jordan-hale", new Date(2026, 9, 14))
    ).toBe(
      "Civitas Research. (2026, October 13). A record [Data set]. Retrieved October 14, 2026, from https://civitas-research.org/politicians/jordan-hale"
    );
  });

  it("says n.d. for a record without a date of its own", () => {
    expect(citation("A bill", null, "/congress/bills/HR.1", new Date(2026, 0, 2))).toMatch(
      /^Civitas Research\. \(n\.d\.\)\. A bill \[Data set\]\. Retrieved January 2, 2026, from /
    );
  });

  it("reads the date as written, whatever the reader's time zone", () => {
    expect(apaDate("2026-01-01")).toBe("2026, January 1");
    expect(apaDate("not a date")).toBeNull();
  });
});

describe("citation of a government record", () => {
  const today = new Date(2026, 9, 16);

  it("credits the issuing body and the official copy, Civitas only as where it was accessed", () => {
    expect(
      citation("Guidance on Eligible Investments", "2026-08-21", "/explore/fr-2026-17123", today, {
        author: "Internal Revenue Service",
        container: "Federal Register, 91, 54280 (FR Doc. 2026-17123)",
        url: "https://www.federalregister.gov/d/2026-17123",
      })
    ).toBe(
      "Internal Revenue Service. (2026, August 21). Guidance on Eligible Investments. Federal Register, 91, 54280 (FR Doc. 2026-17123). https://www.federalregister.gov/d/2026-17123 (accessed via Civitas Research, October 16, 2026, at https://civitas-research.org/explore/fr-2026-17123)"
    );
  });

  it("never doubles a full stop a title already ends with", () => {
    const text = citation(
      "Roll call vote 372, 119th Congress, 1st session: H. Con. Res. 14.",
      "2025-07-01",
      "/congress/votes/senate/119/1/372",
      today,
      { author: "United States Senate", url: "https://www.senate.gov/x.htm" }
    );
    expect(text).not.toContain("..");
    expect(text.startsWith("United States Senate. (2025, July 1). Roll call vote 372")).toBe(true);
    expect(citation("H. Con. Res. 14.", null, "/x", today)).toContain(
      "H. Con. Res. 14 [Data set]."
    );
  });
});
