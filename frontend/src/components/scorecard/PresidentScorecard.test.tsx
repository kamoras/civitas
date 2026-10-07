import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import PresidentScorecard from "./PresidentScorecard";
import PresidentSummary from "./PresidentSummary";
import type { President } from "@/types/president";
import type { PresidentScoreBreakdown } from "@/types/scoreBreakdown";

vi.mock("@/lib/api", () => ({
  fetchPresidentStockTrades: vi
    .fn()
    .mockResolvedValue({ trades: [], total: 21285, lateCount: 0, page: 1, totalPages: 1 }),
  fetchSenatorStockTrades: vi.fn(),
  fetchRepStockTrades: vi.fn(),
  fetchPresidentHistory: vi.fn().mockResolvedValue({ snapshots: [] }),
  fetchPresidentHoldings: vi.fn().mockResolvedValue({
    available: true,
    reportLabel: "2025 annual report",
    asOfDate: "2025-12-31",
    filedDate: "2026-07-01",
    sourceUrl: "https://extapps2.oge.gov/report.pdf",
    parsed: true,
    unreadableReason: null,
    laterFilingLabel: null,
    laterFilingUrl: null,
    holdingsCount: 1,
    unvaluedCount: 0,
    totalLow: 50000000,
    totalHigh: 50000000,
    totalOpenEnded: true,
    categories: [
      {
        category: "REAL_ESTATE",
        label: "Real estate",
        color: "#008300",
        count: 1,
        unvaluedCount: 0,
        zeroValueCount: 0,
        valueLow: 50000000,
        valueHigh: 50000000,
        openEnded: true,
        weight: 50000000,
        share: 1,
      },
    ],
    categoryFilter: null,
    holdings: [
      {
        assetName: "40 Wall Street LLC",
        account: null,
        ticker: null,
        assetType: "Commercial real estate",
        category: "REAL_ESTATE",
        categoryLabel: "Real estate",
        owner: "self",
        valueText: "Over $50,000,000",
        valueLow: 50000000,
        valueHigh: 50000000,
        valueOpenEnded: true,
      },
    ],
    total: 1,
    page: 1,
    perPage: 5,
    totalPages: 1,
  }),
  fetchSenatorHoldings: vi.fn(),
  fetchRepHoldings: vi.fn(),
  fetchSenatorHistory: vi.fn(),
  fetchRepresentativeHistory: vi.fn(),
}));

// Donald J. Trump's second term as the API served it on 2026-09-28.
const president = {
  id: "trump-47",
  name: "Donald J. Trump",
  party: "R",
  number: 47,
  termStart: "2025-01-20",
  termEnd: null,
  isCurrent: true,
  score: {
    publicMandate: 29,
    effectiveness: 33,
    historicalLegacy: null,
    overall: 31,
    dimensionsAvailable: 2,
    // A sitting president has no Historical Legacy: the two scored parts
    // share the whole, and the card says so.
    effectiveWeights: { publicMandate: 0.5, effectiveness: 0.5 },
  },
  avgApproval: 37.3,
  gdpGrowthAvg: null,
  jobsCreatedMillions: 0.5,
  eoCount: 285,
  electionMargin: null,
  historicalLegacyScore: null,
  recentAvgApproval: 35.4,
} as President;

const breakdown: PresidentScoreBreakdown = {
  publicMandate: {
    score: 29,
    components: [{ label: "Average approval", weight: 0.7, score: 12.7, detail: "…" }],
    facts: {
      approval: 37.27,
      approvalMean: 50.93,
      approvalTrend: -5.6,
      trendMean: -13.6,
      approvalStart: 41.0,
      trendExpected: 4.8,
      comparedOverDays: 598,
      electionMargin: null,
      marginMean: 8.39,
      recentApproval: 35.42,
    },
  },
  effectiveness: {
    score: 33,
    components: [{ label: "Jobs created", weight: 1, score: 32.6, detail: "…" }],
    facts: {
      jobsMillions: 0.5,
      jobsPerYear: 0.73,
      jobsMean: 1.44,
      gdpGrowth: null,
      gdpMean: null,
      gdpSince: null,
      gdpPerPerson: null,
      gdpPeers: null,
      gdpCatchUp: null,
      gdpRelative: null,
      gdpRelativeMean: null,
    },
  },
  historicalLegacy: {
    score: null as unknown as number,
    components: [],
    facts: {
      points: null,
      pointsMean: 549,
      otherTerms: [{ id: "trump-45", number: 45, points: 312, score: 12 }],
    },
  },
};

