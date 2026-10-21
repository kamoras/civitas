import { describe, expect, it } from "vitest";
import type { DayReport } from "@/types/congress";
import type { StateBallot } from "@/types/election";
import {
  ballotTiles,
  type RecordCard,
  chamberTile,
  formatStanding,
  parseCongressDate,
  parseIssueId,
  parsePoliticianId,
  parseBillRef,
  parseRecordId,
  parseStateCode,
  parseVoteRef,
  recordCardKey,
  recordCardTexts,
  billStage,
  parseAgencySlug,
  parseBillPageRef,
  parseNominationRef,
  parseRaceId,
  initialsOf,
  profileCardTexts,
  raceCardTexts,
} from "./route";

describe("formatStanding", () => {
  it("formats a senator as party-state, no district", () => {
    expect(formatStanding({ party: "D", state: "CA" })).toBe("D-CA");
  });

  it("formats a representative as party-state-district", () => {
    expect(formatStanding({ party: "R", state: "TX", district: 12 })).toBe("R-TX-12");
  });

  // FEC codes at-large districts as "00" -> district 0. The bug this
  // guards against: a truthy `identity?.district ?` check treated 0 like
  // "no district", silently rendering an at-large representative in the
  // Senate's party-state format instead of party-state-AL. Same bug
  // class already guarded in lib/elections.test.ts.
  it("formats an at-large district (0) as AL, not as a missing district", () => {
    expect(formatStanding({ party: "D", state: "AK", district: 0 })).toBe("D-AK-AL");
  });

  it("returns an empty string when neither state nor district is known", () => {
    expect(formatStanding({})).toBe("");
    expect(formatStanding(undefined)).toBe("");
  });
});

describe("parseIssueId", () => {
  // The bug this guards against: every real link on the site points at
  // issue.publicId ("i" + 8 hex chars, e.g. "i9e3779b1"), never the raw
  // numeric id — a digits-only check here rejected every real request
  // and silently fell through to the generic fallback card for every
  // issue, photo or not, regardless of how much real content it had.
  it("accepts a real public id", () => {
    expect(parseIssueId("i9e3779b1")).toBe("i9e3779b1");
  });

  it("accepts a public id regardless of letter case", () => {
    expect(parseIssueId("I9E3779B1")).toBe("I9E3779B1");
  });

  it("still accepts a legacy bare numeric id", () => {
    expect(parseIssueId("650")).toBe("650");
  });

  it("rejects a public id with the wrong hex length", () => {
    expect(parseIssueId("i9e3779b")).toBeNull();
    expect(parseIssueId("i9e3779b12")).toBeNull();
  });

  it("rejects a non-hex suffix", () => {
    expect(parseIssueId("i9e3779zz")).toBeNull();
  });

  it("rejects garbage and empty input", () => {
    expect(parseIssueId("not-an-id")).toBeNull();
    expect(parseIssueId("")).toBeNull();
    expect(parseIssueId(null)).toBeNull();
  });
});

describe("parseStateCode", () => {
  it("accepts a real state code and uppercases it", () => {
    expect(parseStateCode("tx")).toBe("TX");
    expect(parseStateCode("CA")).toBe("CA");
  });

  it("accepts DC", () => {
    expect(parseStateCode("dc")).toBe("DC");
  });

  it("rejects anything that isn't exactly 2 letters", () => {
    expect(parseStateCode("texas")).toBeNull();
    expect(parseStateCode("t")).toBeNull();
    expect(parseStateCode("12")).toBeNull();
  });

  // The bug this guards against: a shape-only check (any 2 letters) let
  // a syntactically-valid but non-existent code through to render a
  // fully-formed, plausible-looking "ZZ Ballot 2026" share card —
  // asserting ZZ is a real jurisdiction rather than falling back to a
  // generic card the way an unknown issue/politician id already does.
  it("rejects a syntactically valid but non-existent state code", () => {
    expect(parseStateCode("zz")).toBeNull();
    expect(parseStateCode("xx")).toBeNull();
  });

  it("rejects garbage and empty input", () => {
    expect(parseStateCode("")).toBeNull();
    expect(parseStateCode(null)).toBeNull();
  });
});

