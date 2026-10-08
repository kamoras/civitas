import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ScoreTrend from "./ScoreTrend";

// A methodology change between the first snapshot and the last: 75.2 under
// no recorded version, 51.7 under v6.30.
const snapshots = [
  { date: "2026-04-27", overallScore: 75.2, algorithmVersion: null, scores: {} },
  { date: "2026-09-29", overallScore: 50.0, algorithmVersion: "v6.30", scores: {} },
  { date: "2026-10-05", overallScore: 51.7, algorithmVersion: "v6.30", scores: {} },
];

describe("ScoreTrend", () => {
  it("states the API's change on the current method, not the change since the first snapshot", () => {
    render(<ScoreTrend snapshots={snapshots} change={{ since: "2026-09-29", points: 1.7 }} />);
    expect(screen.getByText("↑ +1.7 since Sep 29, 2026")).toBeInTheDocument();
    expect(screen.queryByText(/-23|since first snapshot/)).not.toBeInTheDocument();
  });

  it("says so when nothing on the current method came before", () => {
    render(<ScoreTrend snapshots={snapshots} change={null} />);
    expect(screen.getByText("no earlier score on the current method")).toBeInTheDocument();
  });
});