describe("PresidentScorecard", () => {
  it("states each score's figures beside the averages they are scored against", async () => {
    render(
      <main>
        <PresidentScorecard president={president} breakdown={breakdown} rank={null} />
      </main>
    );
    expect(screen.getByRole("heading", { level: 1, name: "Donald J. Trump" })).toBeInTheDocument();
    expect(screen.getByText("Ranked once the term ends")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Approval has averaged 37\.3% so far, below the 50\.9% past presidents averaged over their first 20 months\. It fell 5\.6 points from 41\.0% at the start\. Presidents who started there typically rose 4\.8 points, so this is worse than usual\./
      )
    ).toBeInTheDocument();
    // Each score shows its actual share of this president's overall, not
    // the nominal weight (Historical Legacy is unscored for a sitting one).
    expect(screen.getAllByText("50% of the score")).toHaveLength(2);
    expect(
      screen.getByText(
        /Growth is counted from the term's second full year, so there's no figure yet\./
      )
    ).toBeInTheDocument();
    // The other presidency's historians' rating, linked.
    expect(screen.getByRole("link", { name: /312 points, scored 12/ })).toHaveAttribute(
      "href",
      "/politicians/trump-45"
    );
    expect(await screen.findByText("21,285 disclosed this term")).toBeInTheDocument();
    // The sitting president's annual-report holdings, in the members' panel.
    expect(await screen.findByRole("heading", { name: "Holdings" })).toBeInTheDocument();
    expect(screen.getByText("40 Wall Street LLC")).toBeInTheDocument();
    expect(screen.getByText("285 signed")).toBeInTheDocument();
  });

  it("states postwar growth against the peer economies, catch-up allowed for", async () => {
    const peers = {
      ...breakdown,
      effectiveness: {
        ...breakdown.effectiveness,
        facts: {
          ...breakdown.effectiveness.facts,
          gdpGrowth: 2.3,
          gdpMean: 2.8,
          gdpSince: true,
          gdpPerPerson: 1.4,
          gdpPeers: 1.12,
          gdpCatchUp: -1.58,
          gdpRelative: 1.86,
          gdpRelativeMean: 2.23,
        },
      },
    };
    render(<PresidentScorecard president={president} breakdown={peers} rank={null} />);
    expect(
      screen.getByText(
        /Growth: 1\.4% a year per person, against 1\.1% in 13 other wealthy countries over the same years, which shared the same oil shocks, recessions and pandemic\. Those countries were poorer and still catching up with US incomes, which alone would have had them growing 1\.6 points a year faster\. Allowing for that, the US came out 1\.9 points a year ahead; under the typical president since 1947 it came out 2\.2 points a year ahead\./
      )
    ).toBeInTheDocument();
    expect(await screen.findByText("21,285 disclosed this term")).toBeInTheDocument();
  });

  it("states approval by party against the era's polarization", async () => {
    const era = {
      ...breakdown,
      publicMandate: {
        ...breakdown.publicMandate,
        facts: {
          ...breakdown.publicMandate.facts,
          approvalGroups: { own: 87.2, opp: 3.9, ind: 30.4 },
          approvalExpected: { own: 86.6, opp: 6.1, ind: 36.0 },
          approvalVsEra: -2.7,
          approvalVsEraMean: 0.1,
        },
      },
    };
    render(<PresidentScorecard president={president} breakdown={era} rank={null} />);
    expect(
      screen.getByText(
        /Approval has averaged 37\.3% so far: 87% in the president's party, 4% in the other party and 30% among independents\. Under the same polarization, presidents typically got 87%, 6% and 36% over their first 20 months\. That puts this term 2\.7 points below the era; the typical president comes out 0\.1 points above\./
      )
    ).toBeInTheDocument();
    expect(await screen.findByText("21,285 disclosed this term")).toBeInTheDocument();
  });

  it("states unemployment and inflation against where the term started", async () => {
    const economy = {
      ...breakdown,
      effectiveness: {
        ...breakdown.effectiveness,
        facts: {
          ...breakdown.effectiveness.facts,
          unemploymentStart: 9.3,
          unemploymentChange: -4.9,
          unemploymentExpected: -4.5,
          inflationStart: 4.7,
          inflationAverage: 5.0,
          inflationExpected: 4.4,
          economyYears: 4,
        },
      },
    };
    render(<PresidentScorecard president={president} breakdown={economy} rank={null} />);
    expect(
      screen.getByText(
        /Unemployment: fell 4\.9 points from 9\.3% over the credited years; for presidents starting at that rate it typically fell 4\.5 points, so this is better than usual\./
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Inflation: prices rose 5\.0% a year, from 4\.7% the year the term began; presidents starting there averaged 4\.4%, so this is worse than usual\./
      )
    ).toBeInTheDocument();
    expect(await screen.findByText("21,285 disclosed this term")).toBeInTheDocument();
  });

  it("never calls a scored dimension unrated when the breakdown is missing", async () => {
    // A breakdown that failed to load, or one cached from before the API
    // served facts, must not put "not rated" beside a score (2026-09-28:
    // the 45th-president term read "Not rated" over its historians' 12).
    const former = {
      ...president,
      id: "trump-45",
      number: 45,
      termEnd: "2021-01-20",
      isCurrent: false,
      score: { ...president.score, historicalLegacy: 12, dimensionsAvailable: 4 },
    } as President;
    render(
      <main>
        <PresidentScorecard president={former} breakdown={null} rank={null} />
      </main>
    );
    expect(screen.queryByText(/not rated|not scored|No GDP figure/i)).not.toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("has no structural accessibility violations", async () => {
    render(
      <main>
        <PresidentScorecard president={president} breakdown={breakdown} rank={null} />
      </main>
    );
    await screen.findByText("21,285 disclosed this term");
    await screen.findByText("40 Wall Street LLC");
    // The share buttons are part of what axe checks here: the summary, the
    // four score columns and the holdings.
    expect(screen.getAllByRole("button", { name: /as an image$/ })).toHaveLength(5);
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("the leaderboard's summary states the scores and links to the full scorecard", () => {
    render(<PresidentSummary president={president} />);
    expect(screen.getByRole("heading", { level: 2, name: "Donald J. Trump" })).toBeInTheDocument();
    expect(screen.getByText("31")).toBeInTheDocument();
    expect(screen.getByText("Not rated yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the full scorecard/ })).toHaveAttribute(
      "href",
      "/politicians/trump-47"
    );
  });
});