describe("parsePoliticianId", () => {
  it("accepts every id shape the backend issues", () => {
    expect(parsePoliticianId("grassley-chuck")).toBe("grassley-chuck");
    expect(parsePoliticianId("bush-41")).toBe("bush-41");
    // Oyez identifiers — rejected before, so every justice got the generic card.
    expect(parsePoliticianId("ketanji_brown_jackson")).toBe("ketanji_brown_jackson");
  });

  it("rejects anything that could change the outgoing fetch path", () => {
    expect(parsePoliticianId(null)).toBeNull();
    expect(parsePoliticianId("")).toBeNull();
    expect(parsePoliticianId("../admin")).toBeNull();
    expect(parsePoliticianId("a/b")).toBeNull();
    expect(parsePoliticianId("a?b=c")).toBeNull();
  });
});

describe("profileCardTexts", () => {
  it("shows a member's FEC figures, never a composite score", () => {
    const t = profileCardTexts({
      branch: "senate",
      identity: { name: "A Member", party: "R", state: "IA" },
      scorecard: {
        funding: { totalContributions: 9_290_490, pacSharePct: 32.4, smallDonorPercentage: 25 },
      },
    });
    expect(t.eyebrow).toBe("U.S. Senate");
    expect(t.line).toBe("R-IA");
    expect(t.tiles).toEqual([
      { value: "$9.3M", label: "Contributions, latest election" },
      { value: "32%", label: "From PACs" },
      { value: "25%", label: "In gifts of $200 or less" },
    ]);
    expect(t.sources).toBe("FEC filings");
  });

  // Null is not zero: a figure the record lacks is left off the card.
  it("leaves off a figure the record doesn't hold", () => {
    const t = profileCardTexts({
      branch: "house",
      identity: { name: "B", party: "D", state: "AK", district: 0 },
      scorecard: { funding: { totalContributions: null, pacSharePct: 10 } },
    });
    expect(t.line).toBe("D-AK-AL");
    expect(t.tiles.map((x) => x.label)).toEqual(["From PACs"]);
  });

  it("gives a president the historians' rank with its source", () => {
    const t = profileCardTexts(
      {
        branch: "president",
        identity: {
          name: "C",
          party: "D",
          number: 44,
          termStart: "2009-01-20",
          termEnd: "2017-01-20",
        },
        scorecard: { avgApproval: 47.97, eoCount: 276 },
      },
      { rank: 10, rated: 45 }
    );
    expect(t.line).toBe("44th president · 2009 to 2017");
    expect(t.tiles).toEqual([
      { value: "#10 of 45", label: "C-SPAN historians' rank" },
      { value: "48%", label: "Average approval" },
      { value: "276", label: "Executive orders" },
    ]);
  });

  it("says a presidency the survey hasn't rated is not rated", () => {
    const t = profileCardTexts(
      { branch: "president", identity: { name: "D", number: 47, termStart: "2025-01-20" } },
      { rank: null, rated: 45 }
    );
    expect(t.line).toBe("47th president · 2025 to present");
    expect(t.tiles.at(-1)).toEqual({ value: "Not rated", label: "C-SPAN historians' rank" });
  });

  it("gives a justice the voting record and no score", () => {
    const t = profileCardTexts({
      branch: "scotus",
      identity: { name: "E", appointingPresident: "A President", dateStart: "2022-06-30" },
      scorecard: { casesDecided: 175, majorityPct: 71.4, authoredMajority: 16 },
    });
    expect(t.eyebrow).toBe("Supreme Court");
    expect(t.line).toBe("Appointed by A President, since 2022");
    expect(t.tiles.map((x) => x.value)).toEqual(["175", "71%", "16"]);
  });
});

describe("initialsOf", () => {
  it("takes the first and last names' initials", () => {
    expect(initialsOf("Joseph R. Biden, Jr.")).toBe("JB");
    expect(initialsOf("Barack Obama")).toBe("BO");
    expect(initialsOf("Madonna")).toBe("M");
  });
});

