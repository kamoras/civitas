import { describe, expect, it } from "vitest";
import { apaDate, citation } from "./CiteThis";

describe("citation", () => {
  it("is APA's dataset reference, built from the record and the reader's date", () => {
    expect(
      citation("A record", "2026-10-13", "/politicians/jordan-hale", new Date(2026, 9, 14))
    ).toBe(
      "Civitas. (2026, October 13). A record [Data set]. Retrieved October 14, 2026, from https://civitas-research.org/politicians/jordan-hale"
    );
  });

  it("says n.d. for a record without a date of its own", () => {
    expect(citation("A bill", null, "/congress/bills/HR.1", new Date(2026, 0, 2))).toMatch(
      /^Civitas\. \(n\.d\.\)\. A bill \[Data set\]\. Retrieved January 2, 2026, from /
    );
  });

  it("reads the date as written, whatever the reader's time zone", () => {
    expect(apaDate("2026-01-01")).toBe("2026, January 1");
    expect(apaDate("not a date")).toBeNull();
  });
});
