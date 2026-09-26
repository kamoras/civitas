import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
vi.mock("@/components/elections/DistrictMap", () => ({ default: () => null }));

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
    confirmed: true,
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
    ballotBasis: {
      basis: "filers",
      primaryPassed: null,
      daysSincePrimary: null,
      supersededByPrimary: false,
    },
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
    judicialRaces: [],
    judicialCoverage: { status: "not_yet_covered", checkedAt: null, sourceName: null },
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

/** Opens a contest the way a phone reader does — from the index of
 * contests — and returns the drawer to query inside. The desktop columns
 * render too (CSS picks one), so every assertion is scoped to the dialog. */
async function openContest(name: RegExp) {
  const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
  await userEvent.click(within(index).getByRole("button", { name }));
  return within(screen.getByRole("dialog"));
}

describe("the ballot page", () => {
  it("lays the ballot out as contests, federal first, with no drawer open", () => {
    render(<StateBallotClient ballot={ballot()} />);
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    const titles = within(index).getAllByRole("button").map((b) => b.textContent);
    expect(titles[0]).toContain("U.S. Representative");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("names federal offices one way everywhere", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          senateRaces: [houseRace({ id: "2026-SEN-OH", office: "S", district: null, counties: null })],
        })}
      />,
    );
    expect(screen.getAllByText("U.S. Senator").length).toBeGreaterThan(0);
    expect(screen.getAllByText("U.S. Representative").length).toBeGreaterThan(0);
    expect(screen.queryByText(/United States Senator/)).not.toBeInTheDocument();
  });

  it("counts the ballot's federal candidates, third parties and voting records in the header", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          houseRaces: [
            houseRace({
              candidates: [
                candidate({ id: "d", name: "A Dem", party: "DEM", incumbentRecord: { id: "L000001", score: 71.2 } }),
                candidate({ id: "r", name: "A Rep", party: "REP" }),
                candidate({ id: "l", name: "A Lib", party: "LIB" }),
              ],
            }),
          ],
        })}
      />,
    );
    expect(
      screen.getByText(/3 federal candidates, 1 outside the two major parties · 1 with a congressional voting record/),
    ).toBeInTheDocument();
  });

  it("shows a candidate the state certified even with no FEC activity", () => {
    // North Carolina's Libertarian Senate nominee: on the certified
    // ballot, no funds, not a statutory candidate — once filed away under
    // "other filers".
    render(
      <StateBallotClient
        ballot={ballot({
          houseRaces: [
            houseRace({
              candidateSource: "confirmed",
              candidates: [
                candidate({ id: "d", name: "A Dem", party: "DEM" }),
                candidate({
                  id: "l", name: "Shannon Bray", party: "LIB", confirmed: true,
                  candidateStatus: "N", hasRaisedFunds: false,
                }),
              ],
            }),
          ],
        })}
      />,
    );
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText("Shannon Bray")).toBeInTheDocument();
  });

  it("keeps the list of what this page does not cover in view", () => {
    render(<StateBallotClient ballot={ballot({ omits: ["County and municipal offices"] })} />);
    expect(screen.getAllByText("County and municipal offices").length).toBeGreaterThan(0);
  });
});