describe("raceCardTexts", () => {
  const race = {
    id: "2026-HOUSE-TX-12",
    cycleYear: 2026,
    office: "H",
    state: "TX",
    district: 12,
    usualVote: { label: "R+12" },
    candidates: [
      {
        name: "ROE, JANE",
        ballotName: "Jane Roe",
        partyGroup: "DEM",
        confirmed: true,
        contributions: 1000,
      },
      {
        name: "DOE, JOHN",
        partyGroup: "REP",
        confirmed: true,
        incumbentChallenge: "I",
        contributions: 2_500_000,
      },
      { name: "FILER, UN", partyGroup: "REP", confirmed: false, contributions: 9_000_000 },
      { name: "SMALL, PARTY", partyGroup: "LIB", partyLabel: "Libertarian Party", confirmed: true },
    ],
  };

  it("names the seat and lists the confirmed candidates by money raised", () => {
    const t = raceCardTexts(race);
    expect(t.title).toBe("Texas's 12th district, U.S. House");
    expect(t.lean).toBe("Usually votes R+12");
    expect(t.rows.map((r) => [r.name, r.party, r.incumbent, r.money])).toEqual([
      ["DOE, JOHN", "Republican", true, "$2.5M raised"],
      ["Jane Roe", "Democrat", false, "$1K raised"],
      ["SMALL, PARTY", "Libertarian Party", false, ""],
    ]);
  });

  it("names a Senate seat and an at-large seat", () => {
    expect(raceCardTexts({ ...race, office: "S", district: null }).title).toBe(
      "Texas, U.S. Senate"
    );
    expect(raceCardTexts({ ...race, state: "AK", district: 0 }).title).toBe(
      "Alaska at large, U.S. House"
    );
  });
});

describe("billStage", () => {
  const record = (
    stops: { key: string; label: string; status: string; detail: string | null; date?: string }[]
  ) => ({ journey: { stops } }) as unknown as Parameters<typeof billStage>[0];

  it("names the furthest stop reached, with what happened there", () => {
    expect(
      billStage(
        record([
          { key: "introduced", label: "Introduced in the House", status: "done", detail: null },
          { key: "committee", label: "Committee", status: "current", detail: "Referred" },
          { key: "floor", label: "House floor", status: "ahead", detail: null },
        ])
      )
    ).toBe("Committee: Referred");
    expect(
      billStage(
        record([
          { key: "introduced", label: "Introduced", status: "done", detail: null },
          { key: "president", label: "President", status: "done", detail: "Signed into law" },
        ])
      )
    ).toBe("President: Signed into law");
    expect(
      billStage(
        record([
          {
            key: "committee",
            label: "Committee",
            status: "current",
            detail: "Reported by the committee",
          },
        ])
      )
    ).toBe("Reported by the committee");
    expect(
      billStage(
        record([
          {
            key: "committee",
            label: "Committee",
            status: "current",
            detail: "In committee since",
            date: "2025-01-03",
          },
        ])
      )
    ).toBe("In committee since January 3, 2025");
    expect(billStage(record([]))).toBe("");
  });
});

describe("new card references", () => {
  it("accepts only well-formed ids", () => {
    expect(parseRaceId("2026-SEN-GA")).toBe("2026-SEN-GA");
    expect(parseRaceId("2026-HOUSE-TX-12")).toBe("2026-HOUSE-TX-12");
    expect(parseRaceId("../admin")).toBeNull();
    expect(parseAgencySlug("environmental-protection-agency")).toBe(
      "environmental-protection-agency"
    );
    expect(parseAgencySlug("a/b")).toBeNull();
    expect(parseNominationRef("119/PN123-1")).toBe("119/PN123-1");
    expect(parseNominationRef("119/PN0")).toBeNull();
    expect(parseNominationRef("119/PN1/x")).toBeNull();
    expect(parseBillPageRef("119/HR.1")).toEqual({ congress: 119, id: "HR.1" });
    expect(parseBillPageRef("119/HR.1/x")).toBeNull();
  });
});

