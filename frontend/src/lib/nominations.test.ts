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

const nv = (
  kind: "confirm" | "cloture",
  citationCount: number,
  stored: string[]
): VoteRollCall["nominationVote"] => ({
  kind,
  label: kind === "confirm" ? "Vote to confirm" : "Vote to end debate (cloture)",
  citationCount,
  nominations: stored.map(nom),
});

describe("a member's vote row", () => {
  it("opens one nomination's record, its status only on the vote to confirm", () => {
    const r = rc({ nominationVote: nv("confirm", 1, ["PN1129"]) });
    expect(voteSubjectHref(r)).toBe("/congress/nominations/119/PN1129");
    expect(voteNominationLine(r)).toBe(
      "Vote to confirm · Nomination PN1129 · Department of Labor · Confirmed"
    );
  });
  it("never calls a cloture vote confirmed", () => {
    const r = rc({
      question: "On the Cloture Motion PN1129",
      nominationVote: nv("cloture", 1, ["PN1129"]),
    });
    expect(voteNominationLine(r)).toBe(
      "Vote to end debate (cloture) · Nomination PN1129 · Department of Labor"
    );
  });
  it("counts an en bloc vote by its question, however many are stored", () => {
    const r = rc({ nominationVote: nv("confirm", 70, ["PN1"]) });
    expect(voteSubjectHref(r)).toBe("/congress/votes/senate/119/2/256");
    expect(voteNominationLine(r)).toBe("Vote to confirm · 70 nominations, en bloc");
  });
  it("names the question alone for a nomination not read yet", () => {
    const r = rc({ nominationVote: nv("cloture", 1, []) });
    expect(voteSubjectHref(r)).toBeNull();
    expect(voteNominationLine(r)).toBe("Vote to end debate (cloture)");
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
