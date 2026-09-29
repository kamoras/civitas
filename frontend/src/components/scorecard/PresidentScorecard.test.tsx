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
    agencyAlignment: 91,
    historicalLegacy: null,
    overall: 51,
    dimensionsAvailable: 3,
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
    },
  },
  agencyAlignment: {
    score: 91,
    components: [{ label: "Finalization rate", weight: 1, score: 90.7, detail: "…" }],
    facts: { finalizedPct: 62, finalizedMean: 54, rulemakings: 1400 },
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
        /Averaged 37\.3% approval over the term; presidents average 50\.9%\. Approval fell 5\.6 points/
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /62% of the 1,400 rulemakings federal agencies began reached a final rule\. Administrations since 1994 average 54%\./
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(/GDP growth is measured from the second full year of a term\./)
    ).toBeInTheDocument();
    // The other presidency's historians' rating, linked.
    expect(screen.getByRole("link", { name: /312 points, scored 12/ })).toHaveAttribute(
      "href",
      "/politicians/trump-45"
    );
    expect(await screen.findByText("21,285 disclosed this term")).toBeInTheDocument();
    expect(screen.getByText("285 signed")).toBeInTheDocument();
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
    // The share buttons are part of what axe checks here: the summary and
    // the four score columns.
    expect(screen.getAllByRole("button", { name: /as an image$/ })).toHaveLength(5);
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("the leaderboard's summary states the scores and links to the full scorecard", () => {
    render(<PresidentSummary president={president} />);
    expect(screen.getByRole("heading", { level: 2, name: "Donald J. Trump" })).toBeInTheDocument();
    expect(screen.getByText("51")).toBeInTheDocument();
    expect(screen.getByText("Not rated yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the full scorecard/ })).toHaveAttribute(
      "href",
      "/politicians/trump-47"
    );
  });
});