describe("parseCongressDate", () => {
  it("accepts an ISO date and nothing else", () => {
    expect(parseCongressDate("2026-09-29")).toBe("2026-09-29");
    expect(parseCongressDate("2026-9-29")).toBeNull();
    expect(parseCongressDate("../admin")).toBeNull();
    expect(parseCongressDate(null)).toBeNull();
  });
});

describe("chamberTile", () => {
  const day = (status: string, recordVotes: number) =>
    ({ status, counts: { recordVotes } }) as unknown as DayReport["chambers"]["senate"];
  const report = (senate: DayReport["chambers"]["senate"], house: DayReport["chambers"]["house"]) =>
    ({ chambers: { senate, house } }) as DayReport;

  it("shows a chamber's record votes when the day was recorded", () => {
    const r = report(day("final", 2), day("live", 0));
    expect(chamberTile(r, "senate")).toEqual({ value: "2", label: "Senate record votes" });
    expect(chamberTile(r, "house")).toEqual({ value: "0", label: "House record votes" });
  });

  // A day without a record must never read as "0 record votes".
  it("says why there is no count instead of showing zero", () => {
    const r = report(day("not_in_session", 0), day("no_record", 0));
    expect(chamberTile(r, "senate")).toEqual({ value: "—", label: "Senate: not in session" });
    expect(chamberTile(r, "house")).toEqual({ value: "—", label: "House: no record yet" });
  });
});

describe("record card references", () => {
  it("accepts a roll call by chamber, congress, session and number", () => {
    expect(parseVoteRef("senate/119/2/123")).toBe("senate/119/2/123");
    expect(parseVoteRef("house/119/1/7")).toBe("house/119/1/7");
    expect(parseVoteRef("scotus/119/1/7")).toBeNull();
    expect(parseVoteRef("senate/119/2")).toBeNull();
    expect(parseVoteRef("senate/119/2/12a")).toBeNull();
    expect(parseVoteRef("senate/../admin")).toBeNull();
    expect(parseVoteRef(null)).toBeNull();
  });

  it("accepts a bill as congress and type.number", () => {
    for (const ok of ["119/S.4668", "119/HR.22", "119/HJRES.5", "119/SRES.12"]) {
      expect(parseBillRef(ok)).toBe(ok);
    }
    expect(parseBillRef("119/HR22")).toBeNull();
    expect(parseBillRef("119/H.R.22")).toBeNull();
    expect(parseBillRef("S.4668")).toBeNull();
    expect(parseBillRef("119/S.4668/x")).toBeNull();
    expect(parseBillRef("")).toBeNull();
  });

  it("accepts only a numeric record id", () => {
    expect(parseRecordId("42")).toBe("42");
    expect(parseRecordId("4a")).toBeNull();
    expect(parseRecordId("../42")).toBeNull();
    expect(parseRecordId(null)).toBeNull();
  });

  it("maps each query parameter to the backend's card key", () => {
    const key = (q: string) => recordCardKey(new URLSearchParams(q));
    expect(key("vote=senate/119/2/123")).toBe("vote:senate/119/2/123");
    expect(key("bill=119/S.4668&title=0")).toBe("bill:119/S.4668");
    expect(key("congressEvent=9")).toBe("event:9");
    expect(key("explore=31")).toBe("document:31");
    expect(key("explore=x")).toBeNull();
    expect(key("issue=650")).toBeNull();
  });
});

