import { describe, expect, it } from "vitest";
import { formerOfficeBadge, formerOfficeNotice } from "./officeStatus";

describe("formerOfficeNotice", () => {
  it("calls a departed senator's seat vacant, with reason and date", () => {
    const notice = formerOfficeNotice({
      branch: "senate",
      name: "Jane Doe",
      vacancyReason: "resigned",
      leftOfficeDate: "2026-03-01",
    });
    expect(notice.label).toBe("Seat Vacant");
    expect(notice.detail).toContain("Jane Doe is no longer serving (resigned) as of 2026-03-01");
  });

  it("omits the parenthetical when no reason is recorded", () => {
    const notice = formerOfficeNotice({ branch: "house", name: "Jane Doe" });
    expect(notice.detail).toContain("Jane Doe is no longer serving.");
  });

  it("never says a seat is vacant for a former president", () => {
    const notice = formerOfficeNotice({
      branch: "president",
      name: "Barack Obama",
      number: 44,
      termStart: "2009-01-20",
      termEnd: "2017-01-20",
    });
    expect(notice.label).toBe("Former President");
    expect(notice.detail).toBe(
      "Barack Obama served as the 44th President from 2009 to 2017 and is no longer in office. " +
      "The scores and data below reflect their record in office.",
    );
    expect(notice.detail.toLowerCase()).not.toContain("vacant");
  });

  it("uses the right ordinal for teens", () => {
    const notice = formerOfficeNotice({ branch: "president", name: "Abraham Lincoln", number: 16 });
    expect(notice.detail).toContain("the 16th President");
  });

  it("falls back gracefully when a president's term end is unknown", () => {
    const notice = formerOfficeNotice({
      branch: "president",
      name: "Jane Doe",
      number: 3,
      termStart: "2001-01-20",
      termEnd: null,
    });
    expect(notice.detail).toContain("from 2001 to the end of their term");
  });

  it("describes a retired justice as former, not vacant", () => {
    const notice = formerOfficeNotice({ branch: "scotus", name: "Stephen Breyer" });
    expect(notice.label).toBe("Former Justice");
    expect(notice.detail).toContain("no longer sits on the Supreme Court");
  });
});

describe("formerOfficeBadge", () => {
  it("appends the vacancy reason for congressional seats", () => {
    expect(formerOfficeBadge({ branch: "senate", name: "Jane Doe", vacancyReason: "died" }))
      .toBe("SEAT VACANT — DIED");
  });

  it("does not append a reason for presidents", () => {
    expect(formerOfficeBadge({ branch: "president", name: "Barack Obama", number: 44 }))
      .toBe("FORMER PRESIDENT");
  });
});
