import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StageLadder from "./StageLadder";

const data = {
  total: 19161,
  milestones: [
    { label: "Introduced", bills: 19161, share: 1, shareText: "100%" },
    { label: "Reported by a committee", bills: 2453, share: 0.128, shareText: "13%" },
    { label: "Passed one chamber", bills: 1640, share: 0.0856, shareText: "9%" },
    { label: "Passed both chambers", bills: 176, share: 0.0092, shareText: "0.9%" },
    { label: "Became law", bills: 119, share: 0.0062, shareText: "0.6%" },
  ],
  reached: 2,
};

describe("StageLadder", () => {
  it("marks how far the bill got beside how far bills get", () => {
    render(<StageLadder data={data} />);
    const here = screen.getByText("Passed one chamber").closest("li")!;
    expect(here).toHaveAttribute("aria-current", "step");
    expect(within(here).getByText("This bill")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("0.6%")).toBeInTheDocument();
    expect(screen.getByText(/Share of the 19,161 bills/)).toBeInTheDocument();
  });
});