describe("recordCardTexts", () => {
  // Built the way backend record_cards.RecordCard.alt builds it, so a
  // rendered string that isn't in it is something the alt text omits.
  const source = "Source: Senate.gov · civitas-research.org/votes/1 · data as of October 10, 2026";
  const card: RecordCard = {
    kind: "vote",
    key: "senate/119/2/123",
    eyebrow: "Senate roll call 123, 119th Congress",
    headline: "On Passage of the Bill",
    date: "2026-10-09",
    path: "/votes/1",
    source_name: "Senate.gov",
    as_of: "2026-10-10",
    lines: ["Passed"],
    tally: [
      {
        position: "yea",
        label: "Yea",
        total: 52,
        parties: [
          { party: "R", name: "Republican", count: 50 },
          { party: "D", name: "Democratic", count: 2 },
          { party: "I", name: "Independent", count: 0 },
        ],
      },
      {
        position: "nay",
        label: "Nay",
        total: 47,
        parties: [
          { party: "D", name: "Democratic", count: 45 },
          { party: "I", name: "Independent", count: 2 },
        ],
      },
      { position: "notVoting", label: "Not voting", total: 1, parties: [] },
    ],
    source,
    alt:
      "Senate roll call 123, 119th Congress, October 9, 2026. On Passage of the Bill. Passed. " +
      "Yea 52: Republican 50, Democratic 2. Nay 47: Democratic 45, Independent 2. " +
      `Not voting 1. ${source}.`,
  };
  const texts = recordCardTexts(card);

  it("renders the source line verbatim", () => {
    expect(texts).toContain(source);
  });

  it("renders every tally total and every counted party", () => {
    for (const row of card.tally) {
      expect(texts).toContain(row.label);
      expect(texts).toContain(String(row.total));
      for (const p of row.parties.filter((x) => x.count > 0)) {
        expect(texts).toContain(`${p.name} ${p.count}`);
      }
    }
  });

  it("renders the eyebrow, the record's date, the headline and each line", () => {
    expect(texts).toEqual(
      expect.arrayContaining([card.eyebrow, "October 9, 2026", card.headline, "Passed"])
    );
  });

  it("renders nothing the alt text does not say", () => {
    for (const text of texts) expect(card.alt).toContain(text);
    expect(texts.join(" ")).not.toContain("Independent 0");
  });

  it("leaves out a date the record does not have", () => {
    expect(recordCardTexts({ ...card, date: null, tally: [] })).toEqual([
      card.eyebrow,
      card.headline,
      "Passed",
      source,
    ]);
  });
});

describe("ballotTiles", () => {
  type Coverage = StateBallot["measureCoverage"];
  const coverage = (status: Coverage["status"]): Coverage => ({
    status,
    sourceName: null,
    checkedAt: null,
    lastAttemptAt: null,
  });
  const ballot = (over: Partial<StateBallot>): StateBallot =>
    ({
      cycleYear: 2026,
      senateRaces: [],
      houseRaces: [],
      measures: [],
      nextSenateElection: 2028,
      measureCoverage: coverage("covered"),
      ...over,
    }) as unknown as StateBallot;
  const race = {} as StateBallot["senateRaces"][number];
  const measure = (status: string) => ({ status }) as StateBallot["measures"][number];

  it("never shows a 0 for measures nobody has read", () => {
    for (const status of ["not_yet_covered", "ingest_failed"] as const) {
      const [, , m] = ballotTiles(ballot({ measureCoverage: coverage(status) }));
      expect(m).toEqual({ value: null, label: "Measures not yet covered" });
    }
  });

  it("says there are none only when the state's source said so", () => {
    const [, , m] = ballotTiles(ballot({ measureCoverage: coverage("confirmed_none") }));
    expect(m).toEqual({ value: null, label: "No statewide measures" });
  });

  it("counts measures on the ballot, not removed ones", () => {
    const [, , m] = ballotTiles(ballot({ measures: [measure("active"), measure("removed")] }));
    expect(m).toEqual({ value: "1", label: "Statewide ballot measure" });
    const [, , gone] = ballotTiles(ballot({ measures: [measure("withdrawn")] }));
    expect(gone).toEqual({ value: null, label: "No statewide measures remaining" });
  });

  it("words an empty Senate list as a sentence, and a seatless state as one", () => {
    const [s, h] = ballotTiles(ballot({ houseRaces: [race, race] }));
    expect(s).toEqual({ value: null, label: "No Senate race in 2026" });
    expect(h).toEqual({ value: "2", label: "U.S. House races" });
    expect(ballotTiles(ballot({ nextSenateElection: null }))[0].label).toBe("No U.S. Senate seat");
    expect(ballotTiles(ballot({ senateRaces: [race] }))[0]).toEqual({
      value: "1",
      label: "U.S. Senate race",
    });
  });

  it("never shows a 0 for House races", () => {
    expect(ballotTiles(ballot({}))[1]).toEqual({
      value: null,
      label: "House races not listed yet",
    });
  });
});
