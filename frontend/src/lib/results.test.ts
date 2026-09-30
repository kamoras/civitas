import { describe, expect, it } from "vitest";
import {
  AWAITING_FILL,
  AWAITING_MARK,
  FEED_FAILED_MARK,
  POLLS_OPEN_MARK,
  feedFailed,
  FEED_FAILED_FILL,
  isTied,
  TIED_FILL,
  UNCOVERED_FILL,
  heldByPhrase,
  describeUpdate,
  formatLed,
  POLLS_OPEN_FILL,
  pollsClosed,
  pollsStillOpen,
  seatsLed,
  formatEasternTime,
  raceLabel,
  reportingText,
  raceStatusText,
  resultFill,
  showsResults,
  stateFill,
  stateShade,
  summarizeState,
} from "./results";
import type { ElectionPhaseInfo, LiveRaceResult, ResultEvent } from "@/types/election";

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

  it("is results only for the phases the backend names — never for a malformed one", () => {
    expect(showsResults({} as ElectionPhaseInfo)).toBe(false);
    expect(showsResults({ phase: "Results" } as unknown as ElectionPhaseInfo)).toBe(false);
    expect(
      showsResults({
        phase: "results",
        electionDate: "",
        resultsUntil: null,
        lastResultChange: null,
      })
    ).toBe(true);
  });
});

