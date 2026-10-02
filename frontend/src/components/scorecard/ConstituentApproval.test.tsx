import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ConstituentApproval from "./ConstituentApproval";

const approval = {
  survey: "CES 2024 Common Content (pre-election wave, Oct-Nov 2024)",
  fielded: "2024-10/2024-11",
  surveyedAs: "Ruth Pryor",
  byParty: [
    { party: "D" as const, approve: 0.2283, ownWeight: 0.72, respondents: 88 },
    { party: "R" as const, approve: 0.5675, ownWeight: 0.31, respondents: 72 },
    { party: "I" as const, approve: null, respondents: 12 },
  ],
};

describe("ConstituentApproval", () => {
  it("shows each party's approval with how many gave an opinion", () => {
    render(<ConstituentApproval approval={approval} />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toMatch(/Democrats.*23% approve.*88 with an opinion/);
    expect(items[1]).toMatch(/Republicans.*57% approve/);
  });

  it("marks a figure that comes mostly from similar members", () => {
    render(<ConstituentApproval approval={approval} />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).not.toMatch(/similar members/);
    expect(items[1]).toMatch(/mostly based on similar members/);
  });

  it("says a group can't be measured rather than showing a pooled number", () => {
    render(<ConstituentApproval approval={approval} />);
    expect(screen.getByText(/too few respondents to measure/)).toBeTruthy();
  });

  it("names the survey and the member it asked about, and says it isn't scored", () => {
    render(<ConstituentApproval approval={approval} />);
    expect(screen.getByText(/rating .Ruth Pryor.\. Informational, not scored\./)).toBeTruthy();
  });

  it("renders nothing without a reading", () => {
    const { container } = render(<ConstituentApproval approval={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
