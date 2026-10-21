import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import MeasuresClient from "./MeasuresClient";
import type { ChamberMeasures } from "@/types/peers";

vi.mock("@/components/layout/Navbar", () => ({ default: () => null }));
vi.mock("@/components/layout/Footer", () => ({ default: () => null }));

const point = (id: string, name: string, party: string, value: number) => ({
  id,
  name,
  party,
  state: "OR",
  district: null,
  value,
  shown: `${Math.round(value * 100)}%`,
  expected: 0.2,
  gap: value - 0.2,
  rank: value === 0.5 ? "Highest of 3" : value === 0.1 ? "Lowest of 3" : "2nd highest of 3",
});

const member = (id: string, name: string, party: string, state: string, pac: number) => ({
  id,
  name,
  party,
  caucus: party,
  state,
  district: null,
  lean: null,
  smallGifts: 0.1,
  pacMoney: pac,
  breaks: 1,
  partyVotes: 100,
  billsPastCommittee: 2,
  bills: 10,
  passed: 1,
  law: 0,
});

const DATA: ChamberMeasures = {
  chamber: "senate",
  measures: [
    {
      key: "pacMoney",
      label: "Money from PACs",
      question: "Who funds the campaign?",
      why: "Share of campaign contributions from PACs.",
      direction: "higher means more of the money came from PACs",
      unit: "share",
      median: 0.2,
      n: 3,
      source: { name: "FEC filings", url: "https://www.fec.gov/data/" },
      points: [
        point("b", "Bea Ray", "R", 0.1),
        point("a", "Al Fox", "D", 0.2),
        point("c", "Cy Ng", "I", 0.5),
      ],
      mostAbove: [point("c", "Cy Ng", "I", 0.5)],
      mostBelow: [point("b", "Bea Ray", "R", 0.1)],
    },
  ],
  members: [
    member("a", "Al Fox", "D", "OR", 0.2),
    member("b", "Bea Ray", "R", "WY", 0.1),
    member("c", "Cy Ng", "I", "VT", 0.5),
  ],
};

describe("MeasuresClient", () => {
  it("lists the chamber alphabetically, sorts by a fact, filters, and ranks no one", async () => {
    render(<MeasuresClient chamber="senate" data={DATA} />);
    expect(
      screen.getByRole("heading", { level: 1, name: "The Senate by the numbers" })
    ).toBeInTheDocument();
    expect(screen.getByText("Furthest above expected")).toBeInTheDocument();
    const table = screen.getByRole("table");
    const names = () =>
      within(table)
        .getAllByRole("link")
        .map((a) => a.textContent);
    expect(names()).toEqual(["Al Fox", "Bea Ray", "Cy Ng"]);
    await userEvent.click(within(table).getByRole("button", { name: "From PACs" }));
    expect(names()).toEqual(["Cy Ng", "Al Fox", "Bea Ray"]);
    expect(within(table).getByRole("columnheader", { name: /From PACs/ })).toHaveAttribute(
      "aria-sort",
      "descending"
    );
    await userEvent.selectOptions(screen.getByLabelText("Party"), "R");
    expect(names()).toEqual(["Bea Ray"]);
    expect(document.body.textContent).not.toMatch(/#\d+ of \d+|ranked/i);
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});
