import { describe, expect, it } from "vitest";
import {
  AWAITING_FILL,
  AWAITING_MARK,
  NO_COUNT_FILL,
  NO_COUNT_STRIPE,
  FEED_FAILED_MARK,
  POLLS_OPEN_MARK,
  STALE_SHADOW,
  countReadAt,
  countReadRange,
  resultsNow,
  serverTimeOf,
  stateFeedBehind,
  everyLiveStateVoting,
  feedBehind,
  feedFailed,
  FEED_FAILED_FILL,
  resultsSyncInterval,
  isTied,
  TIED_FILL,
  UNCOVERED_FILL,
  heldByPhrase,
  describeUpdate,
  formatLed,
  NO_PARTY_KEY,
  partyTag,
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
  flipShown,
  flipNotShownTag,
  flipNotShownText,
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

  it("calls a chamber the counting feed gives nothing for 'no count', not 'no votes yet'", () => {
    // The feed counts the state's House races but gives no Senate row.
    const house = [race({ office: "H", district: 1, raceId: "H1" })];
    const senate = stateShade("GA", house, "S", true, true);
    expect(senate.fill).toBe(NO_COUNT_FILL);
    expect(senate.label).toBe("GA Senate: no count from the state's feed");
    expect(senate.stale).toBe(false);
    // ...whatever the feed's latest read: it did answer, for the House.
    expect(stateShade("GA", house, "S", true, true, true).label).toBe(
      "GA Senate: no count from the state's feed"
    );
    // The reverse: a Senate count and no House row.
    const onlySenate = stateShade("GA", [race()], "H", true, true);
    expect(onlySenate.fill).toBe(NO_COUNT_FILL);
    expect(onlySenate.label).toBe("GA House: no count from the state's feed");
    // A chamber the feed lists but whose count is zero is still "no votes yet".
    expect(
      stateShade(
        "GA",
        [race({ votesCounted: 0, candidates: [], leaderParty: null, flip: false })],
        "S",
        true,
        true
      ).label
    ).toBe("GA Senate: no votes yet");
    // No count from the feed at all: "no votes yet", as before.
    expect(stateShade("GA", [], "S", true, true).fill).toBe(AWAITING_FILL);
  });

  it("keeps the no-count fill AWAITING_FILL's colour, spelt apart for its own texture", () => {
    const rgb = (c: string) =>
      c.startsWith("#")
        ? [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16))
        : c.match(/\d+/g)!.map(Number).slice(0, 3);
    expect(NO_COUNT_FILL).not.toBe(AWAITING_FILL);
    expect(rgb(NO_COUNT_FILL)).toEqual(rgb(AWAITING_FILL));
  });

  it("says each state's status in its accessible name, not in colour alone", () => {
    expect(stateShade("GA", [], "S", true, true, false, true).label).toBe(
      "GA: polls not yet closed"
    );
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
    // A held flip (announced; the latest count has the holder ahead) is
    // named as that, and not counted as a seat changing party.
    const held = [...house, heldFlip({ raceId: "2026-HOUSE-GA-4", office: "H", district: 4 })];
    expect(stateShade("GA", held, "H", true, true).label).toBe(
      "GA House: seats split evenly, seats led D 2, R 2, of 4 listed, 1 seat changing party, 1 change of party announced earlier that the latest count doesn't show"
    );
    // Nothing counted in any seat, one with a change of party announced
    // earlier: that is the latest count, not "no votes yet".
    const empty = [
      race({ office: "H", district: 1, votesCounted: 0, flip: false }),
      heldFlip({ raceId: "2026-HOUSE-GA-2", office: "H", district: 2, votesCounted: 0 }),
    ];
    expect(stateShade("GA", empty, "H", true, true).label).toBe(
      "GA House: no votes in the latest count, 1 change of party announced earlier that the latest count doesn't show"
    );
    expect(stateShade("GA", [empty[0], { ...empty[0], district: 2 }], "H", true, true).label).toBe(
      "GA House: no votes yet"
    );
    expect(stateShade("GA", [heldFlip()], "S", true, true).label).toBe(
      "GA Senate: Democrat leads, 80% in, change of party announced earlier, holder's party ahead in the latest count"
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
    // An older count still shown keeps its colour — marked stale, so it
    // never passes for a live one.
    expect(stateFill([race()], "S", true, true, true)).toMatch(/^rgba\(255,137,137/);
    expect(stateShade("GA", [race()], "S", true, true, true).stale).toBe(true);
    expect(stateShade("GA", [race(), race({ isSpecial: true })], "S", true, true, true).stale).toBe(
      true
    );
    expect(stateShade("GA", [race()], "S", true, true, false).stale).toBe(false);
    // Nothing to mark stale without a count, or a feed.
    expect(stateShade("GA", [], "S", true, true, true).stale).toBe(false);
    expect(stateShade("GA", [race()], "S", false, true, true).stale).toBe(false);
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
    // Never a bare OFFICIAL beside the shares: that reads as a result.
    expect(describeUpdate(event("official")).tag).toBe("OFFICIAL COUNT");
    expect(describeUpdate(event("official")).text).toMatch(/Not called\.$/);
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
    expect(raceStatusText(race({ votesCounted: 0, flip: false }))).toBe("no votes yet");
    expect(raceStatusText(race({ official: true, flip: false }))).toBe(
      "Republican leads, official count"
    );
    // A party the vocabulary doesn't name is another party; no party given
    // is said as that, never "another party".
    expect(raceStatusText(race({ totalUnits: null, flip: false, leaderParty: "WFP" }))).toBe(
      "another party leads"
    );
    expect(raceStatusText(race({ totalUnits: null, flip: false, leaderParty: null }))).toBe(
      "leader's party not given"
    );
  });

  it("says an announced flip before 'no votes yet' and 'tied'", () => {
    // A held poll that fell to zero votes: the counter lists it as not
    // counted, so its own wording must say why.
    expect(raceStatusText(heldFlip({ votesCounted: 0, leaderParty: null }))).toBe(
      "no votes in the latest count, change of party announced earlier"
    );
    const tied = heldFlip({
      leaderParty: null,
      candidates: [
        { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
        { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
      ],
    });
    expect(raceStatusText(tied)).toBe("tied, 80% in, change of party announced earlier");
    // A leader whose party isn't given: said once, as what is unknown.
    const noParty = heldFlip({
      leaderParty: null,
      candidates: [
        { name: "Indy Pen", party: null, votes: 1000, pct: 52.6, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
      ],
    });
    expect(raceStatusText(noParty)).toBe(
      "leader's party not given, 80% in, change of party announced earlier"
    );
  });

  it("never says 'changing party' for a flip the count no longer shows", () => {
    // A held poll: the flip stands (announced), the figures show the holder ahead.
    expect(raceStatusText(heldFlip())).toBe(
      "Democrat leads, 80% in, change of party announced earlier, holder's party ahead in the latest count"
    );
    expect(raceStatusText(race())).toBe("Republican leads, 80% in, seat changing party");
  });
});

/** A flip announced earlier whose latest (held) poll shows the holder ahead. */
function heldFlip(overrides: Partial<LiveRaceResult> = {}): LiveRaceResult {
  return race({
    flip: true,
    leaderParty: "DEM",
    candidates: [
      { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
    ],
    ...overrides,
  });
}

describe("flipShown / flipNotShownText", () => {
  it("says a seat is changing party only while the figures show it", () => {
    expect(flipShown(race())).toBe(true);
    expect(flipNotShownText(race())).toBeNull();
    expect(flipShown(heldFlip())).toBe(false);
    expect(flipNotShownText(heldFlip())).toBe("holder's party ahead in the latest count");
    const tied = heldFlip({
      leaderParty: null,
      candidates: [
        { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
        { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
      ],
    });
    expect(flipShown(tied)).toBe(false);
    expect(flipNotShownText(tied)).toBe("tied in the latest count");
    expect(flipNotShownTag(tied)).toBe("TIED");
    // Zero votes in a held poll: announced, not shown — never "seat changing party".
    const empty = heldFlip({ votesCounted: 0, leaderParty: "REP" });
    expect(flipShown(empty)).toBe(false);
    expect(flipNotShownText(empty)).toBe("no votes in the latest count");
    expect(flipNotShownTag(empty)).toBe("NO VOTES IN THIS COUNT");
    // A leader with no party given: what is unknown, not "another party".
    const noParty = heldFlip({
      leaderParty: null,
      candidates: [
        { name: "Indy Pen", party: null, votes: 1000, pct: 52.6, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
      ],
    });
    expect(flipNotShownText(noParty)).toBe("leader's party not given in the latest count");
    expect(flipNotShownTag(noParty)).toBe("LEADER'S PARTY NOT GIVEN");
    expect(flipNotShownTag(heldFlip())).toBe("HOLDER'S PARTY LEADS");
    expect(flipNotShownTag(race())).toBeNull();
    // No flip announced: neither.
    expect(flipShown(race({ flip: false }))).toBe(false);
    expect(flipNotShownText(race({ flip: false }))).toBeNull();
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
    expect(s.flipsNotShown).toBe(0);
  });

  it("does not count a held flip as a seat changing party", () => {
    const s = summarizeState([race(), heldFlip({ raceId: "2026-SEN-GA-S", isSpecial: true })]);
    expect(s.flips).toBe(1);
    expect(s.flipsNotShown).toBe(1);
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
    // A leader with no party given is counted apart from any real party,
    // and said as that — never "OTHER".
    expect(led).toEqual({ DEM: 1, IND: 1, [NO_PARTY_KEY]: 1 });
    expect(formatLed(led)).toBe("D 1 · R 0 · I 1 · party not given 1");
  });

  it("keeps a real party the vocabulary doesn't name apart from an unstated one", () => {
    const pat = { name: "Pat Doe", party: null, votes: 1000, pct: 52.6, candidateId: null };
    const led = seatsLed([
      race({ office: "H", district: 1, leaderParty: "WFP" }),
      race({ office: "H", district: 2, leaderParty: null, candidates: [pat] }),
      race({ office: "H", district: 3, leaderParty: null, candidates: [pat] }),
    ]);
    expect(led).toEqual({ WFP: 1, [NO_PARTY_KEY]: 2 });
    expect(formatLed({ [NO_PARTY_KEY]: 2, WFP: 1 })).toBe("D 0 · R 0 · WFP 1 · party not given 2");
    expect(partyTag(null)).toBe("party not given");
    expect(partyTag("IND")).toBe("I");
  });

  it("says a delegation led by leaders with no party given as that", () => {
    const pat = { name: "Pat Doe", party: null, votes: 1000, pct: 52.6, candidateId: null };
    const house = [
      race({ office: "H", district: 1, leaderParty: null, flip: false, candidates: [pat] }),
      race({ office: "H", district: 2, leaderParty: null, flip: false, candidates: [pat] }),
    ];
    const shade = stateShade("GA", house, "H", true, true);
    expect(shade.label).toBe(
      "GA House: leaders whose party isn't given lead in the most seats, seats led D 0, R 0, party not given 2, of 2 listed"
    );
    expect(shade.label).not.toMatch(/other/i);
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
      // DistrictMap's "no count from the state's feed" hatch, drawn over
      // AWAITING_FILL beside every other count-less fill.
      [NO_COUNT_STRIPE, AWAITING_FILL],
    ]) {
      // The mark as drawn: over its own fill, over the panel.
      const drawn = over(mark, over(base, SURFACE));
      for (const f of [...fills, SURFACE]) expect(ratio(drawn, f)).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("the stale stripe", () => {
  // As in the count-less fills' test above.
  const parse = (c: string) =>
    c.startsWith("#")
      ? ([1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)).concat(1) as number[])
      : c
          .match(/[\d.]+/g)!
          .map(Number)
          .concat(1)
          .slice(0, 4);
  const over = (c: string, bg: number[]) => {
    const [r, g, b, a] = parse(c);
    return [r, g, b].map((v, i) => a * v + (1 - a) * bg[i]);
  };
  const lum = ([r, g, b]: number[]) => {
    const ch = (v: number) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
  };
  const ratio = (a: number[], b: number[]) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const SURFACE = parse("#14110e");

  it("has a mark at least 3:1 against every fill a count can have (WCAG 1.4.11)", () => {
    const fills = [TIED_FILL, AWAITING_FILL];
    for (const rgb of ["130,172,255", "255,137,137", "201,149,255"])
      for (const a of [0.3, 0.45, 0.55, 0.9, 1]) fills.push(`rgba(${rgb}, ${a})`);
    for (const f of fills) {
      const base = over(f, SURFACE);
      const best = Math.max(
        ratio(over(FEED_FAILED_MARK, base), base),
        ratio(over(STALE_SHADOW, base), base)
      );
      expect(best, f).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("the clock the results pages judge time by", () => {
  const clock = { serverDate: Date.parse("2026-11-04T03:00:00Z"), receivedAt: 1_000_000 };

  it("is the server's Date, not the browser's clock, however far off that is", () => {
    // Browser three hours slow, or a quarter hour fast: the server's time.
    expect(resultsNow({ clock }, 1_000_000)).toBe(clock.serverDate);
    expect(resultsNow({ clock }, 1_000_000 + 30_000)).toBe(clock.serverDate + 30_000);
  });

  it("runs on at most one poll interval, so a tab back from hiding isn't judged by its absence", () => {
    expect(resultsNow({ clock }, 1_000_000 + 60 * 60_000)).toBe(clock.serverDate + 60_000);
    // A browser clock that jumped back runs nothing on.
    expect(resultsNow({ clock }, 0)).toBe(clock.serverDate);
  });

  it("stops where it stood when the page's own refresh failed — never back at the last answer", () => {
    // Failed 45 s after the answer: stays at +45 s however long it fails.
    expect(resultsNow({ clock }, 1_000_000 + 45_000, 1_000_000 + 45_000)).toBe(
      clock.serverDate + 45_000
    );
    expect(resultsNow({ clock }, 1_000_000 + 30 * 60_000, 1_000_000 + 45_000)).toBe(
      clock.serverDate + 45_000
    );
    // Failed after the run-on was used up: stays at +60 s, where it stood,
    // rather than jumping back a minute (which re-opened closed polls).
    expect(resultsNow({ clock }, 1_000_000 + 60_000, null)).toBe(clock.serverDate + 60_000);
    expect(resultsNow({ clock }, 1_000_000 + 5 * 60_000, 1_000_000 + 61_000)).toBe(
      clock.serverDate + 60_000
    );
  });

  it("dates a browser-measured moment on the server's clock", () => {
    // A browser three hours slow: the failure 90 s after the answer is 90 s
    // after the server's Date, not three hours before the count.
    const slow = {
      serverDate: Date.parse("2026-11-04T03:00:00Z"),
      receivedAt: Date.parse("2026-11-04T00:00:00Z"),
    };
    expect(serverTimeOf({ clock: slow }, slow.receivedAt + 90_000)).toBe(
      Date.parse("2026-11-04T03:01:30Z")
    );
    expect(serverTimeOf(null, 1234)).toBe(1234);
    expect(serverTimeOf({ clock: { serverDate: null, receivedAt: 5_000 } }, 6_000)).toBe(6_000);
  });

  it("falls back to the arrival time with no Date header, and to the browser without a clock", () => {
    expect(resultsNow({ clock: { serverDate: null, receivedAt: 5_000 } }, 6_000)).toBe(6_000);
    expect(resultsNow({ clock: { serverDate: null, receivedAt: 5_000 } }, 9e9)).toBe(65_000);
    expect(resultsNow(null, 1234)).toBe(1234);
  });
});

describe("whether the backend is still reading a state's feed", () => {
  const moving = { lastResultChange: "2026-11-04T02:42:00Z" };
  const at = (iso: string) => Date.parse(iso);

  it("reads the sync's own cadence: five minutes, hourly once no count has moved for a day", () => {
    expect(resultsSyncInterval(moving, at("2026-11-04T03:00:00Z"))).toBe(5 * 60_000);
    expect(resultsSyncInterval({ lastResultChange: null }, at("2026-11-04T03:00:00Z"))).toBe(
      5 * 60_000
    );
    expect(resultsSyncInterval(moving, at("2026-11-05T02:43:00Z"))).toBe(60 * 60_000);
  });

  it("calls a feed behind past 15 minutes while counts move, 70 once hourly", () => {
    const feed = { checkedAt: "2026-11-04T03:00:00Z" };
    expect(feedBehind(feed, moving, at("2026-11-04T03:14:00Z"))).toBe(false);
    expect(feedBehind(feed, moving, at("2026-11-04T03:16:00Z"))).toBe(true);
    const settled = { checkedAt: "2026-11-06T03:00:00Z" };
    expect(feedBehind(settled, moving, at("2026-11-06T04:05:00Z"))).toBe(false);
    expect(feedBehind(settled, moving, at("2026-11-06T04:11:00Z"))).toBe(true);
  });

  it("is never behind with no record to judge by", () => {
    expect(feedBehind(undefined, moving, at("2026-11-20T00:00:00Z"))).toBe(false);
    expect(feedBehind({ checkedAt: null }, moving, at("2026-11-20T00:00:00Z"))).toBe(false);
  });

  it("calls a state behind with no read record once its polls closed well over a pass ago", () => {
    const base = {
      phase: {
        ...moving,
        phase: "election_day" as const,
        electionDate: "2026-11-03",
        resultsUntil: null,
      },
      pollsClose: { GA: "2026-11-04T00:00:00Z" },
      feeds: {},
    };
    expect(stateFeedBehind(base, "GA", at("2026-11-04T00:14:00Z"))).toBe(false);
    expect(stateFeedBehind(base, "GA", at("2026-11-04T00:16:00Z"))).toBe(true);
    // An older backend that keeps no read records: nothing to judge by.
    expect(stateFeedBehind({ ...base, feeds: undefined }, "GA", at("2026-11-05T00:00:00Z"))).toBe(
      false
    );
    // No known closing time: nothing to judge by either.
    expect(stateFeedBehind({ ...base, pollsClose: {} }, "GA", at("2026-11-05T00:00:00Z"))).toBe(
      false
    );
    // With a record, the record decides.
    const read = { GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: null } };
    expect(stateFeedBehind({ ...base, feeds: read }, "GA", at("2026-11-04T03:10:00Z"))).toBe(false);
    expect(stateFeedBehind({ ...base, feeds: read }, "GA", at("2026-11-04T03:16:00Z"))).toBe(true);
  });
});

describe("countReadAt", () => {
  it("is a state's last good read, else its newest race read", () => {
    const results = {
      feeds: {
        GA: {
          status: "stale",
          checkedAt: "2026-11-04T03:30:00Z",
          lastOkAt: "2026-11-04T03:00:00Z",
        },
      },
      races: [
        race({ state: "GA", fetchedAt: "2026-11-04T03:05:00Z" }),
        race({ state: "NC", fetchedAt: "2026-11-04T03:20:00Z" }),
      ],
    };
    expect(countReadAt(results, "GA")).toBe("2026-11-04T03:00:00Z");
    expect(countReadAt(results, "NC")).toBe("2026-11-04T03:20:00Z");
    expect(countReadAt({ races: [] }, "GA")).toBeNull();
  });

  it("is the newest read of any state for the whole page", () => {
    expect(
      countReadAt({
        feeds: {
          GA: { status: "ok", checkedAt: "2026-11-04T03:30:00Z", lastOkAt: "2026-11-04T03:25:00Z" },
        },
        races: [race({ fetchedAt: "2026-11-04T03:10:00Z" })],
      })
    ).toBe("2026-11-04T03:25:00Z");
    expect(countReadAt({ races: [] })).toBeNull();
  });
});

describe("countReadRange", () => {
  it("gives the oldest and newest read of the states with a count", () => {
    const feeds = {
      GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T01:00:00Z" },
      NC: { status: "ok", checkedAt: "2026-11-04T03:20:00Z", lastOkAt: "2026-11-04T03:20:00Z" },
      // A state with no count doesn't stretch the range.
      OH: { status: "ok", checkedAt: "2026-11-04T03:30:00Z", lastOkAt: "2026-11-04T03:30:00Z" },
    };
    const races = [race(), race({ state: "NC", raceId: "2026-SEN-NC" })];
    expect(countReadRange({ feeds, races })).toEqual({
      oldest: "2026-11-04T01:00:00Z",
      newest: "2026-11-04T03:20:00Z",
    });
    expect(countReadRange({ feeds, races: [] })).toBeNull();
  });
});

describe("everyLiveStateVoting", () => {
  const base = {
    phase: {
      phase: "election_day",
      electionDate: "2026-11-03",
      resultsUntil: null,
      lastResultChange: null,
    } as ElectionPhaseInfo,
    liveStates: ["GA", "WA"],
    pollsClose: { GA: "2026-11-04T00:00:00Z", WA: "2026-11-04T04:00:00Z" },
    races: [] as LiveRaceResult[],
  };

  it("is true on election day until the first covered state's polls close", () => {
    expect(everyLiveStateVoting(base, Date.parse("2026-11-03T13:00:00Z"))).toBe(true);
    expect(everyLiveStateVoting(base, Date.parse("2026-11-04T00:00:00Z"))).toBe(false);
  });

  it("is false with a count stored, with no live state, or outside election day", () => {
    const morning = Date.parse("2026-11-03T13:00:00Z");
    expect(everyLiveStateVoting({ ...base, races: [race()] }, morning)).toBe(false);
    expect(everyLiveStateVoting({ ...base, liveStates: [] }, morning)).toBe(false);
    expect(
      everyLiveStateVoting({ ...base, phase: { ...base.phase, phase: "results" } }, morning)
    ).toBe(false);
  });
});

describe("a state electing both its senators, on the national map", () => {
  const regular = (o: Partial<LiveRaceResult> = {}) => race({ raceId: "2026-SEN-GA", ...o });
  const special = (o: Partial<LiveRaceResult> = {}) =>
    race({ raceId: "2026-SEN-GA-S", isSpecial: true, ...o });
  const rep = {
    leaderParty: "REP",
    candidates: [
      { name: "A", party: "REP", votes: 1000, pct: 52, candidateId: null },
      { name: "B", party: "DEM", votes: 900, pct: 48, candidateId: null },
    ],
  } as Partial<LiveRaceResult>;

  it("is the party leading more of its two races, as the footnote says", () => {
    // One race led, the other with nothing counted: that party leads more.
    expect(
      stateFill([regular(rep), special({ votesCounted: 0, candidates: [] })], "S", true, true)
    ).toMatch(/^rgba\(255,137,137,/);
    // Both led by one party.
    expect(stateFill([regular(rep), special(rep)], "S", true, true)).toMatch(/^rgba\(255,137,137,/);
  });

  it("is grey when two parties lead equally many", () => {
    const dem = {
      leaderParty: "DEM",
      candidates: [
        { name: "C", party: "DEM", votes: 1000, pct: 52, candidateId: null },
        { name: "D", party: "REP", votes: 900, pct: 48, candidateId: null },
      ],
    } as Partial<LiveRaceResult>;
    expect(stateFill([regular(rep), special(dem)], "S", true, true)).toBe(TIED_FILL);
  });
});
