import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import MemberScorecard from "./MemberScorecard";
import type { KeyVote, Senator } from "@/types/senator";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";

const fetchRepVotes = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchRepVotes: (...args: unknown[]) => fetchRepVotes(...args),
  fetchSenatorVotes: vi.fn(),
  fetchRepStockTrades: vi
    .fn()
    .mockResolvedValue({ trades: [], total: 0, lateCount: 0, page: 1, totalPages: 1 }),
  fetchSenatorStockTrades: vi.fn(),
  fetchPresidentStockTrades: vi.fn(),
  fetchRepHoldings: vi.fn().mockResolvedValue({ available: false }),
  fetchPresidentHoldings: vi.fn(),
  fetchSenatorHoldings: vi.fn(),
  fetchRepresentativeHistory: vi.fn().mockResolvedValue({ snapshots: [] }),
  fetchSenatorHistory: vi.fn(),
  fetchPresidentHistory: vi.fn(),
}));

// Tim Burchett (R-TN-2) as the API served him on 2026-09-28, trimmed.
const member = {
  id: "tim-burchett",
  name: "Tim Burchett",
  state: "TN",
  party: "R",
  yearsInOffice: 7,
  initials: "TB",
  representationScore: {
    fundingIndependence: 73,
    promisePersistence: 55,
    constituentAlignment: 8,
    fundingDiversity: 62,
    legislativeEffectiveness: 43,
    overall: 41.35,
  },
  funding: {
    totalRaised: 1238070,
    totalContributions: 1212091,
    totalFromPACs: 138817,
    smallDonorPercentage: 24,
    topDonors: [
      {
        name: "American Israel Public Affairs Committee Political Action Committee",
        total: 17611,
        type: "PAC",
        industry: "LOBBYISTS",
        pacSponsor: null,
        pacIndustry: null,
        pacAnalysis: null,
      },
      {
        name: "Burchett for Congress",
        total: 50000,
        type: "CandidateAffiliated",
        industry: "OTHER",
        pacSponsor: null,
        pacIndustry: null,
        pacAnalysis: null,
      },
    ],
    industryBreakdown: [
      {
        industry: "LARGE_INDIVIDUAL",
        name: "Large Individual Donors",
        total: 580000,
        percentage: 47,
      },
      { industry: "SMALL_DONORS", name: "Small Donors", total: 290000, percentage: 23 },
    ],
  },
  votingRecord: {
    totalVotes: 121,
    votedWithPartyCount: 70,
    votedAgainstPartyCount: 6,
    partyLoyaltyPct: 92.1,
    recentVoteCount: 119,
    keyVoteCount: 2,
  },
  lobbyingMatches: [],
  campaignPromises: [],
  partisanDepth: null,
  sponsoredBills: [
    {
      billId: "HR.1373",
      title: "Tennessee Valley Authority Transparency Act of 2025",
      introducedDate: "2025-02-14",
      latestAction: "Received in the Senate",
      latestActionDate: "2025-06-01",
      policyArea: "ENERGY",
      policyAreas: [],
      partyLeaning: null,
      congress: 119,
      billType: "HR",
      isLaw: false,
      stage: "IN_OTHER_CHAMBER",
    },
    {
      billId: "HR.10535",
      title: "To amend section 3506 of title 44",
      introducedDate: "2026-09-24",
      latestAction: "Referred",
      latestActionDate: "2026-09-24",
      policyArea: "TAXES",
      policyAreas: [],
      partyLeaning: null,
      congress: 119,
      billType: "HR",
      isLaw: false,
      stage: "REFERRED",
    },
  ],
  leadershipScore: 0.33,
  ideologyScore: 0.74,
  sponsorshipDescription: "conservative Republican",
  officePhone: "(202) 225-5435",
} as unknown as Senator;