describe("polls open or closed", () => {
  const day = {
    phase: "election_day" as const,
    electionDate: "2026-11-03",
    resultsUntil: null,
    lastResultChange: null,
  };
  const t = Date.parse("2026-11-04T00:00:00Z");
  const info = (over: Partial<Parameters<typeof pollsClosed>[0]> = {}) => ({
    phase: day,
    races: [],
    pollsClose: { GA: "2026-11-04T00:30:00Z" },
    feeds: {},
    ...over,
  });

  it("goes by the closing time", () => {
    expect(pollsStillOpen(info(), "GA", t)).toBe(true);
    expect(pollsClosed(info(), "GA", t)).toBe(false);
    expect(pollsStillOpen(info(), "GA", t + 3_600_000)).toBe(false);
    expect(pollsClosed(info(), "GA", t + 3_600_000)).toBe(true);
    // The closing time wins over a feed read minutes before it passed.
    const read = { GA: { status: "polls_open", checkedAt: "", lastOkAt: null } };
    expect(pollsStillOpen(info({ feeds: read }), "GA", t + 3_600_000)).toBe(false);
  });

  it("falls back to the feed's status, and treats unknown as not closed", () => {
    const open = { GA: { status: "polls_open", checkedAt: "", lastOkAt: null } };
    expect(pollsStillOpen(info({ pollsClose: undefined, feeds: open }), "GA", t)).toBe(true);
    const ok = { GA: { status: "ok", checkedAt: "", lastOkAt: null } };
    expect(pollsClosed(info({ pollsClose: undefined, feeds: ok }), "GA", t)).toBe(true);
    expect(pollsClosed(info({ pollsClose: undefined }), "GA", t)).toBe(false);
    expect(pollsStillOpen(info({ pollsClose: undefined }), "GA", t)).toBe(false);
  });

  it("is closed once a count is stored, and everywhere in the results phase", () => {
    expect(pollsClosed(info({ races: [race()] }), "GA", t)).toBe(true);
    expect(pollsStillOpen(info({ races: [race()] }), "GA", t)).toBe(false);
    const results = { ...day, phase: "results" as const };
    expect(pollsClosed(info({ phase: results }), "GA", t)).toBe(true);
    expect(pollsStillOpen(info({ phase: results }), "GA", t)).toBe(false);
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

  it("is fainter (lower opacity) with under half in and solid only when official", () => {
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
    // One seat: drawn exactly as resultFill draws that race (80% in).
    const one = race({ office: "H", district: 1, leaderParty: "IND" });
    expect(stateFill([one], "H", true, true)).toBe(resultFill(one, true));
    expect(stateFill([one], "H", true, true)).toMatch(/^rgba\(201,149,255, /);
  });

  it("compares every party, not just the two majors", () => {
    const h = (district: number, leaderParty: string) =>
      race({ office: "H", district, leaderParty, flip: false });
    // D 1, R 0, independents 3: the independents' state, not a blue one.
    expect(
      stateFill([h(1, "DEM"), h(2, "IND"), h(3, "IND"), h(4, "IND")], "H", true, true)
    ).toMatch(/^rgba\(201,149,255, /);
    // D 1, R 1, independents 2: the independents lead the most — not "tied".
    expect(
      stateFill([h(1, "DEM"), h(2, "REP"), h(3, "IND"), h(4, "IND")], "H", true, true)
    ).toMatch(/^rgba\(201,149,255, /);
    // D 2, independents 2: a split at the top is tied/split, whoever they are.
    expect(stateFill([h(1, "DEM"), h(2, "DEM"), h(3, "IND"), h(4, "IND")], "H", true, true)).toBe(
      TIED_FILL
    );
  });

  it("fades a House delegation by its least-counted seat, and is solid only when all are official", () => {
    const h = (district: number, extra: Partial<LiveRaceResult> = {}) =>
      race({ office: "H", district, leaderParty: "REP", flip: false, ...extra });
    // Two of three seats nearly in, one at 25%: under half in somewhere is faint.
    expect(
      stateFill(
        [h(1, { reportingUnits: 95 }), h(2, { reportingUnits: 95 }), h(3, { reportingUnits: 25 })],
        "H",
        true,
        true
      )
    ).toBe("rgba(255,137,137, 0.30)");
    // Every seat at 90%: the single-race scale at 90%.
    expect(
      stateFill([h(1, { reportingUnits: 90 }), h(2, { reportingUnits: 90 })], "H", true, true)
    ).toBe("rgba(255,137,137, 0.85)");
    // Official in one seat only: not solid.
    expect(
      stateFill([h(1, { official: true }), h(2, { reportingUnits: 90 })], "H", true, true)
    ).not.toBe("rgba(255,137,137, 1.00)");
    // Every seat official: solid.
    expect(stateFill([h(1, { official: true }), h(2, { official: true })], "H", true, true)).toBe(
      "rgba(255,137,137, 1.00)"
    );
    // A seat with nothing counted yet has nothing in: faint.
    expect(
      stateFill(
        [
          h(1, { reportingUnits: 95 }),
          h(2, { votesCounted: 0, reportingUnits: 0, candidates: [] }),
        ],
        "H",
        true,
        true
      )
    ).toBe("rgba(255,137,137, 0.30)");
  });

  it("draws a state electing both its senators from both races", () => {
    const regular = race({ raceId: "S1", leaderParty: "REP", flip: true });
    const special = race({
      raceId: "S2",
      isSpecial: true,
      leaderParty: "DEM",
      flip: false,
      candidates: [
        { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
        { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
      ],
    });
    // Split between the parties: tied/split, not the first race's colour.
    const split = stateShade("GA", [regular, special], "S", true, true);
    expect(split.fill).toBe(TIED_FILL);
    // Each race is named, so the change of party in the regular one shows.
    expect(split.label).toBe(
      "GA Senate: regular race Republican leads, 80% in, seat changing party; special race Democrat leads, 80% in"
    );
    // The same party ahead in both: that party.
    expect(
      stateShade(
        "GA",
        [regular, { ...special, leaderParty: "REP", candidates: regular.candidates }],
        "S",
        true,
        true
      ).fill
    ).toMatch(/^rgba\(255,137,137, /);
  });

  it("says each state's status in its accessible name, not in colour alone", () => {
    expect(stateShade("GA", [], "S", true, true, false, true).label).toBe("GA: polls open");
    expect(stateShade("GA", [], "S", true, true, true).label).toBe("GA: results feed not read");
    expect(stateShade("GA", [], "S", true, true).label).toBe("GA Senate: no votes yet");
    expect(stateShade("GA", [], "S", false, true).label).toBe("GA: no live count here");
    expect(stateShade("GA", [], "S", true, false).label).toBe("GA: no Senate race this year");
    expect(stateShade("GA", [race({ reportingUnits: 25 })], "S", true, true).label).toBe(
      "GA Senate: Republican leads, 25% in, early, seat changing party"
    );
    const house = [
      race({ office: "H", district: 1, leaderParty: "REP", flip: false }),
      race({ office: "H", district: 2, leaderParty: "REP", flip: true }),
      race({ office: "H", district: 3, leaderParty: "DEM", flip: false }),
    ];
    expect(stateShade("GA", house, "H", true, true).label).toBe(
      "GA House: Republicans lead in the most seats, seats led D 1, R 2, of 3 listed, 1 seat changing party"
    );
  });

  it("draws a covered state still voting as that, not as no votes yet", () => {
    expect(stateFill([], "S", true, true, false, true)).toBe(POLLS_OPEN_FILL);
    expect(stateFill([], "H", true, true, false, true)).toBe(POLLS_OPEN_FILL);
    // Uncovered stays uncovered.
    expect(stateFill([], "S", false, true, false, true)).toBe(UNCOVERED_FILL);
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
    // Never "wins", even official: Civitas never calls a race.
    const official = describeUpdate(event("flip", { official: true })).text;
    expect(official).toMatch(
      /^Ray Jones \(R\) leads in the count the state lists as official, in a seat Democrats hold\./
    );
    expect(official).not.toMatch(/\bwins?\b|\bwon\b/i);
  });

  it("says an exact tie is tied and never names the runner-up alone", () => {
    // The backend sends no leader on a tie, and the second of the two
    // level candidates as runnerUp.
    const tie = { leader: null, runnerUp: { name: "Sam Roe", party: "REP", votes: 950, pct: 50 } };
    for (const kind of ["first_returns", "all_reporting", "official", "update"]) {
      const text = describeUpdate(event(kind, { ...tie, votesCounted: 1900 })).text;
      expect(text).toMatch(/The top two are tied at 50% each\./);
      expect(text).not.toMatch(/Sam Roe/);
    }
    // Nothing counted: no names, no tie.
    const none = describeUpdate(
      event("first_returns", {
        leader: null,
        runnerUp: { name: "Sam Roe", party: "REP", votes: 0, pct: null },
        votesCounted: 0,
      })
    ).text;
    expect(none).not.toMatch(/Sam Roe|tied/);
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
    expect(describeUpdate(event("all_reporting")).text).toMatch(
      /^All 2,653 precincts have reported\./
    );
    // No unit count from the state: never "All 0 precincts".
    expect(
      describeUpdate(event("all_reporting", { totalUnits: null, reportingUnits: null })).text
    ).toMatch(/^Every reporting unit is in\./);
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

describe("raceStatusText", () => {
  it("says where a race stands without calling it", () => {
    expect(raceStatusText(race({ votesCounted: 0 }))).toBe("no votes yet");
    expect(raceStatusText(race({ official: true, flip: false }))).toBe(
      "Republican leads, official count"
    );
    expect(raceStatusText(race({ totalUnits: null, flip: false, leaderParty: null }))).toBe(
      "another party leads"
    );
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

describe("seatsLed", () => {
  it("counts a leader outside the known parties, and no one for a tie or nothing counted", () => {
    const led = seatsLed([
      race({ office: "H", district: 1, leaderParty: "DEM" }),
      race({ office: "H", district: 2, leaderParty: "IND" }),
      race({
        office: "H",
        district: 3,
        leaderParty: null,
        candidates: [
          { name: "Pat Doe", party: null, votes: 1000, pct: 52.6, candidateId: null },
          { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
        ],
      }),
      race({ office: "H", district: 4, leaderParty: null, votesCounted: 0 }),
      race({
        office: "H",
        district: 5,
        leaderParty: null,
        candidates: [
          { name: "A", party: "REP", votes: 950, pct: 50, candidateId: null },
          { name: "B", party: "DEM", votes: 950, pct: 50, candidateId: null },
        ],
      }),
    ]);
    expect(led).toEqual({ DEM: 1, IND: 1, OTHER: 1 });
    expect(formatLed(led)).toBe("D 1 · R 0 · I 1 · OTHER 1");
  });
});

describe("the count-less fills' textures", () => {
  // WCAG 2.x contrast of two sRGB colours, each an rgba composited over `bg`.
  const parse = (c: string) => {
    if (c.startsWith("#"))
      return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)).concat(1) as number[];
    return c
      .match(/[\d.]+/g)!
      .map(Number)
      .concat(1)
      .slice(0, 4);
  };
  const over = (c: string, bg: number[]) => {
    const [r, g, b, a] = parse(c);
    return [r, g, b].map((v, i) => a * v + (1 - a) * bg[i]);
  };
  const lum = ([r, g, b]: number[]) => {
    const ch = (v: number) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
  };
  const ratio = (a: number[], b: number[]) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const SURFACE = parse("#14110e"); // --surface, the map panel

  it("puts marks at least 3:1 against every dark fill and the page (WCAG 1.4.11)", () => {
    const fills = [AWAITING_FILL, UNCOVERED_FILL, POLLS_OPEN_FILL, FEED_FAILED_FILL].map((f) =>
      over(f, SURFACE)
    );
    for (const [mark, base] of [
      [POLLS_OPEN_MARK, POLLS_OPEN_FILL],
      [FEED_FAILED_MARK, FEED_FAILED_FILL],
      [AWAITING_MARK, AWAITING_FILL],
    ]) {
      // The mark as drawn: over its own fill, over the panel.
      const drawn = over(mark, over(base, SURFACE));
      for (const f of [...fills, SURFACE]) expect(ratio(drawn, f)).toBeGreaterThanOrEqual(3);
    }
  });
});
