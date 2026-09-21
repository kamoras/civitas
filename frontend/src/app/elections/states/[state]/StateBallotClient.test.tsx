import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StateBallotClient from "./StateBallotClient";
import type { RaceWithCandidates, StateBallot } from "@/types/election";

vi.mock("@/lib/api", () => ({
  fetchTownsForState: vi.fn().mockResolvedValue([]),
  fetchTownBallot: vi.fn(),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

// jsdom doesn't implement scrollIntoView — the app code's real, correct
// call to it (deep-linking/expand-to-district) just has nothing to call
// in this environment.
Element.prototype.scrollIntoView = vi.fn();
vi.mock("@/components/elections/CoverageFeed", async () => {
  const actual = await vi.importActual<typeof import("@/components/elections/CoverageFeed")>(
    "@/components/elections/CoverageFeed"
  );
  return { ...actual, default: () => <div /> };
});

function candidate(overrides: Partial<RaceWithCandidates["candidates"][number]>) {
  return {
    id: "c1",
    name: "Jane Doe",
    party: "DEM",
    incumbentChallenge: null as string | null,
    candidateStatus: "C" as string | null,
    hasRaisedFunds: true,
    contributions: null as number | null,
    cashOnHand: null as number | null,
    lastFinancialsSync: null as string | null,
    incumbentRecord: null,
    ...overrides,
  };
}

function houseRace(overrides: Partial<RaceWithCandidates> = {}): RaceWithCandidates {
  return {
    id: "2026-HOUSE-OH-1",
    cycleYear: 2026,
    office: "H",
    state: "OH",
    district: 1,
    isSpecial: false,
    pvi: -3,
    pviLevel: "district",
    candidateSource: "filers",
    counties: ["Hamilton County (part)"],
    candidates: [
      candidate({ id: "dem1", name: "Greg Landsman", party: "DEM", incumbentChallenge: "I", cashOnHand: 3_610_213 }),
      candidate({ id: "rep1", name: "Eric Conroy", party: "REP", cashOnHand: 474_156 }),
    ],
    ...overrides,
  };
}

function ballot(overrides: Partial<StateBallot> = {}): StateBallot {
  return {
    state: "OH",
    cycleYear: 2026,
    electionDate: "2026-11-03",
    electionType: "general",
    primaryDate: null,
    statePvi: 6,
    senateRaces: [],
    nextSenateElection: null,
    houseRaces: [houseRace()],
    coverage: [],
    measures: [],
    measureCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
    statewideRaces: [],
    statewideCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
    stateLegRaces: [],
    officialLookup: {
      url: "https://www.usa.gov/election-office",
      label: "Find your election office",
      sourceName: "USA.gov",
      isStateSpecific: false,
      verifiedAt: null,
    },
    omits: [],
    ...overrides,
  };
}

describe("StateBallotClient — House section", () => {
  it("shows every district's leading matchup without any selection", () => {
    render(<StateBallotClient ballot={ballot()} />);

    // The whole point: no dropdown, no address entry needed to see this.
    expect(screen.getByText("Greg Landsman (I)")).toBeInTheDocument();
    expect(screen.getByText("Eric Conroy")).toBeInTheDocument();
    expect(screen.queryByLabelText("Select your district")).not.toBeInTheDocument();
  });

  it("expands a district's full detail on click", async () => {
    render(<StateBallotClient ballot={ballot()} />);

    expect(screen.queryByText(/News coverage of this race is tagged/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Greg Landsman (I)"));
    expect(screen.getByText(/News coverage of this race is tagged/)).toBeInTheDocument();
  });

  it("collapses a district again on a second click", async () => {
    render(<StateBallotClient ballot={ballot()} />);

    const row = screen.getByText("Greg Landsman (I)");
    await userEvent.click(row);
    expect(screen.getByText(/News coverage of this race is tagged/)).toBeInTheDocument();
    await userEvent.click(row);
    expect(screen.queryByText(/News coverage of this race is tagged/)).not.toBeInTheDocument();
  });

  it("can still be closed after opening via a #race-{id} deep link", async () => {
    // The bug this guards against: openId started as `null`, the same
    // value used to mean "nothing chosen yet" (falling back to the URL
    // hash). Clicking a hash-opened row to close it set openId to null
    // again -- a no-op, since it was already null -- so the row could
    // never be closed once a deep link opened it.
    window.location.hash = "#race-2026-HOUSE-OH-1";
    render(<StateBallotClient ballot={ballot()} />);

    expect(screen.getByText(/News coverage of this race is tagged/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Greg Landsman (I)"));
    expect(screen.queryByText(/News coverage of this race is tagged/)).not.toBeInTheDocument();

    window.location.hash = "";
  });

  it("flags a district's PVI as statewide when no district-level crosswalk data exists", () => {
    render(<StateBallotClient ballot={ballot({ houseRaces: [houseRace({ pviLevel: "state" })] })} />);
    expect(screen.getByText("(statewide)")).toBeInTheDocument();
  });

  it("does not flag PVI as statewide when real district-level data exists", () => {
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByText("(statewide)")).not.toBeInTheDocument();
  });

  it("shows the district's full county list once expanded, not just the truncated preview", async () => {
    render(
      <StateBallotClient
        ballot={ballot({
          houseRaces: [
            houseRace({ counties: ["Hamilton County (part)", "Butler County", "Warren County", "Clermont County", "Clinton County"] }),
          ],
        })}
      />
    );
    expect(screen.queryByText(/Covers:/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Greg Landsman (I)"));
    expect(
      screen.getByText("Covers: Hamilton County (part), Butler County, Warren County, Clermont County, Clinton County")
    ).toBeInTheDocument();
  });

  it("shows multiple districts, each independently collapsed", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          houseRaces: [
            houseRace({ id: "d1", district: 1 }),
            houseRace({
              id: "d2",
              district: 2,
              candidates: [
                candidate({ id: "dem2", name: "Second District Dem", party: "DEM", cashOnHand: 1000 }),
                candidate({ id: "rep2", name: "Second District Rep", party: "REP", cashOnHand: 900 }),
              ],
            }),
          ],
        })}
      />
    );

    expect(screen.getByText("Greg Landsman (I)")).toBeInTheDocument();
    expect(screen.getByText("Second District Dem")).toBeInTheDocument();
    expect(screen.getByText("U.S. HOUSE — 2 DISTRICTS")).toBeInTheDocument();
  });
});

describe("statewide executive offices", () => {
  const covered = {
    statewideCoverage: {
      status: "covered" as const,
      sourceName: "Rhode Island Board of Elections",
      checkedAt: "2026-09-21T04:08:00.625851Z",
    },
    statewideRaces: [
      {
        office: "governor",
        label: "Governor",
        nominees: [
          { party: "DEM", name: "Helena Buonanno Foulkes" },
          { party: "REP", name: "Aaron C. Guckian" },
        ],
      },
      {
        office: "secretary_of_state",
        label: "Secretary of State",
        nominees: [{ party: "DEM", name: "Gregg M. Amore" }],
      },
    ],
  };

  it("renders each office with its nominees", () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    expect(screen.getByText("STATEWIDE EXECUTIVE OFFICES")).toBeInTheDocument();
    expect(screen.getByText("Governor")).toBeInTheDocument();
    expect(screen.getByText("Helena Buonanno Foulkes")).toBeInTheDocument();
    expect(screen.getByText("Aaron C. Guckian")).toBeInTheDocument();
    expect(screen.getByText("Secretary of State")).toBeInTheDocument();
  });

  it("names the source and the date it was checked", () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    expect(
      screen.getByText(/Rhode Island Board of Elections · last checked 2026-09-21/)
    ).toBeInTheDocument();
  });

  it("says the list is what the state published, not every office", () => {
    // An office whose primary nobody contested is often not itemised in
    // a results feed at all — Arkansas publishes two of its seven that
    // way — so the section must not read as exhaustive.
    render(<StateBallotClient ballot={ballot(covered)} />);
    expect(
      screen.getByText(/Only offices named in the state's own results feed appear/)
    ).toBeInTheDocument();
  });

  it("says plainly that no money or score exists for these offices", () => {
    // They have no FEC filing. Without this line the absence of the
    // funding bars every federal race on the page shows reads as missing
    // data rather than as a fact about the office.
    render(<StateBallotClient ballot={ballot(covered)} />);
    expect(screen.getByText(/no federal campaign-finance filings/)).toBeInTheDocument();
  });

  it("states a confirmed absence in words rather than showing nothing", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          statewideRaces: [],
          statewideCoverage: {
            status: "confirmed_none",
            sourceName: "Ohio Secretary of State",
            checkedAt: "2026-09-21T04:08:00Z",
          },
        })}
      />
    );
    expect(
      screen.getByText(/No statewide executive offices are on OH's 2026-11-03 ballot/)
    ).toBeInTheDocument();
    // ... without explaining the funding data missing from names that
    // aren't there.
    expect(screen.queryByText(/no federal campaign-finance filings/)).not.toBeInTheDocument();
  });

  it("renders no section at all for a state nobody has checked", () => {
    // The page's own `omits` list already names these contests as out of
    // scope. An empty panel would repeat that admission and, worse, read
    // as a state that elects nobody.
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByText("STATEWIDE EXECUTIVE OFFICES")).not.toBeInTheDocument();
  });

  it("colours nominees by the same party codes federal candidates use", () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    expect(screen.getByText("Helena Buonanno Foulkes").className).toContain("text-dem-blue");
    expect(screen.getByText("Aaron C. Guckian").className).toContain("text-rep-red");
  });

  it("names each nominee's party in text, not only in colour", () => {
    // WCAG 1.4.1. These rows can hold a single unopposed nominee, so
    // unlike a federal row there is no opposing colour to read the party
    // against — Secretary of State here has exactly one.
    render(<StateBallotClient ballot={ballot(covered)} />);
    const sos = screen.getByText("Gregg M. Amore").closest("span")!.parentElement!;
    expect(sos.textContent).toContain("DEM");
  });

  it("renders an independent nominee without forcing them into a major party", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          statewideCoverage: covered.statewideCoverage,
          statewideRaces: [
            {
              office: "governor",
              label: "Governor",
              nominees: [{ party: "IND", name: "Someone Unaffiliated" }],
            },
          ],
        })}
      />
    );
    const el = screen.getByText("Someone Unaffiliated");
    expect(el.className).not.toContain("text-dem-blue");
    expect(el.className).not.toContain("text-rep-red");
  });
});

