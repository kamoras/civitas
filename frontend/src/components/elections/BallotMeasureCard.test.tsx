import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import BallotMeasureCard from "./BallotMeasureCard";
import type { BallotMeasure } from "@/types/election";

function measure(overrides: Partial<BallotMeasure>): BallotMeasure {
  return {
    id: "TX-2026-11-03-3",
    state: "TX",
    electionDate: "2026-11-03",
    electionType: "general",
    number: "3",
    title: "Proposition 3",
    measureType: null,
    origin: "Texas Legislature",
    status: "certified",
    officialTitle: null,
    officialSummary: "The constitutional amendment to do a thing.",
    fiscalImpact: null,
    yesMeans: null,
    noMeans: null,
    titleAuthority: "Texas Legislature",
    fiscalAuthority: null,
    sourceName: "Texas Legislative Reference Library",
    sourceUrl: "https://lrl.texas.gov/",
    asOf: "2026-09-28T00:00:00Z",
    ...overrides,
  };
}

describe("BallotMeasureCard", () => {
  it("never presents the display label as an official ballot title", () => {
    render(<BallotMeasureCard measure={measure({})} />);
    // The label still shows, as the card's heading line...
    expect(screen.getByText("Proposition 3")).toBeInTheDocument();
    // ... but not under "OFFICIAL BALLOT TITLE".
    expect(screen.queryByText("OFFICIAL BALLOT TITLE")).not.toBeInTheDocument();
  });

  it("names the drafter beside the summary when there is no official title", () => {
    render(<BallotMeasureCard measure={measure({})} />);
    const summary = screen.getByText("OFFICIAL SUMMARY").closest("section");
    expect(summary).toHaveTextContent("Drafted by Texas Legislature");
    expect(screen.getAllByText(/Drafted by/)).toHaveLength(1);
  });

  it("names the drafter beside the official title when the state publishes one", () => {
    render(
      <BallotMeasureCard
        measure={measure({
          officialTitle: "BUDGET STABILIZATION FUND",
          titleAuthority: "Florida Legislature",
        })}
      />
    );
    const title = screen.getByText("OFFICIAL BALLOT TITLE").closest("section");
    expect(title).toHaveTextContent("BUDGET STABILIZATION FUND");
    expect(title).toHaveTextContent("Drafted by Florida Legislature");
    expect(screen.getAllByText(/Drafted by/)).toHaveLength(1);
  });
});
