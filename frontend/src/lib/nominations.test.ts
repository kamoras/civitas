import { describe, expect, it } from "vitest";
import { CITATION, nominationHref, voteNominationLine, voteSubjectHref } from "./nominations";
import type { VoteRollCall } from "@/types/senator";

const rc = (over: Partial<VoteRollCall> = {}): VoteRollCall => ({
  chamber: "senate",
  congress: 119,
  session: 2,
  number: 256,
  date: "2026-09-30",
  question: "On the Nomination PN1129",
  title: "Keith Sonderling, of Florida, to be Secretary of Labor",
  result: "Nomination Confirmed",
  billId: null,
  billLabel: null,
  sourceUrl: "",
  parties: [],
  ...over,
});
const nom = (citation: string) => ({
  citation,
  congress: 119,
  positionTitle: "Secretary of Labor",
  organization: "Department of Labor",
  status: "confirmed" as const,
  nominees: ["Keith Sonderling (FL)"],
  nomineeCount: 1,
});

describe("a member's vote row", () => {
  it("opens one nomination's record", () => {
    const r = rc({ nominations: [nom("PN1129")] });
    expect(voteSubjectHref(r)).toBe("/congress/nominations/119/PN1129");
    expect(voteNominationLine(r)).toBe("Nomination PN1129 · Department of Labor · Confirmed");
  });
  it("opens an en bloc vote's roll call, which lists each", () => {
    const r = rc({ nominations: [nom("PN1"), nom("PN2")] });
    expect(voteSubjectHref(r)).toBe("/congress/votes/senate/119/2/256");
    expect(voteNominationLine(r)).toBe("2 nominations, en bloc");
  });
  it("leaves a bill vote, and an older backend's row, as they were", () => {
    expect(voteSubjectHref(rc({ billId: "S.5", question: "On Passage" }))).toBe(
      "/congress/bills/S.5?congress=119"
    );
    expect(voteSubjectHref(rc())).toBeNull();
    expect(voteNominationLine(rc())).toBeNull();
  });
});

describe("addresses", () => {
  it("names the Congress and the citation", () => {
    expect(nominationHref({ congress: 119, citation: "PN999-1" })).toBe(
      "/congress/nominations/119/PN999-1"
    );
    expect(CITATION.test("PN999-1")).toBe(true);
    expect(CITATION.test("PN0")).toBe(false);
    expect(CITATION.test("pn12")).toBe(false);
  });
});
