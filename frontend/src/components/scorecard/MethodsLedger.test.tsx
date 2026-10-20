import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import MethodsLedger from "./MethodsLedger";
import { HeldScoreNote, HeldScoreTag, ThinDataNote, heldScoreText } from "./HeldScore";
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
    confidence: {},
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
      weights={{
        fundingIndependence: 0.4,
        constituentAlignment: 0.35,
        legislativeEffectiveness: 0.25,
      }}
      scoredOn={{ date: "2026-10-16", algorithmVersion: "v6.35" }}
    />
  );
  fireEvent.click(screen.getByRole("button", { name: /Methods, data and citation/ }));
  return view;
}

describe("MethodsLedger arithmetic", () => {
  it("shows a points part as the points it adds, not as a weight", () => {
    renderLedger();
    const row = screen.getByRole("rowheader", { name: "Constituent approval" }).closest("tr")!;
    const weight = (row as HTMLTableRowElement).cells[1].textContent ?? "";
    expect(weight.startsWith("+7.1 pts")).toBe(true);
    expect(weight).not.toMatch(/^30%/);
  });

  it("totals each part with its sum before rounding", () => {
    renderLedger();
    expect(screen.getByText(/plus the points shown/).textContent).toContain(
      "85.6, shown rounded as 86"
    );
  });

  it("says where the 0 to 100 clamp applied", () => {
    renderLedger();
    expect(screen.getByText(/held at 0/).textContent).toContain("−2.5");
  });

  it("says when today's records would score a part differently", () => {
    renderLedger();
    const note = screen.getByText(/Updated since this score/).closest("p")!;
    expect(note.textContent).toContain("1 bill changed after the run of 2026-10-16 05:12 UTC");
    expect(note.textContent).toContain("would be 86");
  });

  it("has no axe violations", async () => {
    const { container } = renderLedger();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});

describe("held and thin-data marks", () => {
  const held = {
    failedOn: "2026-10-16",
    source: "FEC filings",
    scoredOn: "2026-10-14",
    algorithmVersion: "v6.34",
  };

  it("names the run kept and what the latest run couldn't read", () => {
    expect(heldScoreText(held)).toBe(
      "Score from the 2026-10-14 run (method v6.34): the 2026-10-16 run couldn't read FEC filings, so other members' scores may be newer."
    );
    expect(heldScoreText({ ...held, source: null })).toContain("couldn't score this member");
  });

  it("gives the tag's full sentence to screen readers", () => {
    render(<HeldScoreTag held={held} />);
    expect(screen.getByText(/Score from the 2026-10-14 run/)).toHaveClass("sr-only");
    expect(screen.getByText(/^Held/).textContent).toBe("Held · 10-14");
  });

  it("shows nothing when the score isn't held or the data isn't thin", () => {
    const { container } = render(
      <>
        <HeldScoreNote held={null} />
        <ThinDataNote dims={[]} />
      </>
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("names the thin parts in plain words", () => {
    render(<ThinDataNote dims={["legislativeEffectiveness"]} />);
    expect(
      screen.getByText(/Little data is behind Legislative Effectiveness, so that part is/)
    ).toBeInTheDocument();
  });
});
