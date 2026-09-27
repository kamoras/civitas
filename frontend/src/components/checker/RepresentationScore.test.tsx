import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfigProvider } from "@/hooks/useConfig";
import type { AppConfig } from "@/lib/api";
import type { Senator, VotingRecord } from "@/types/senator";
import RepresentationScore from "./RepresentationScore";

vi.mock("@/components/shared/ScoreBreakdownPanel", () => ({ default: () => null }));

const breakdown = {
  fundingIndependence: 50, promisePersistence: 50, constituentAlignment: 50,
  fundingDiversity: 50, legislativeEffectiveness: 50, overall: 50,
} as Senator["representationScore"];

const config = {
  industries: {}, platformCategories: {}, policyAreas: [], billStages: {},
  constituentVotes: { minimum: 3, fullConfidence: 20 },
} as AppConfig;

function record(withParty: number, against: number): VotingRecord {
  return {
    totalVotes: 40, votedWithPartyCount: withParty, votedAgainstPartyCount: against,
    partyLoyaltyPct: 0, recentVoteCount: 0, keyVoteCount: 0,
  };
}

function renderWith(votingRecord: VotingRecord, cfg: AppConfig | null = config) {
  return render(
    <ConfigProvider value={cfg}>
      <RepresentationScore breakdown={breakdown} votingRecord={votingRecord} />
    </ConfigProvider>,
  );
}

describe("RepresentationScore constituent basis line", () => {
  it("says the vote part is neutral below the minimum", () => {
    renderWith(record(1, 1));
    expect(screen.getByText(/2 party-line, vote part neutral 50/)).toBeInTheDocument();
  });

  it("says how much of the distance from 50 a thin record keeps", () => {
    renderWith(record(8, 4));
    expect(screen.getByText(/12 party-line, vote part keeps 60% of its distance from 50/)).toBeInTheDocument();
  });

  it("says nothing extra at full confidence", () => {
    renderWith(record(18, 4));
    expect(screen.getByText("40 votes tracked")).toBeInTheDocument();
  });

  it("claims nothing about shrinkage before the rules have loaded", () => {
    renderWith(record(8, 4), null);
    expect(screen.getByText("40 votes tracked")).toBeInTheDocument();
  });
});
