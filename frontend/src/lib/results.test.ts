import { describe, expect, it } from "vitest";
import {
  AWAITING_FILL,
  feedFailed,
  FEED_FAILED_FILL,
  isTied,
  TIED_FILL,
  UNCOVERED_FILL,
  heldByPhrase,
  describeUpdate,
  formatEasternTime,
  raceLabel,
  reportingText,
  resultFill,
  showsResults,
  stateFill,
  summarizeState,
} from "./results";
import type { LiveRaceResult, ResultEvent } from "@/types/election";

function race(overrides: Partial<LiveRaceResult> = {}): LiveRaceResult {
  return {
    raceId: "2026-SEN-GA",
    state: "GA",
    office: "S",
    district: null,
    isSpecial: false,
    heldBy: "DEM",
    official: false,
    votesCounted: 1900,
    reportingUnits: 80,
    totalUnits: 100,
    unitLabel: "precincts",
    sourceName: "GA SOS",
    sourceUrl: null,
    fetchedAt: "2026-11-04T02:44:00Z",
    lastChangeAt: "2026-11-04T02:42:00Z",
    leaderParty: "REP",
    flip: true,
    candidates: [
      { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
    ],
    ...overrides,
  };
}

function event(kind: string, detail: ResultEvent["detail"] = {}): ResultEvent {
  return {
    id: 1,
    raceId: "2026-SEN-GA",
    state: "GA",
    office: "S",
    district: null,
    isSpecial: false,
    kind,
    at: "2026-11-04T02:42:00Z",
    detail: {
      leader: { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6 },
      runnerUp: { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4 },
      reportingUnits: 2103,
      totalUnits: 2653,
      unitLabel: "precincts",
      heldBy: "DEM",
      ...detail,
    },
  };
}

describe("showsResults", () => {
  it("is the campaign page unless the phase says otherwise", () => {
    expect(showsResults(undefined)).toBe(false);
    expect(
      showsResults({
        phase: "campaign",
        electionDate: "",
        resultsUntil: null,
        lastResultChange: null,
      })
    ).toBe(false);
    expect(
      showsResults({
        phase: "election_day",
        electionDate: "",
        resultsUntil: null,
        lastResultChange: null,
      })
    ).toBe(true);
  });
});

describe("resultFill", () => {
  it("says which absence it is", () => {
    expect(resultFill(undefined, false)).toBe(UNCOVERED_FILL);
    expect(resultFill(undefined, true)).toBe(AWAITING_FILL);
    expect(resultFill(race({ leaderParty: null, votesCounted: 0 }), true)).toBe(AWAITING_FILL);
  });

  it("draws a tie as a tie, not as nothing counted", () => {
    const tie = race({
      leaderParty: null,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
      ],
    });
    expect(resultFill(tie, true)).toBe(TIED_FILL);
    expect(isTied(tie)).toBe(true);
    expect(isTied(race())).toBe(false);
    expect(isTied(race({ votesCounted: 0 }))).toBe(false);
  });

  it("draws a leader the feed gives no known party as a lead, not a tie", () => {
    const unknown = race({
      leaderParty: null,
      candidates: [
        { name: "Pat Doe", party: null, votes: 1000, pct: 52.6, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
      ],
    });
    expect(resultFill(unknown, true)).toMatch(/^rgba\(201,149,255/);
  });

  it("is paler with under half in and solid only when official", () => {
    expect(resultFill(race({ reportingUnits: 10 }), true)).toBe("rgba(255,137,137, 0.30)");
    expect(resultFill(race({ official: true }), true)).toBe("rgba(255,137,137, 1)");
    expect(resultFill(race({ leaderParty: "DEM", reportingUnits: 100 }), true)).toBe(
      "rgba(130,172,255, 0.90)"
    );
  });
});

describe("stateFill", () => {
  it("is uncovered where a state has no Senate race", () => {
    expect(stateFill([], "S", true, false)).toBe(UNCOVERED_FILL);
  });

  it("shades a House delegation led only by a third party in that party's colour", () => {
    expect(
      stateFill([race({ office: "H", district: 1, leaderParty: "IND" })], "H", true, true)
    ).toBe("rgba(201,149,255, 0.6)");
  });

  it("draws a covered state whose feed failed as that, not as no votes yet", () => {
    expect(stateFill([], "S", true, true, true)).toBe(FEED_FAILED_FILL);
    expect(stateFill([], "H", true, true, true)).toBe(FEED_FAILED_FILL);
    expect(stateFill([], "S", true, true, false)).toBe(AWAITING_FILL);
    // A state with no feed at all is uncovered whatever its status says.
    expect(stateFill([], "S", false, true, true)).toBe(UNCOVERED_FILL);
    // An older count still shown keeps its colour.
    expect(stateFill([race()], "S", true, true, true)).toMatch(/^rgba\(255,137,137/);
  });

  it("shades House by the party leading more districts", () => {
    const house = [
      race({ office: "H", district: 1, leaderParty: "DEM" }),
      race({ office: "H", district: 2, leaderParty: "REP" }),
      race({ office: "H", district: 3, leaderParty: "REP" }),
    ];
    expect(stateFill(house, "H", true, true)).toMatch(/^rgba\(255,137,137/);
  });
});

describe("labels", () => {
  it("names races the way the page does", () => {
    expect(raceLabel({ state: "GA", office: "S", district: null })).toBe("GA Senate");
    expect(raceLabel({ state: "FL", office: "S", district: null, isSpecial: true })).toBe(
      "FL Senate (special)"
    );
    expect(raceLabel({ state: "GA", office: "H", district: 2 })).toBe("GA-2");
    expect(raceLabel({ state: "AK", office: "H", district: 0 })).toBe("AK at-large");
  });

  it("reports units in the state's own terms", () => {
    expect(reportingText({ reportingUnits: 40, totalUnits: 64, unitLabel: "counties" })).toBe(
      "40 of 64 counties reporting (63%)"
    );
    expect(reportingText({ reportingUnits: null, totalUnits: null })).toBe("");
  });

  it("tells the time in Eastern, whatever the reader's zone", () => {
    // vitest pins TZ=America/Los_Angeles, which would read 6:42 PM.
    // With its date: the results stay up for weeks.
    expect(formatEasternTime("2026-11-04T02:42:00Z")).toBe("Nov 3, 9:42 PM ET");
  });
});

describe("describeUpdate", () => {
  it("words a flip as a lead until the state calls it", () => {
    expect(describeUpdate(event("flip"))).toEqual({
      tag: "FLIP",
      tone: "flip",
      text:
        "Ray Jones (R) leads in a seat Democrats hold. Ray Jones (R) 52.6%, Dana Smith (D) 47.4%. " +
        "2,103 of 2,653 precincts reporting (79%). Not final.",
    });
    expect(describeUpdate(event("flip", { official: true })).text).toMatch(
      /^Ray Jones \(R\) wins in the official count, taking a seat Democrats held\./
    );
  });

  it("never leaves a lead change dangling without a previous leader", () => {
    expect(describeUpdate(event("lead_change")).text).toMatch(/^Ray Jones \(R\) moves ahead\. /);
  });

  it("words a reversal to a tie without naming a leader", () => {
    expect(
      describeUpdate(event("flip_reversed", { leader: null, votesCounted: 1800 })).text
    ).toMatch(/^The count is now tied, so the seat no longer shows a change of party\./);
    expect(describeUpdate(event("flip_reversed")).text).toMatch(
      /^The count no longer shows the seat changing party\./
    );
  });

  it("says who held a seat in words that fit any party", () => {
    expect(heldByPhrase("IND")).toBe("an independent");
    expect(heldByPhrase("LIB")).toBe("a Libertarian");
    expect(heldByPhrase(null)).toBe("another party");
  });

  it("words the other events from their own figures", () => {
    expect(
      describeUpdate(event("lead_change", { previousLeader: { name: "Dana Smith", party: "DEM" } }))
        .text
    ).toMatch(/^Ray Jones \(R\) moves ahead of Dana Smith \(D\)\./);
    expect(describeUpdate(event("all_reporting")).text).toMatch(
      /Counting can continue after every unit reports\.$/
    );
    expect(describeUpdate(event("official")).tag).toBe("OFFICIAL");
    expect(
      describeUpdate(
        event("flip_reversed", {
          leader: { name: "Dana Smith", party: "DEM", votes: 1000, pct: 50.1 },
        })
      ).text
    ).toMatch(/^Dana Smith \(D\) is ahead again, so the seat no longer shows a change of party\./);
    expect(describeUpdate(event("first_returns")).text).toMatch(/^First returns\./);
  });
});

describe("summarizeState", () => {
  it("counts House leads and flips", () => {
    const s = summarizeState([
      race(),
      race({ office: "H", district: 1, leaderParty: "DEM", flip: false }),
      race({ office: "H", district: 2, leaderParty: "DEM", flip: false }),
    ]);
    expect(s.houseLeads).toEqual({ DEM: 2 });
    expect(s.flips).toBe(1);
  });
});

describe("feedFailed", () => {
  it("is a read that gave no usable count, not one that hasn't happened", () => {
    expect(feedFailed(undefined)).toBe(false);
    expect(feedFailed({ status: "ok" })).toBe(false);
    expect(feedFailed({ status: "polls_open" })).toBe(false);
    for (const status of ["untrusted", "unavailable", "stale", "failed"])
      expect(feedFailed({ status })).toBe(true);
  });
});