const breakdown: RepresentationScoreBreakdown = {
  fundingIndependence: {
    score: 73,
    components: [
      {
        label: "PAC dependency",
        weight: 0.38,
        score: 81.4,
        detail: "11% of $1,212,091 in contributions came from PACs",
      },
    ],
    facts: {
      contributions: 1212091,
      pacShare: 0.1145,
      smallDonorShare: 0.24,
      smallDonorExpectedShare: 0.06,
      smallDonorComparison: "house-median",
    },
  },
  constituentAlignment: {
    score: 8,
    components: [
      {
        label: "Seat-relative vote alignment",
        weight: 0.7,
        score: 0,
        detail: "broke with party on 7.9%",
      },
    ],
    facts: { party: "R", partyVotes: 76, breaks: 6, breakRate: 0.0789, expectedBreakRate: 0.011 },
  },
  legislativeEffectiveness: {
    score: 43,
    components: [
      {
        label: "Legislative leadership",
        weight: 0.25,
        score: 33.5,
        detail: "PageRank leadership 33/100",
      },
    ],
    facts: { billsByStage: [58, 3, 0, 4, 0] },
  },
};

// House roll call 277 of 2026: the motion to recommit H.R. 8800.
const recommit: KeyVote = {
  billName: "H R 8800",
  billId: "HouseRC-2026-277",
  date: "",
  vote: "Yea",
  policyArea: "DEFENSE",
  policyAreas: [],
  partyAlignmentWeight: 0,
  stance: "neutral",
  description: "",
  partyLeaning: "D",
  votedWithParty: false,
  voteCategory: "recent",
  rollCall: {
    chamber: "house",
    congress: 119,
    session: 2,
    number: 277,
    date: "2026-07-22",
    question: "On Motion to Recommit",
    title: "H R 8800",
    result: "Failed",
    billId: "HR.8800",
    billLabel: "H.R. 8800",
    sourceUrl: "https://clerk.house.gov/evs/2026/roll277.xml",
    parties: [
      { party: "R", yea: 2, nay: 215, present: 0, notVoting: 3 },
      { party: "D", yea: 211, nay: 0, present: 0, notVoting: 2 },
    ],
  },
};

function renderCard() {
  // Inside <main>, as the profile page mounts it.
  return render(
    <main>
      <MemberScorecard
        member={member}
        chamber="house"
        breakdown={breakdown}
        district={2}
        stateName="Tennessee"
        rank={{ rank: 412, of: 433 }}
        committees={[
          { committeeName: "House Committee on Foreign Affairs", chamber: "house", title: null },
        ]}
      />
    </main>
  );
}

beforeEach(() => {
  fetchRepVotes.mockReset();
  fetchRepVotes.mockResolvedValue({
    votes: [recommit],
    total: 1,
    page: 1,
    perPage: 100,
    totalPages: 1,
    counts: { all: 121, yea: 79, nay: 39, againstParty: 6 },
  });
});

