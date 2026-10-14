import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ScoreSpreadStrip from "./ScoreSpreadStrip";
import type { ScoreSpread } from "@/types/senator";

afterEach(cleanup);

const spread: ScoreSpread = {
  chamber: "senate",
  total: 6,
  counts: [
    { score: 52, members: 1 },
    { score: 60, members: 3 },
    { score: 71, members: 2 },
  ],
  p25: 58,
  median: 60,
  p75: 68,
  parties: { D: 3, R: 3, I: 0 },
};

describe("ScoreSpreadStrip", () => {
  it("draws one square per member and states the backend's figures", () => {
    const { container } = render(<ScoreSpreadStrip spread={spread} highlight={null} />);
    // Six members plus the middle-half bracket.
    expect(container.querySelectorAll("svg rect")).toHaveLength(7);
    expect(screen.getByText("Middle half: 58–68 · median 60")).toBeTruthy();
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe(
      "6 senators on 3 different scores, from 52 to 71; half score between 58 and 68."
    );
  });

  it("names the highlighted row's score and the members sharing it", () => {
    render(<ScoreSpreadStrip spread={spread} highlight={60} />);
    expect(screen.getByText(/3 senators show 60 and share one rank/)).toBeTruthy();
  });

  it("draws nothing for an empty chamber", () => {
    const empty = { ...spread, total: 0, counts: [], p25: null, median: null, p75: null };
    const { container } = render(<ScoreSpreadStrip spread={empty} highlight={null} />);
    expect(container.innerHTML).toBe("");
  });
});
