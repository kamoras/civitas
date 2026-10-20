import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import PresidentScorecard from "./PresidentScorecard";
import type { President } from "@/types/president";
import type { PresidentPeerMeasure, PresidentPeers } from "@/types/presidentPeers";

vi.mock("@/lib/api", () => ({
  fetchPresidentStockTrades: vi
    .fn()
    .mockResolvedValue({ trades: [], total: 21285, lateCount: 0, page: 1, totalPages: 1 }),
  fetchSenatorStockTrades: vi.fn(),
  fetchRepStockTrades: vi.fn(),
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
        assetName: "Example Tower LLC",
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

// A sitting president, shaped as the API serves one.
const president = {
  id: "doe-47",
  name: "Jane Doe",
  party: "R",
  number: 47,
  termStart: "2025-01-20",
  termEnd: null,
  isCurrent: true,
  avgApproval: 37.3,
  gdpGrowthAvg: null,
  jobsCreatedMillions: 0.5,
  eoCount: 285,
  electionMargin: null,
  historicalLegacyScore: null,
  recentAvgApproval: 35.4,
} as President;

const approval: PresidentPeerMeasure = {
  key: "approval",
  label: "Average approval",
  question: "How did the public rate the term?",
  why: "How much of the public approved of the job the president was doing, on average.",
  measured: "The average of every approval poll over the term.",
  source: { name: "American Presidency Project", url: "https://www.presidency.ucsb.edu/" },
  value: 37.3,
  shown: "37.3%",
  expected: 57.5,
  expectedBasis: "same stretch",
  expectedShown: "57.5% over the same stretch",
  peerGroup: { label: "presidencies with approval polls (from 1945)", n: 3 },
  distribution: [45.1, 49.2, 60.3],
  distributionShown: ["45.1%", "49.2%", "60.3%"],
  distributionIds: ["roe-40", "poe-41", "moe-42"],
  distributionNames: ["John Roe", "Ann Poe", "Max Moe"],
  percentile: 12.5,
  word: "well below other presidencies",
  sentence:
    "Approval averaged 37.3% during the term. 3 of the other 3 presidencies averaged higher.",
  thin: false,
};

const peers: PresidentPeers = {
  rule: { wellOutside: [10, 90], outside: [25, 75], wellGapSd: 1, minPeers: 10 },
  measures: [approval],
  standsOut: `Average approval: ${approval.sentence}`,
  standsOutWhy: "Picked by rule.",
  missing: [
    "Not rated by historians: C-SPAN's survey rates only completed terms, and the newest edition published does not rate this presidency.",
  ],
  historians: {
    rank: null,
    rated: 45,
    points: null,
    surveyYear: 2021,
    surveyUrl: "https://www.c-span.org/presidentsurvey2021/",
  },
  recentApproval: 35.4,
  otherTerms: [{ id: "doe-45", number: 45, points: 312 }],
};

describe("PresidentScorecard", () => {
  it("sets each figure among the other presidencies, with no score", async () => {
    render(
      <main>
        <PresidentScorecard president={president} peers={peers} />
      </main>
    );
    expect(screen.getByRole("heading", { level: 1, name: "Jane Doe" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Where Jane Doe stands among presidents" })
    ).toBeInTheDocument();
    // No composite, part score, breakdown or rank of Civitas's own.
    expect(
      screen.queryByText(/out of 100|\/100|scored \d|How this is computed|scorecard/i)
    ).not.toBeInTheDocument();
    const figure = screen.getByRole("figure", { name: /Approval averaged 37\.3%/ });
    expect(within(figure).getAllByText("well below other presidencies")[0]).toBeInTheDocument();
    expect(
      within(figure).getByText(/Compared with 3 presidencies with approval polls \(from 1945\)/)
    ).toBeInTheDocument();
    // The strip's accessible form: every other presidency, linked.
    expect(within(figure).getByRole("link", { name: "Max Moe" })).toHaveAttribute(
      "href",
      "/politicians/moe-42"
    );
    expect(screen.getAllByText(/Not rated by historians/)[0]).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /45th-president term/ })).toHaveAttribute(
      "href",
      "/politicians/doe-45"
    );
    expect(screen.getByRole("link", { name: "C-SPAN survey" })).toHaveAttribute(
      "href",
      "https://www.c-span.org/presidentsurvey2021/"
    );
    expect(await screen.findByText("21,285 disclosed this term")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Holdings" })).toBeInTheDocument();
    expect(screen.getByText("Example Tower LLC")).toBeInTheDocument();
    expect(screen.getByText("285 signed")).toBeInTheDocument();
    expect(screen.getByText("35.4%")).toBeInTheDocument();
  });

  it("names the historians' rank as theirs, with the survey's year", () => {
    const former = { ...president, id: "doe-45", isCurrent: false, termEnd: "2021-01-20" };
    const rated = {
      ...peers,
      historians: { ...peers.historians, rank: 41, points: 312 },
      otherTerms: [],
    };
    render(<PresidentScorecard president={former as President} peers={rated} />);
    expect(screen.getByText("#41")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Their ranking, not Civitas's: the C-SPAN Presidential Historians Survey, 2021, by total points \(312 points\)\./
      )
    ).toBeInTheDocument();
  });

  it("says the comparison is missing when it failed to load, and claims nothing", () => {
    const former = { ...president, id: "doe-45", termEnd: "2021-01-20", isCurrent: false };
    render(<PresidentScorecard president={former as President} peers={null} />);
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
    expect(screen.queryByText(/not rated/i)).not.toBeInTheDocument();
  });

  it("has no structural accessibility violations", async () => {
    render(
      <main>
        <PresidentScorecard president={president} peers={peers} />
      </main>
    );
    await screen.findByText("21,285 disclosed this term");
    await screen.findByText("Example Tower LLC");
    // Share buttons: the summary, the standing among presidents, the holdings.
    expect(screen.getAllByRole("button", { name: /as an image$/ })).toHaveLength(3);
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});
