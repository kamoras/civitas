import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { HouseResultRow, RaceResultCard, statusTag } from "./RaceResult";
import type { LiveRaceResult } from "@/types/election";

afterEach(cleanup);

function race(overrides: Partial<LiveRaceResult> = {}): LiveRaceResult {
  return {
    raceId: "2026-HOUSE-GA-2",
    state: "GA",
    office: "H",
    district: 2,
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
    flip: false,
    candidates: [
      { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
    ],
    ...overrides,
  };
}

const TIE = race({
  leaderParty: null,
  candidates: [
    { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
    { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
  ],
});

describe("statusTag", () => {
  it("tags an exact tie as tied, not leading", () => {
    expect(statusTag(TIE).text).toBe("Tied · Not final");
    expect(statusTag(race()).text).toBe("Leading · Not final");
    expect(statusTag(race({ votesCounted: 0 })).text).toBe("No votes yet");
    expect(statusTag({ ...TIE, official: true }).text).toBe("Tied · Official count");
  });

  // The card's "The count is not final." sits under every candidate, more
  // than a screen below the tag on a phone: the tag says it itself.
  it("says not final in every leading, early or tied tag until the count is official", () => {
    const early = race({ reportingUnits: 10, totalUnits: 100 });
    for (const r of [race(), race({ flip: true }), TIE, early]) {
      expect(statusTag(r).text).toMatch(/ · Not final$/);
      expect(statusTag({ ...r, official: true }).text).not.toMatch(/Not final/);
    }
    expect(statusTag(early).text).toBe("Early · Not final");
  });

  it("never puts a bare OFFICIAL beside an official count's leader, and keeps its flip", () => {
    expect(statusTag(race({ official: true })).text).toBe("Leads · Official count");
    expect(statusTag(race({ official: true, flip: true })).text).toBe("Flip · Official count");
    expect(statusTag(race({ flip: true })).text).toBe("Flip · Leading · Not final");
    for (const r of [race({ official: true }), race({ official: true, flip: true }), TIE])
      expect(statusTag({ ...r, official: true }).text).not.toBe("Official");
  });

  it("never passes a count the state's latest read skipped off as live", () => {
    expect(statusTag(race({ inLatestRead: false })).text).toBe("Not in latest read");
    expect(statusTag(race({ inLatestRead: false, flip: true })).text).toBe("Not in latest read");
    expect(statusTag(race({ inLatestRead: true })).text).toBe("Leading · Not final");
    render(<RaceResultCard result={race({ inLatestRead: false })} />);
    expect(
      screen.getByText(/The state's latest read didn't include this race/)
    ).toBeInTheDocument();
  });
});

describe("HouseResultRow", () => {
  it("names both tied candidates without colouring either as ahead", () => {
    render(
      <ol>
        <HouseResultRow result={TIE} />
      </ol>
    );
    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("Tied: Ray Jones (R) 50.0% · Dana Smith (D) 50.0%");
    expect(row.querySelector(".text-rep-red")).toBeNull();
    expect(row).toHaveTextContent("Tied");
  });

  // Clear of the fixed header when a #race- link lands on it: globals.css's
  // scroll-padding-top, for every target at once (layoutStability.test.ts).
  it("can take focus when a #race- link lands on it", () => {
    render(
      <ol>
        <HouseResultRow result={race()} />
      </ol>
    );
    const row = screen.getByRole("listitem");
    expect(row).toHaveAttribute("tabindex", "-1");
  });
});

describe("RaceResultCard", () => {
  it("wraps a long name rather than cutting it short", () => {
    render(<RaceResultCard result={race({ office: "S", district: null })} />);
    const name = screen.getByText("Ray Jones");
    expect(name.className).not.toMatch(/truncate/);
    expect(name.className).toContain("break-words");
  });

  it("says a tie is tied, not led", () => {
    render(<RaceResultCard result={TIE} />);
    expect(screen.getByText(/Tied, not called/)).toBeInTheDocument();
    expect(screen.queryByText(/Leading, not called/)).not.toBeInTheDocument();
  });

  it("says an official count's leader still only leads, and keeps who held the seat", () => {
    render(<RaceResultCard result={race({ official: true, flip: true, heldBy: "DEM" })} />);
    expect(screen.getByText("Flip · Official count")).toBeInTheDocument();
    expect(document.body).toHaveTextContent(
      "Leads in the count the state lists as official; not called. The seat was held by a Democrat; the leader is from another party."
    );
    expect(document.body).not.toHaveTextContent(/The state lists this count as official\./);
    cleanup();
    render(<RaceResultCard result={race({ official: true })} />);
    expect(screen.getByText("Leads · Official count")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/held by a/);
  });

  it("keeps an announced flip through a held poll without saying the holder's lead is another party's", () => {
    // A poll whose total fell announces nothing: the flip stands (as on the
    // Action Center issue and in the feed) while its figures show the
    // holder ahead. The page never calls that leader another party's.
    const held = race({
      flip: true,
      leaderParty: "DEM",
      candidates: [
        { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
        { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
      ],
    });
    render(<RaceResultCard result={held} />);
    // Both things that are true: announced, and the holder ahead here.
    expect(
      screen.getByText("Flip announced · Holder's party leads · Not final")
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Flip · Leading/)).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/another party|changing party/);
    expect(document.body).toHaveTextContent(
      "The seat was held by a Democrat; a change of party was announced earlier (holder's party ahead in the latest count)."
    );
  });

  it("tags a held flip by what its count shows: a tie, or an official count", () => {
    const tiedCands = [
      { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
      { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
    ];
    expect(statusTag(race({ flip: true, leaderParty: null, candidates: tiedCands })).text).toBe(
      "Flip announced · Tied · Not final"
    );
    const heldCands = [
      { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
    ];
    expect(
      statusTag(race({ flip: true, official: true, leaderParty: "DEM", candidates: heldCands }))
        .text
    ).toBe("Flip announced · Holder's party leads · Official count");
    // A flip the count still bears out keeps its FLIP.
    expect(statusTag(race({ flip: true })).text).toBe("Flip · Leading · Not final");
  });

  it("tags a held flip on a House row by its standing, never FLIP · LEADING", () => {
    render(
      <HouseResultRow
        result={race({
          flip: true,
          leaderParty: "DEM",
          candidates: [
            { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
            { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
          ],
        })}
      />
    );
    expect(document.body).toHaveTextContent("Flip announced · Holder's party leads");
    expect(document.body).not.toHaveTextContent(/another party|flip · Leading/);
  });

  it("says a House seat on new district lines has no previous holder", () => {
    render(<RaceResultCard result={race({ state: "TX", heldBy: null })} newLines />);
    expect(screen.getByText(/new district lines · no previous holder/)).toBeInTheDocument();
    expect(screen.queryByText(/held by/)).not.toBeInTheDocument();
  });

  it("says nothing of new lines for a seat with a holder, or a Senate race", () => {
    render(<RaceResultCard result={race({ heldBy: "DEM" })} newLines />);
    expect(screen.getByText(/held by D/)).toBeInTheDocument();
    cleanup();
    render(
      <RaceResultCard result={race({ office: "S", district: null, heldBy: null })} newLines />
    );
    expect(screen.queryByText(/no previous holder/)).not.toBeInTheDocument();
    cleanup();
    // Unknown holder in a state whose lines did not change: no claim either way.
    render(<RaceResultCard result={race({ heldBy: null })} />);
    expect(screen.queryByText(/no previous holder|held by/)).not.toBeInTheDocument();
  });
});

describe("a flip announced earlier that the latest count doesn't show", () => {
  it("is said before 'no votes yet' when the held poll has none, on the card and the row", () => {
    // The counter lists it as "not counted" and points at the card: the
    // card must then say why, not only "no votes yet".
    const empty = race({
      office: "S",
      district: null,
      flip: true,
      votesCounted: 0,
      leaderParty: null,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 0, pct: null, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 0, pct: null, candidateId: null },
      ],
    });
    expect(statusTag(empty).text).toBe("Flip announced · No votes in this count");
    render(<RaceResultCard result={empty} />);
    expect(document.body).toHaveTextContent(
      "No votes in the latest count. The seat was held by a Democrat; a change of party was announced earlier."
    );
    expect(document.body).not.toHaveTextContent(/No votes counted yet|no votes yet/);
    cleanup();
    render(
      <ol>
        <HouseResultRow result={{ ...empty, office: "H", district: 2 }} />
      </ol>
    );
    expect(document.body).toHaveTextContent("No votes in the latest count");
    expect(document.body).toHaveTextContent("Flip announced · No votes in this count");
  });

  it("says a leader with no party given is that, not 'another party'", () => {
    const noParty = race({
      flip: true,
      leaderParty: null,
      candidates: [
        { name: "Indy Pen", party: null, votes: 1000, pct: 52.6, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
      ],
    });
    expect(statusTag(noParty).text).toBe("Flip announced · Leader's party not given · Not final");
    render(<RaceResultCard result={noParty} />);
    expect(document.body).toHaveTextContent(
      "a change of party was announced earlier (leader's party not given in the latest count)."
    );
    expect(document.body).not.toHaveTextContent(/another party|not in this count/);
    // Its party slot says the same, never "OTHER" beside that wording.
    expect(document.body).toHaveTextContent("Party not given");
    expect(document.body).not.toHaveTextContent(/\bOTHER\b/i);
  });

  it("says a House row's leader, or a tied candidate, with no party given as that", () => {
    const pen = { name: "Indy Pen", party: null, votes: 1000, pct: 52.6, candidateId: null };
    render(
      <HouseResultRow
        result={race({
          leaderParty: null,
          candidates: [pen, { ...pen, name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4 }],
        })}
      />
    );
    expect(document.body).toHaveTextContent("Indy Pen (party not given) 52.6%");
    cleanup();
    render(
      <HouseResultRow
        result={race({ leaderParty: null, candidates: [pen, { ...pen, name: "Dana Smith" }] })}
      />
    );
    expect(document.body).toHaveTextContent(
      "Indy Pen (party not given) 52.6% · Dana Smith (party not given) 52.6%"
    );
    expect(document.body).not.toHaveTextContent(/\(other\)/);
  });
});

describe("a House row on a phone", () => {
  it("puts the status tag under the leader, not in a column beside the name", () => {
    render(
      <ol>
        <HouseResultRow result={race({ flip: true })} />
      </ol>
    );
    const li = document.querySelector("li")!;
    // Two columns below `sm` (label, the rest); four from `sm`.
    expect(li.className).toMatch(/(^|\s)grid-cols-\[3\.75rem_minmax\(0,1fr\)\](\s|$)/);
    expect(li.className).toContain("sm:grid-cols-[4.5rem_minmax(0,1fr)_12rem_8rem]");
    const tag = screen.getByText("Flip · Leading · Not final");
    expect(tag.className).toContain("col-start-2");
    expect(tag.className).toContain("sm:col-start-auto");
    // The leader wraps at every width, never cut short: from `sm` the
    // state page's side column leaves this cell under 100px at 1024px, and
    // a truncated name took the party and share with it.
    const name = screen.getByText("Ray Jones").parentElement!;
    expect(name.className).not.toMatch(/truncate/);
    expect(name).toHaveTextContent(/Ray Jones \(\w+\) \d+\.\d%/);
  });
});

describe("the count's own words", () => {
  it("says no votes are counted yet on a card with none, not 'leading'", () => {
    render(<RaceResultCard result={race({ office: "S", district: null, votesCounted: 0 })} />);
    expect(screen.getByText("No votes yet")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("No votes counted yet.");
    expect(document.body).not.toHaveTextContent(/Leading, not called/);
  });

  it("gives a House row its reporting figure on a phone too", () => {
    render(
      <ol>
        <HouseResultRow result={race()} />
      </ol>
    );
    // One copy for phones (sm:hidden), one for wider screens (hidden
    // sm:block): neither breakpoint drops it.
    const shown = screen.getAllByText("80 of 100 precincts reporting (80%)");
    expect(shown.map((el) => el.className)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("sm:hidden"),
        expect.stringContaining("sm:block"),
      ])
    );
    expect(shown.find((el) => el.className.includes("sm:hidden"))!.className).not.toMatch(
      /(^|\s)hidden(\s|$)/
    );
  });
});