describe("state legislature", () => {
  const legislature = {
    stateLegRaces: [
      {
        chamber: "upper",
        label: "State Senate",
        districts: [
          {
            district: "5",
            towns: ["Providence city"],
            nominees: [{ party: "DEM", name: "Samuel W. Bell" }],
          },
        ],
      },
      {
        chamber: "lower",
        label: "State House",
        districts: [
          { district: "9", towns: ["Cranston city"], nominees: [{ party: "DEM", name: "Nine Dem" }] },
          { district: "13", towns: ["Foster town", "Glocester town"], nominees: [{ party: "REP", name: "Derick A. Reels" }] },
          { district: "74", towns: ["Jamestown town"], nominees: [{ party: "DEM", name: "Island Dem" }] },
          { district: "75", towns: ["Newport city"], nominees: [{ party: "REP", name: "Newport Rep" }] },
        ],
      },
    ],
  };

  it("renders both chambers with their contested seat counts", () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    expect(screen.getByText("STATE SENATE — 1 SEAT CONTESTED")).toBeInTheDocument();
    expect(screen.getByText("STATE HOUSE — 4 SEATS CONTESTED")).toBeInTheDocument();
  });

  it("shows each seat's towns so a reader can find it without an address", () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    expect(screen.getByText("Foster town, Glocester town")).toBeInTheDocument();
    expect(screen.getByText("Derick A. Reels")).toBeInTheDocument();
  });

  it("offers no filter for a chamber short enough to read whole", () => {
    // The Senate here has one seat; a filter box above it is chrome.
    render(<StateBallotClient ballot={ballot(legislature)} />);
    expect(
      screen.queryByLabelText(/Filter State Senate seats/)
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Filter State House seats/)).toBeInTheDocument();
  });

  it("filters a chamber by town", async () => {
    const user = userEvent.setup();
    render(<StateBallotClient ballot={ballot(legislature)} />);
    await user.type(screen.getByLabelText(/Filter State House seats/), "jamestown");
    expect(screen.getByText("Island Dem")).toBeInTheDocument();
    expect(screen.queryByText("Derick A. Reels")).not.toBeInTheDocument();
    expect(screen.queryByText("Newport Rep")).not.toBeInTheDocument();
  });

  it("filters by an exact district identifier rather than a substring", async () => {
    // "9" must not also bring back 75 or 13. Districts are strings —
    // Minnesota's are "10A"/"10B" — so this is a string comparison, not
    // a numeric one.
    const user = userEvent.setup();
    render(<StateBallotClient ballot={ballot(legislature)} />);
    await user.type(screen.getByLabelText(/Filter State House seats/), "9");
    expect(screen.getByText("Nine Dem")).toBeInTheDocument();
    expect(screen.queryByText("Newport Rep")).not.toBeInTheDocument();
  });

  it("explains an empty filter result instead of showing a blank chamber", async () => {
    const user = userEvent.setup();
    render(<StateBallotClient ballot={ballot(legislature)} />);
    await user.type(screen.getByLabelText(/Filter State House seats/), "zzzz");
    expect(screen.getByText(/No State House seat matches/)).toBeInTheDocument();
  });

  it("truncates a long town list but still filters on the hidden names", async () => {
    // A rural Minnesota senate district covers 292 townships. Rendering
    // them all buries the row; dropping them loses the only way a reader
    // in the 290th can find their seat.
    const many = Array.from({ length: 40 }, (_, i) => `Township ${i + 1}`);
    const user = userEvent.setup();
    render(
      <StateBallotClient
        ballot={ballot({
          stateLegRaces: [
            {
              chamber: "lower",
              label: "State House",
              districts: [
                { district: "1", towns: many, nominees: [{ party: "DEM", name: "Rural Rep" }] },
                { district: "2", towns: ["Elsewhere city"], nominees: [{ party: "REP", name: "Other Rep" }] },
                { district: "3", towns: ["Third city"], nominees: [{ party: "REP", name: "Third Rep" }] },
                { district: "4", towns: ["Fourth city"], nominees: [{ party: "REP", name: "Fourth Rep" }] },
              ],
            },
          ],
        })}
      />
    );
    // Shown: a few names and a count, not all forty.
    expect(screen.getByText(/& 37 more/)).toBeInTheDocument();
    expect(screen.queryByText(/Township 40/)).not.toBeInTheDocument();
    // Hidden, but still findable.
    await user.type(screen.getByLabelText(/Filter State House seats/), "Township 40");
    expect(screen.getByText("Rural Rep")).toBeInTheDocument();
    expect(screen.queryByText("Other Rep")).not.toBeInTheDocument();
  });

  it("renders no section at all for a state whose seats are not covered", () => {
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByText(/SEATS CONTESTED/)).not.toBeInTheDocument();
    expect(screen.queryByText(/STATE SENATE/)).not.toBeInTheDocument();
  });

  it("colours nominees with the same party codes as every other race", () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    expect(screen.getByText("Samuel W. Bell").className).toContain("text-dem-blue");
    expect(screen.getByText("Derick A. Reels").className).toContain("text-rep-red");
  });

  it("names each seat's party in text as well as colour", () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const row = screen.getByText("Derick A. Reels").closest("span")!.parentElement!;
    expect(row.textContent).toContain("REP");
  });
});
