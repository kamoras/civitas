import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import MethodsLedger from "./MethodsLedger";
import type { Senator } from "@/types/senator";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";

const member = {
  id: "pat-doe",
  name: "Pat Doe",
  state: "MI",
  party: "D",
  representationScore: {
    fundingIndependence: 60,
    promisePersistence: null,
    constituentAlignment: 86,
    fundingDiversity: 63,
    legislativeEffectiveness: 75,
    overall: 75.4,
    confidence: { constituentAlignment: "high" },
  },
} as unknown as Senator;

const part = (
  label: string,
  weight: number,
  score: number,
  combines: "mean" | "points" = "mean"
) => ({
  label,
  weight,
  score,
  detail: `${label} detail`,
  combines,
});

// As the API serves them: approval in points, each total with its sum, a
// Legislative Effectiveness that today's records would score differently.
const breakdown: RepresentationScoreBreakdown = {
  constituentAlignment: {
    score: 86,
    sum: 85.6,
    clamped: false,
    basis: "scored",
    sinceScored: null,
    components: [
      part("Seat-relative vote alignment", 0.7, 88),
      part("Position congruence", 0.3, 56.2),
      { ...part("Constituent approval", 0.3, 73.7, "points"), points: 7.1 },
    ],
  },
  legislativeEffectiveness: {
    score: 75,
    sum: 75.2,
    clamped: false,
    basis: "scored",
    sinceScored: { score: 86, billsChanged: 1, scoredAt: "2026-10-16T05:12Z" },
    components: [part("Bills", 0.6, 74), part("Leadership", 0.25, 80), part("Coalition", 0.15, 70)],
  },
  fundingIndependence: {
    score: 0,
    sum: -2.5,
    clamped: true,
    basis: "scored",
    sinceScored: null,
    components: [part("PAC share", 1, -2.5)],
  },
};

function renderLedger() {
  const view = render(
    <MethodsLedger
      member={member}
      chamber="senate"
      breakdown={breakdown}
      scoredOn={{ date: "2026-10-16", algorithmVersion: "v6.35" }}
    />
  );
  fireEvent.click(screen.getByRole("button", { name: /Methods, data and citation/ }));
  return view;
}

describe("MethodsLedger", () => {
  it("shows no 0 to 100 score, weight or component table", () => {
    renderLedger();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Score/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Weight/)).not.toBeInTheDocument();
    expect(screen.queryByText(/out of 100|0 to 100/)).not.toBeInTheDocument();
  });

  it("states each column's source, period and the record behind it", () => {
    const withFacts = {
      ...breakdown,
      constituentAlignment: {
        ...breakdown.constituentAlignment,
        facts: { party: "D", partyVotes: 76, breaks: 6, breakRate: 0.08, expectedBreakRate: 0.05 },
      },
    } as RepresentationScoreBreakdown;
    render(<MethodsLedger member={member} chamber="senate" breakdown={withFacts} />);
    fireEvent.click(screen.getByRole("button", { name: /Methods, data and citation/ }));
    expect(screen.getByText("76 party-line votes · Full")).toBeInTheDocument();
    expect(screen.getAllByText("Period")).toHaveLength(3);
    expect(screen.getByText(/most recent completed election \(six years/)).toBeInTheDocument();
  });

  it("says when records changed after the nightly run, without a score", () => {
    renderLedger();
    const note = screen.getByText(/1 bill changed after the run/);
    expect(note.textContent).toContain("2026-10-16 05:12 UTC");
    expect(note.textContent).not.toMatch(/86/);
  });

  it("has no axe violations", async () => {
    const { container } = renderLedger();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
