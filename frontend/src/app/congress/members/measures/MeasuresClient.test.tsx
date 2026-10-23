import { describe, expect, it, vi } from "vitest";
import { olderApi } from "@/lib/olderApi";
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
      medianShown: "20%",
      axisMax: 0.5,
      axisStartShown: "0%",
      axisEndShown: "50%",
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

describe("an older API, during a rolling update", () => {
  it("draws the chamber strips without the API's axis", () => {
    const { container } = render(<MeasuresClient chamber="senate" data={olderApi(DATA)} />);
    expect(screen.getAllByRole("group").length).toBeGreaterThan(0);
    // Plain: drawn, with no readout, nothing printed from a missing field.
    expect(container.textContent).not.toMatch(/undefined|NaN/);
  });
});

describe("a whole-number measure", () => {
  it("is a dot histogram of party shapes, one per member", async () => {
    const bills = {
      ...DATA.measures[0],
      key: "billsMoved" as const,
      label: "Bills past committee",
      unit: "count" as const,
      median: 2,
      medianShown: "2",
      points: [
        { ...point("b", "Bea Ray", "R", 0), shown: "0 of 4" },
        { ...point("a", "Al Fox", "D", 2), shown: "2 of 9" },
        { ...point("c", "Cy Ng", "I", 2), shown: "2 of 3" },
      ],
    };
    const { container } = render(
      <MeasuresClient chamber="senate" data={{ ...DATA, measures: [bills] }} />
    );
    const chart = screen.getByRole("group", { name: /Bills past committee, every senator: 3/ });
    expect(chart.querySelectorAll("svg circle")).toHaveLength(1);
    expect(chart.querySelectorAll("svg rect")).toHaveLength(1);
    expect(chart.querySelectorAll("svg polygon")).toHaveLength(1);
    expect(screen.getByText("median 2")).toBeInTheDocument();
    const r = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("votes with the president", () => {
  it("is a row with its own rule, a readout of range and expected rate, and the full page linked", async () => {
    const context =
      "Range 55% to 87% · seat's expected rate 20% · more than the seat's expected rate";
    const support = {
      ...DATA.measures[0],
      key: "presidentSupport" as const,
      label: "Votes with the president",
      ruleText: "Its own word rule, not the percentile one.",
      rule: { kind: "interval" as const, interval: 95, minRecord: 20 },
      median: null,
      medianShown: null,
      partyMedians: [
        { party: "D", value: 0.2, n: 1, shown: "Democrats' median 20%" },
        { party: "R", value: 0.1, n: 1, shown: "Republicans' median 10%" },
      ],
      points: [
        { ...point("b", "Bea Ray", "R", 0.1), shown: "10%, 2 of 20 votes", context: "Range a" },
        { ...point("a", "Al Fox", "D", 0.2), shown: "20%, 4 of 20 votes", context: "Range b" },
        { ...point("c", "Cy Ng", "I", 0.5), shown: "74%, 20 of 27 votes", context },
      ],
    };
    const data = {
      ...DATA,
      measures: [support],
      members: DATA.members.map((m) => ({ ...m, withPresident: 20, presidentVotes: 27 })),
    };
    render(<MeasuresClient chamber="house" data={data} />);
    expect(screen.getByText("Its own word rule, not the percentile one.")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Every vote and the president's position" })
    ).toHaveAttribute("href", "/congress/president/house");
    expect(screen.getAllByText("20 / 27")).toHaveLength(3);
    // Each party's median, labelled; no chamber median between them.
    expect(screen.getByText("Democrats' median 20%")).toBeInTheDocument();
    expect(screen.getByText("Republicans' median 10%")).toBeInTheDocument();
    expect(screen.queryByText(/^Median /)).toBeNull();
    screen.getByRole("group", { name: /Votes with the president, every representative/ }).focus();
    await userEvent.keyboard("{End}");
    expect(
      screen.getByText(new RegExp(`Cy Ng, I-OR-AL: 74%, 20 of 27 votes, ${context}`))
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/peers/);
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});