describe("the contest drawer", () => {
  const twoContests = () =>
    ballot({
      senateRaces: [houseRace({ id: "2026-SEN-OH", office: "S", district: null, counties: null })],
    });

  it("opens a contest's research and closes with Escape", async () => {
    render(<StateBallotClient ballot={twoContests()} />);
    const drawer = await openContest(/U\.S\. Senator/);
    expect(drawer.getByRole("heading", { name: "U.S. Senator" })).toBeInTheDocument();
    expect(drawer.getByRole("tab", { name: "Money" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("walks the ballot in order with Next and Previous", async () => {
    render(<StateBallotClient ballot={twoContests()} />);
    const drawer = await openContest(/U\.S\. Senator/);
    expect(drawer.getByText(/CONTEST 1 OF/)).toBeInTheDocument();
    await userEvent.click(drawer.getByRole("button", { name: "U.S. Representative →" }));
    expect(within(screen.getByRole("dialog")).getByRole("heading", { name: "U.S. Representative" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "← U.S. Senator" }));
    expect(within(screen.getByRole("dialog")).getByRole("heading", { name: "U.S. Senator" })).toBeInTheDocument();
  });

  it("switches a race's research with the arrow keys", async () => {
    render(<StateBallotClient ballot={twoContests()} />);
    const drawer = await openContest(/U\.S\. Senator/);
    drawer.getByRole("tab", { name: "Money" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(drawer.getByRole("tab", { name: "Record" })).toHaveAttribute("aria-selected", "true");
    expect(drawer.getAllByText("no scorecard")).toHaveLength(2);
  });

  it("opens the race a #race-{id} link names, and can still be closed", async () => {
    // The bug this guards against: "nothing chosen yet" (defer to the
    // URL) and "closed" were once the same value, so a contest a link had
    // opened could never be closed.
    window.location.hash = "#race-2026-HOUSE-OH-1";
    render(<StateBallotClient ballot={ballot()} />);
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByRole("heading", { name: "U.S. Representative" })).toBeInTheDocument();
    await userEvent.click(drawer.getByRole("button", { name: "ALL CONTESTS" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    window.location.hash = "";
  });
});

describe("U.S. Representative", () => {
  const twoDistricts = () =>
    ballot({
      houseRaces: [
        houseRace({ id: "d1", district: 1 }),
        houseRace({
          id: "d2",
          district: 2,
          counties: ["Hamilton County (part)", "Butler County", "Warren County", "Clermont County", "Clinton County"],
          candidates: [
            candidate({ id: "dem2", name: "Second District Dem", party: "DEM", cashOnHand: 1000 }),
            candidate({ id: "rep2", name: "Second District Rep", party: "REP", cashOnHand: 900 }),
          ],
        }),
      ],
    });

  it("offers every district from the ballot itself, no address needed", async () => {
    render(<StateBallotClient ballot={twoDistricts()} />);
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByRole("button", { name: "District 1" })).toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "District 2" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Select your district")).not.toBeInTheDocument();
  });

  it("lists each district's leading matchup in the picker", async () => {
    render(<StateBallotClient ballot={twoDistricts()} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.getByText("Greg Landsman (I)")).toBeInTheDocument();
    expect(drawer.getByText("Second District Dem")).toBeInTheDocument();
  });

  it("opens a picked district's research with its full county list", async () => {
    render(<StateBallotClient ballot={twoDistricts()} />);
    const box = screen.getByTestId("ballot-columns");
    await userEvent.click(within(box).getByRole("button", { name: "District 2" }));
    const drawer = within(screen.getByRole("dialog"));
    expect(
      drawer.getByText("Covers: Hamilton County (part), Butler County, Warren County, Clermont County, Clinton County"),
    ).toBeInTheDocument();
    expect(window.location.hash).toBe("#race-d2");
    await userEvent.click(drawer.getByRole("button", { name: "← PICK ANOTHER DISTRICT" }));
    expect(within(screen.getByRole("dialog")).getByText("Greg Landsman (I)")).toBeInTheDocument();
    window.location.hash = "";
  });

  it("flags a district's PVI as statewide when no district-level crosswalk data exists", async () => {
    render(<StateBallotClient ballot={ballot({ houseRaces: [houseRace({ pviLevel: "state" })] })} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.getByText("(statewide)")).toBeInTheDocument();
  });

  it("does not flag PVI as statewide when real district-level data exists", async () => {
    render(<StateBallotClient ballot={ballot()} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.queryByText("(statewide)")).not.toBeInTheDocument();
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

  it("renders each office with its nominees", async () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    const drawer = await openContest(/Statewide offices/);
    expect(drawer.getByText("Governor")).toBeInTheDocument();
    expect(drawer.getByText("Helena Buonanno Foulkes")).toBeInTheDocument();
    expect(drawer.getByText("Aaron C. Guckian")).toBeInTheDocument();
    expect(drawer.getByText("Secretary of State")).toBeInTheDocument();
  });

  it("names the source and the date it was checked", async () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    const drawer = await openContest(/Statewide offices/);
    expect(drawer.getByText(/Rhode Island Board of Elections · last checked 2026-09-21/)).toBeInTheDocument();
  });

  it("says the list is what the state published, not every office", async () => {
    // An office whose primary nobody contested is often not itemised in
    // a results feed at all — Arkansas publishes two of its seven that
    // way — so the list must not read as exhaustive.
    render(<StateBallotClient ballot={ballot(covered)} />);
    const drawer = await openContest(/Statewide offices/);
    expect(drawer.getByText(/Only offices named in the state's own results feed appear/)).toBeInTheDocument();
  });

  it("says plainly that no money or score exists for these offices", async () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    const drawer = await openContest(/Statewide offices/);
    expect(drawer.getByText(/no federal campaign-finance filings/)).toBeInTheDocument();
  });

  it("states a confirmed absence in words rather than showing nothing", async () => {
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
      />,
    );
    const drawer = await openContest(/Statewide offices/);
    expect(drawer.getByText(/No statewide executive offices are on OH's 2026-11-03 ballot/)).toBeInTheDocument();
    expect(drawer.queryByText(/no federal campaign-finance filings/)).not.toBeInTheDocument();
  });

  it("is not a contest at all for a state nobody has checked", () => {
    // The page's "not on this page" list already names these contests as
    // out of scope. An empty contest would repeat that and read as a
    // state that elects nobody.
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByText("Statewide offices")).not.toBeInTheDocument();
  });

  it("colours nominees by the same party codes federal candidates use", async () => {
    render(<StateBallotClient ballot={ballot(covered)} />);
    const drawer = await openContest(/Statewide offices/);
    expect(drawer.getByText("Helena Buonanno Foulkes").className).toContain("text-dem-blue");
    expect(drawer.getByText("Aaron C. Guckian").className).toContain("text-rep-red");
  });

  it("names each nominee's party in text, not only in colour", async () => {
    // WCAG 1.4.1: a single unopposed nominee has no opposing colour to be
    // read against.
    render(<StateBallotClient ballot={ballot(covered)} />);
    const drawer = await openContest(/Statewide offices/);
    const sos = drawer.getByText("Gregg M. Amore").closest("span")!.parentElement!;
    expect(sos.textContent).toContain("DEM");
  });

  it("renders an independent nominee without forcing them into a major party", async () => {
    render(
      <StateBallotClient
        ballot={ballot({
          statewideCoverage: covered.statewideCoverage,
          statewideRaces: [
            { office: "governor", label: "Governor", nominees: [{ party: "IND", name: "Someone Unaffiliated" }] },
          ],
        })}
      />,
    );
    const drawer = await openContest(/Statewide offices/);
    const el = drawer.getByText("Someone Unaffiliated");
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
          { district: "5", towns: ["Providence city"], nominees: [{ party: "DEM", name: "Samuel W. Bell" }] },
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
  const open = () => openContest(/State Senate · State House/);

  it("renders both chambers with their contested seat counts", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    expect(drawer.getByText("STATE SENATE — 1 SEAT CONTESTED")).toBeInTheDocument();
    expect(drawer.getByText("STATE HOUSE — 4 SEATS CONTESTED")).toBeInTheDocument();
  });

  it("shows each seat's towns so a reader can find it without an address", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    expect(drawer.getByText("Foster town, Glocester town")).toBeInTheDocument();
    expect(drawer.getByText("Derick A. Reels")).toBeInTheDocument();
  });

  it("offers no filter for a chamber short enough to read whole", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    expect(drawer.queryByLabelText(/Filter State Senate seats/)).not.toBeInTheDocument();
    expect(drawer.getByLabelText(/Filter State House seats/)).toBeInTheDocument();
  });

  it("filters a chamber by town", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    await userEvent.type(drawer.getByLabelText(/Filter State House seats/), "jamestown");
    expect(drawer.getByText("Island Dem")).toBeInTheDocument();
    expect(drawer.queryByText("Derick A. Reels")).not.toBeInTheDocument();
    expect(drawer.queryByText("Newport Rep")).not.toBeInTheDocument();
  });

  it("filters by an exact district identifier rather than a substring", async () => {
    // "9" must not also bring back 75 or 13. Districts are strings —
    // Minnesota's are "10A"/"10B".
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    await userEvent.type(drawer.getByLabelText(/Filter State House seats/), "9");
    expect(drawer.getByText("Nine Dem")).toBeInTheDocument();
    expect(drawer.queryByText("Newport Rep")).not.toBeInTheDocument();
  });

  it("explains an empty filter result instead of showing a blank chamber", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    await userEvent.type(drawer.getByLabelText(/Filter State House seats/), "zzzz");
    expect(drawer.getByText(/No State House seat matches/)).toBeInTheDocument();
  });

  it("truncates a long town list but still filters on the hidden names", async () => {
    // A rural Minnesota senate district covers 292 townships.
    const many = Array.from({ length: 40 }, (_, i) => `Township ${i + 1}`);
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
      />,
    );
    const drawer = await openContest(/State House/);
    expect(drawer.getByText(/& 37 more/)).toBeInTheDocument();
    expect(drawer.queryByText(/Township 40/)).not.toBeInTheDocument();
    await userEvent.type(drawer.getByLabelText(/Filter State House seats/), "Township 40");
    expect(drawer.getByText("Rural Rep")).toBeInTheDocument();
    expect(drawer.queryByText("Other Rep")).not.toBeInTheDocument();
  });

  it("is not a contest at all for a state whose seats are not covered", () => {
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByText(/SEATS CONTESTED/)).not.toBeInTheDocument();
    expect(screen.queryByText(/State Senate/)).not.toBeInTheDocument();
  });

  it("colours nominees with the same party codes as every other race", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    expect(drawer.getByText("Samuel W. Bell").className).toContain("text-dem-blue");
    expect(drawer.getByText("Derick A. Reels").className).toContain("text-rep-red");
  });

  it("names each seat's party in text as well as colour", async () => {
    render(<StateBallotClient ballot={ballot(legislature)} />);
    const drawer = await open();
    const row = drawer.getByText("Derick A. Reels").closest("span")!.parentElement!;
    expect(row.textContent).toContain("REP");
  });
});

describe("judges", () => {
  const withJudicial = (judicialRaces: StateBallot["judicialRaces"]) =>
    ballot({
      judicialRaces,
      judicialCoverage: {
        status: judicialRaces.length ? "covered" : "not_yet_covered",
        checkedAt: "2026-09-22T00:00:00Z",
        sourceName: "NC State Board of Elections",
      },
    });

  it("is not a contest when the state has no judicial coverage", () => {
    render(<StateBallotClient ballot={withJudicial([])} />);
    expect(screen.queryByText("Judges")).not.toBeInTheDocument();
  });

  it("groups seats under their court and shows each nominee's party in text", async () => {
    render(
      <StateBallotClient
        ballot={withJudicial([
          { court: "appeals", label: "Court of Appeals", seats: [{ seat: "Seat 4", nominees: [{ party: "R", name: "Michael C. Byrne" }] }] },
          {
            court: "district",
            label: "District Court",
            seats: [{ seat: "District 14, Seat 3", nominees: [{ party: "D", name: "Sherry Miller" }] }],
          },
        ])}
      />,
    );
    const drawer = await openContest(/Judges/);
    expect(drawer.getByText("COURT OF APPEALS")).toBeInTheDocument();
    expect(drawer.getByText("DISTRICT COURT")).toBeInTheDocument();
    expect(drawer.getByText("District 14, Seat 3")).toBeInTheDocument();
    expect(drawer.getByText(/Michael C\. Byrne/)).toBeInTheDocument();
    expect(drawer.getByText(/Sherry Miller/)).toBeInTheDocument();
  });

  it("says retention questions are not covered, so the list isn't read as the whole bench", async () => {
    render(
      <StateBallotClient
        ballot={withJudicial([
          { court: "district", label: "District Court", seats: [{ seat: "Seat 1", nominees: [{ party: "R", name: "A Judge" }] }] },
        ])}
      />,
    );
    const drawer = await openContest(/Judges/);
    expect(drawer.getByText(/Retention questions are a separate ballot item/i)).toBeInTheDocument();
  });

  it("says none are on the ballot when the state was checked and has none", async () => {
    render(
      <StateBallotClient
        ballot={ballot({
          state: "ID",
          judicialRaces: [],
          judicialCoverage: { status: "confirmed_none", checkedAt: "2026-09-22T00:00:00Z", sourceName: "Idaho Secretary of State" },
        })}
      />,
    );
    const drawer = await openContest(/Judges/);
    expect(drawer.getByText(/No judicial contests are on ID/i)).toBeInTheDocument();
    expect(drawer.getByText(/takes a majority wins the seat outright/i)).toBeInTheDocument();
  });
});

describe("ballot measures", () => {
  it("never implies zero measures for a state not loaded yet", async () => {
    render(<StateBallotClient ballot={ballot()} />);
    const drawer = await openContest(/Statewide ballot measures/);
    expect(drawer.getByText(/does not have OH's statewide ballot measures yet/)).toBeInTheDocument();
    expect(drawer.getByText("not")).toBeInTheDocument(); // "This does not mean there are none"
  });
});