describe("MemberScorecard", () => {
  it("states each score's evidence without anything to expand", async () => {
    renderCard();
    expect(screen.getByRole("heading", { level: 1, name: "Tim Burchett" })).toBeInTheDocument();
    expect(screen.getByText("#412 of 433 representatives")).toBeInTheDocument();
    expect(
      screen.getByText(
        /11% of \$1\.2M in contributions came from PACs\. 24% came from donors giving under \$200; the House median is 6%\./
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Voted against most Republicans on 6 of 76 party-line votes \(7\.9%\)\. Republicans in seats that lean like TN-2 do that on 1\.1%\./
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Sponsored 65 bills this Congress\. 4 passed the House, 3 got committee action; none has become law\./
      )
    ).toBeInTheDocument();
    // A member's own committee isn't one of their donors.
    expect(screen.queryByText("Burchett for Congress")).not.toBeInTheDocument();
  });

  it("lists every vote against party with the chamber's own tallies", async () => {
    renderCard();
    expect(fetchRepVotes).toHaveBeenCalledWith(
      "tim-burchett",
      expect.objectContaining({ category: "all", filter: "against-party" })
    );
    // The Clerk's bare "H R 8800" shows as the site's own label.
    // Each break opens its bill's page.
    expect(await screen.findByRole("link", { name: "H.R. 8800" })).toHaveAttribute(
      "href",
      "/congress/bills/HR.8800?congress=119"
    );
    expect(screen.getByText("VOTED YEA")).toBeInTheDocument();
    expect(
      screen.getByText(/Republicans 2 yea, 215 nay · Democrats 211 yea, 0 nay/)
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Jul 22, 2026" })).toHaveAttribute(
      "href",
      "/congress/2026-07-22"
    );
  });

  it("lists the breaks the score counts, and the flank's apart, from the breakdown", async () => {
    const counted = {
      ...breakdown,
      constituentAlignment: {
        ...breakdown.constituentAlignment,
        facts: {
          party: "R",
          partyVotes: 292,
          breaks: 1,
          breakRate: 0.0034,
          expectedBreakRate: 0.004,
          flankBreaks: 1,
          breakVotes: [
            { vote: "Nay", rollCall: { ...recommit.rollCall!, number: 12, billId: "HR.1" } },
          ],
          flankBreakVotes: [{ vote: "Yea", rollCall: recommit.rollCall! }],
        },
      },
    };
    render(
      <MemberScorecard
        member={member}
        chamber="house"
        breakdown={counted}
        district={2}
        stateName="Tennessee"
        rank={{ rank: 412, of: 433 }}
        committees={[]}
      />
    );
    expect(screen.getByText("Votes against party (1)")).toBeInTheDocument();
    expect(screen.getByText("From the right flank, not counted (1)")).toBeInTheDocument();
    expect(screen.getByText(/Each bill or nomination counts once/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "H.R. 8800" })).toHaveLength(2);
    // Served with the score: nothing to fetch.
    expect(fetchRepVotes).not.toHaveBeenCalled();
  });

  it("shows constituents' approval by party in the alignment column, marked not scored", async () => {
    // Illustrative figures, not Burchett's.
    const surveyed = {
      ...member,
      constituentApproval: {
        survey: "CES 2024 Common Content (pre-election wave, Oct-Nov 2024)",
        fielded: "2024-10/2024-11",
        surveyedAs: "Tim Burchett",
        byParty: [
          { party: "D" as const, approve: 0.21, ownWeight: 0.3, respondents: 40 },
          { party: "R" as const, approve: 0.74, ownWeight: 0.6, respondents: 90 },
        ],
      },
    };
    render(
      <main>
        <MemberScorecard
          member={surveyed}
          chamber="house"
          breakdown={breakdown}
          district={2}
          stateName="Tennessee"
          committees={[]}
        />
      </main>
    );
    await screen.findByText("H.R. 8800");
    const column = document.getElementById("constituent-alignment")!;
    const list = within(column).getByRole("list", { name: /Approval among constituents/ });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((li) => li.textContent)
    ).toEqual([
      expect.stringMatching(/Democrats21% approve40 with an opinion · mostly based on similar/),
      expect.stringMatching(/Republicans74% approve90 with an opinion$/),
    ]);
    expect(within(column).getByText(/Informational, not scored\./)).toBeInTheDocument();
    const violations = (
      await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } })
    ).violations.map((v) => v.id);
    expect(violations).toEqual([]);
  });

  it("opens the full record in a drawer and closes it with Escape", async () => {
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: /All 65 sponsored bills/ }));
    const dialog = screen.getByRole("dialog", { name: "Sponsored bills" });
    expect(within(dialog).getByText(/SPONSORED LEGISLATION/)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("has no structural accessibility violations, closed or with a drawer open", async () => {
    renderCard();
    await screen.findByText("H.R. 8800");
    const run = async () =>
      (
        await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } })
      ).violations.map((v) => v.id);
    expect(await run()).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: /All donors and industries/ }));
    expect(await run()).toEqual([]);
  });
});
