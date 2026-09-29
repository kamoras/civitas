import { describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
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

  it("says 1 contest and 1 candidate, not 1 contests", () => {
    render(
      <StateBallotClient ballot={ballot({ houseRaces: [houseRace({ candidates: [candidate({ id: "d" })] })] })} />,
    );
    expect(screen.getByText(/^1 contest · 1 federal candidate/)).toBeInTheDocument();
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
    await userEvent.click(drawer.getByRole("button", { name: "U.S. Representative\u00a0→" }));
    expect(within(screen.getByRole("dialog")).getByRole("heading", { name: "U.S. Representative" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "←\u00a0U.S. Senator" }));
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
    window.history.replaceState(null, "", "/elections/states/OH#race-2026-HOUSE-OH-1");
    render(<StateBallotClient ballot={ballot()} />);
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByRole("heading", { name: "U.S. Representative" })).toBeInTheDocument();
    await userEvent.click(drawer.getByRole("button", { name: "ALL CONTESTS" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });

  it("opens a #race- link reached by in-app navigation, once the URL is this page's", async () => {
    // A soft navigation renders the new page BEFORE Next commits its URL:
    // the first render still sees the page the reader came from.
    window.history.replaceState(null, "", "/elections");
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // Next commits the URL (pushState: no event of its own); the page picks
    // it up on its next look.
    await act(async () => {
      window.history.pushState(null, "", "/elections/states/OH#race-2026-HOUSE-OH-1");
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    });
    const drawer = within(await screen.findByRole("dialog"));
    expect(drawer.getByRole("heading", { name: "U.S. Representative" })).toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });

  it("latches the page's own URL, not the hash of the page it came from", async () => {
    // Came from another page that had a #race- hash of its own.
    window.history.replaceState(null, "", "/elections/states/GA#race-2026-HOUSE-OH-1");
    render(<StateBallotClient ballot={ballot()} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => {
      window.history.pushState(null, "", "/elections/states/OH");
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });
});

describe("U.S. Representative", () => {
  const twoDistricts = (overrides: Partial<StateBallot> = {}) =>
    ballot({
      ...overrides,
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

  it("shows a district's lean in the picker and on a picked district during the campaign", async () => {
    render(<StateBallotClient ballot={twoDistricts()} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.getAllByText("D+3").length).toBe(2);
    await userEvent.click(drawer.getByRole("button", { name: /Second District Dem/ }));
    expect(within(screen.getByRole("dialog")).getByText("D+3")).toBeInTheDocument();
    window.location.hash = "";
  });

  it("marks a sitting member on new lines as a sitting member, never the new district's incumbent", async () => {
    render(<StateBallotClient ballot={twoDistricts({ newDistrictLines: true })} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.getByText("Greg Landsman (sitting member)")).toBeInTheDocument();
    expect(drawer.queryByText(/\(I\)/)).not.toBeInTheDocument();
    await userEvent.click(drawer.getByRole("button", { name: /Greg Landsman/ }));
    const research = within(screen.getByRole("dialog"));
    expect(research.getByText("SITTING MEMBER")).toBeInTheDocument();
    expect(research.queryByText("INCUMBENT")).not.toBeInTheDocument();
    window.location.hash = "";
  });

  it("marks a sitting member on a single new-lines seat's ballot box the same way", () => {
    render(<StateBallotClient ballot={ballot({ newDistrictLines: true })} />);
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText("SITTING MEMBER")).toBeInTheDocument();
    expect(within(box).queryByText("INCUMBENT")).not.toBeInTheDocument();
  });

  it("keeps INCUMBENT on a state's old lines", () => {
    render(<StateBallotClient ballot={ballot()} />);
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText("INCUMBENT")).toBeInTheDocument();
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

  it("never colours a statewide stand-in as the district's own lean", async () => {
    render(<StateBallotClient ballot={ballot({ houseRaces: [houseRace({ pvi: 6, pviLevel: "state" })] })} />);
    const drawer = await openContest(/U\.S\. Representative/);
    const lean = drawer.getByText("R+6");
    expect(lean).not.toHaveClass("text-signal-red");
    expect(lean).toHaveClass("text-ink-lo");
  });

  it("colours a district's own lean", async () => {
    render(<StateBallotClient ballot={ballot({ houseRaces: [houseRace({ pvi: 6 })] })} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.getByText("R+6")).toHaveClass("text-signal-red");
  });

  const fourDistricts = (overrides: Partial<StateBallot> = {}) =>
    ballot({
      houseRaces: [1, 2, 3, 4].map((d) =>
        houseRace({ id: `d${d}`, district: d, counties: [`County ${d}`], pvi: 6, pviLevel: "state" }),
      ),
      ...overrides,
    });

  it("offers house.gov and a candidate's name where the lines are unchanged", async () => {
    render(<StateBallotClient ballot={fourDistricts()} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.getByRole("link", { name: /house\.gov/ })).toBeInTheDocument();
    expect(drawer.getByLabelText("Filter districts by county, candidate, or district number")).toBeInTheDocument();
  });

  it("never sends a reader on new lines to a lookup by representative", async () => {
    render(
      <StateBallotClient
        ballot={fourDistricts({
          state: "TX",
          stateName: "Texas",
          newDistrictLines: true,
          officialLookup: {
            url: "https://teamrv-mvp.sos.texas.gov/MVP/mvp.do",
            label: "Texas voter portal",
            sourceName: "Texas Secretary of State",
            isStateSpecific: true,
            verifiedAt: null,
          },
        })}
      />,
    );
    const drawer = await openContest(/U\.S\. Representative/);
    // house.gov answers for the district today's member holds.
    expect(drawer.queryByRole("link", { name: /house\.gov/ })).not.toBeInTheDocument();
    expect(drawer.getByText(/new congressional district lines/)).toBeInTheDocument();
    expect(drawer.getByText(/lookups by representative show today's districts, not these/)).toBeInTheDocument();
    // The state's own lookup does know the new lines.
    expect(drawer.getByRole("link", { name: "Texas voter portal (opens in new tab)" })).toHaveAttribute(
      "href",
      "https://teamrv-mvp.sos.texas.gov/MVP/mvp.do",
    );
    const input = drawer.getByLabelText("Filter districts by county or district number");
    await userEvent.type(input, "zzz");
    expect(drawer.getByText(/Try a county name or a district number/)).toBeInTheDocument();
    expect(drawer.queryByText(/surname/)).not.toBeInTheDocument();
    // Never an address field.
    expect(drawer.queryByLabelText(/address|zip/i)).not.toBeInTheDocument();
  });

  it("links no lookup at all on new lines when the state has none of its own", async () => {
    render(<StateBallotClient ballot={fourDistricts({ newDistrictLines: true })} />);
    const drawer = await openContest(/U\.S\. Representative/);
    expect(drawer.queryByRole("link", { name: /house\.gov|election office/i })).not.toBeInTheDocument();
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

describe("state offices not loaded", () => {
  it("fills the State column with a placeholder rather than leaving it empty", async () => {
    render(<StateBallotClient ballot={ballot()} />);
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText("State offices")).toBeInTheDocument();
    const drawer = await openContest(/State offices/);
    expect(drawer.getByText(/does not have OH's own offices yet/)).toBeInTheDocument();
    expect(drawer.getByText("not")).toBeInTheDocument(); // "This does not mean there are none"
  });

  it("shows no placeholder once any state office is on file", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          statewideRaces: [],
          statewideCoverage: { status: "confirmed_none", sourceName: "Ohio Secretary of State", checkedAt: null },
        })}
      />,
    );
    expect(screen.queryByText("State offices")).not.toBeInTheDocument();
  });
});

describe("term lengths", () => {
  it("gives the term a vote is for: two years in the House, six in the Senate", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          senateRaces: [houseRace({ id: "2026-SEN-OH", office: "S", district: null, counties: null })],
        })}
      />,
    );
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText(/you vote in one · 2-year term|One statewide seat · 2-year term/)).toBeInTheDocument();
    expect(within(box).getByText(/6-year term/)).toBeInTheDocument();
  });

  it("says a special Senate election fills only the rest of the term", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          senateRaces: [houseRace({ id: "2026-SEN-OH-S", office: "S", district: null, counties: null, isSpecial: true })],
        })}
      />,
    );
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText(/Special election · .* · fills the rest of the term/)).toBeInTheDocument();
    expect(within(box).queryByText(/6-year term/)).not.toBeInTheDocument();
  });
});

