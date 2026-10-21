import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import axe from "axe-core";
import StageLadder from "./StageLadder";

const data = {
  total: 19161,
  milestones: [
    { label: "Introduced", stage: "INTRODUCED", bills: 19161, share: 1, shareText: "100%" },
    {
      label: "Reported by a committee",
      stage: "REPORTED",
      bills: 2453,
      share: 0.128,
      shareText: "13%",
    },
    {
      label: "Passed one chamber",
      stage: "PASSED_CHAMBER",
      bills: 1640,
      share: 0.0856,
      shareText: "9%",
    },
    {
      label: "Passed both chambers",
      stage: "PASSED_BOTH",
      bills: 176,
      share: 0.0092,
      shareText: "0.9%",
    },
    { label: "Became law", stage: "ENACTED", bills: 119, share: 0.0062, shareText: "0.6%" },
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

  // Each step opens the bills that got at least that far (?reached=), named
  // like /congress's rows; this bill's mark stays outside its link.
  it("links each step to its bills and keeps this bill's mark out of the link", async () => {
    render(
      <main>
        <StageLadder data={data} />
      </main>
    );
    const link = screen.getByRole("link", { name: "Passed one chamber: 1,640 bills" });
    expect(link).toHaveAttribute("href", "/congress/bills?reached=PASSED_CHAMBER");
    expect(within(link).queryByText("This bill")).not.toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(5);
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  // The steps are cumulative ("Introduced" lists every bill), so a bill in
  // committee also links the bills at exactly its stage, counted by the API.
  it("links the other bills at exactly this bill's stage", () => {
    render(
      <StageLadder
        data={{
          ...data,
          reached: 0,
          stage: "IN_COMMITTEE",
          stageLabel: "In Committee",
          stageOthers: 804,
        }}
      />
    );
    const link = screen.getByRole("link", { name: "Other bills still at this stage (804)" });
    expect(link).toHaveAttribute("href", "/congress/bills?stage=IN_COMMITTEE");
    expect(screen.getByText(/In Committee/)).toBeInTheDocument();
  });

  it("offers no such link once the outcome is known", () => {
    render(<StageLadder data={{ ...data, reached: 4, stage: "ENACTED", stageOthers: null }} />);
    expect(screen.queryByText(/Other bills still/)).not.toBeInTheDocument();
  });
});
