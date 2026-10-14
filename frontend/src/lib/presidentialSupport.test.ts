import { describe, expect, it } from "vitest";
import {
  chamberSentence,
  expectationSentence,
  furthest,
  leanLabel,
  matchesMember,
  points,
  recordSentence,
  seatLabel,
} from "./presidentialSupport";
import type {
  ChamberSupport,
  ChamberSupportMember,
  SupportNumbers,
} from "@/types/presidentialSupport";

const numbers = (over: Partial<SupportNumbers> = {}): SupportNumbers => ({
  withPresident: 30,
  votes: 100,
  rate: 0.3,
  rateLow: 0.22,
  rateHigh: 0.4,
  expected: 0.1,
  difference: 0.2,
  differenceLow: 0.12,
  differenceHigh: 0.3,
  comparedWithExpected: "more",
  ...over,
});

const member = (id: string, over: Partial<ChamberSupportMember> = {}): ChamberSupportMember => ({
  id,
  name: `Member ${id}`,
  party: "D",
  state: "OH",
  district: 3,
  seatLean: -5,
  ...numbers(),
  ...over,
});

describe("presidential support wording", () => {
  it("names the member and uses no pronoun", () => {
    const text = `${recordSentence("Jane Doe", numbers())} ${expectationSentence(numbers())}`;
    expect(text).toContain("Jane Doe voted with the president's stated position on 30 of 100");
    expect(text).not.toMatch(/\b(he|his|him|she|her|hers|they|their|them)\b/i);
  });

  it("says within, more or less without judging", () => {
    expect(
      expectationSentence(numbers({ comparedWithExpected: "within", difference: 0.01 }))
    ).toContain("about as expected");
    expect(expectationSentence(numbers())).toContain("more often than expected");
    expect(
      expectationSentence(numbers({ comparedWithExpected: "less", difference: -0.2 }))
    ).toContain("less often than expected");
    const all = ["more", "less", "within"].map((c) =>
      expectationSentence(numbers({ comparedWithExpected: c as "more" }))
    );
    for (const t of all) expect(t).not.toMatch(/\b(good|bad|loyal|rebel|disloyal|better|worse)\b/i);
    expect(expectationSentence(numbers({ expected: null, comparedWithExpected: null }))).toContain(
      "too few members"
    );
  });

  it("formats gaps, leans and seats", () => {
    expect(points(0.043)).toBe("+4 points");
    expect(points(-0.01)).toBe("-1 point");
    expect(leanLabel(5)).toBe("R+5");
    expect(leanLabel(-12)).toBe("D+12");
    expect(leanLabel(0)).toBe("EVEN");
    expect(seatLabel({ state: "WY", district: 0 })).toBe("WY-AL");
    expect(seatLabel({ state: "GA", district: null })).toBe("GA");
  });

  it("finds members by name, state, state name or seat", () => {
    const m = member("a", { name: "Jane Doe", state: "TX", district: 7 });
    expect(matchesMember(m, "doe")).toBe(true);
    expect(matchesMember(m, "tx")).toBe(true);
    expect(matchesMember(m, "Texas", "Texas")).toBe(true);
    expect(matchesMember(m, "TX-7")).toBe(true);
    expect(matchesMember(m, "TX-17")).toBe(false);
    expect(matchesMember(m, "  ")).toBe(false);
  });

  it("lists the furthest only among members outside their range", () => {
    const view: ChamberSupport = {
      chamber: "house",
      congress: 119,
      rollCalls: [],
      method: {} as ChamberSupport["method"],
      members: [
        member("a", { difference: 0.3 }),
        member("b", { difference: 0.1 }),
        member("c", { difference: 0.5, comparedWithExpected: "within" }),
        member("d", { difference: -0.2, comparedWithExpected: "less" }),
      ],
    };
    expect(furthest(view, "more").map((m) => m.id)).toEqual(["a", "b"]);
    expect(furthest(view, "less").map((m) => m.id)).toEqual(["d"]);
    expect(chamberSentence(view)).toContain("Of 4 House members, 2 voted");
  });
});