describe("state office terms", () => {
  it("gives a state office's term where the backend has it, and nothing where it does not", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          statewideCoverage: { status: "covered", sourceName: "GA SoS", checkedAt: null },
          statewideRaces: [
            { office: "governor", label: "Governor", nominees: [{ party: "DEM", name: "A Dem" }], termYears: 4 },
            { office: "public_service_commission-3", label: "Public Service Commission, District 3", nominees: [{ party: "REP", name: "A Rep" }], termYears: 6 },
            { office: "labor_commissioner", label: "Labor Commissioner", nominees: [{ party: "REP", name: "Another Rep" }], termYears: null },
          ],
        })}
      />,
    );
    const box = screen.getByTestId("ballot-columns");
    expect(within(box).getByText(/· 4-year terms/)).toBeInTheDocument();
    expect(within(box).getByText(/· 6-year terms/)).toBeInTheDocument();
    expect(within(box).getByText("Labor Commissioner").textContent).toBe("Labor Commissioner");
  });

  it("gives each legislative chamber's term", async () => {
    render(
      <StateBallotClient
        ballot={ballot({
          stateLegRaces: [
            { chamber: "upper", label: "State Senate", termYears: 4,
              districts: [{ district: "5", towns: ["Providence city"], nominees: [{ party: "DEM", name: "A Senator" }] }] },
          ],
        })}
      />,
    );
    const drawer = await openContest(/State Senate/);
    expect(drawer.getByText(/STATE SENATE — 1 SEAT CONTESTED/).textContent).toContain("4-YEAR TERMS");
  });
});
