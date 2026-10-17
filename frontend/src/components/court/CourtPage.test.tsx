import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import CourtLine from "./CourtLine";
import DecisionLineups from "./DecisionLineups";
import RecentDecisions from "./RecentDecisions";
import { court, justice } from "./court.fixture";

describe("CourtLine", () => {
  it("lists the justices left to right with the median marked and each estimate in words", () => {
    render(<CourtLine court={court()} />);
    const table = screen.getByRole("table", { name: /left to right by Martin-Quinn position/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("BrookMedian+0.30 (−0.20 to +0.80)");
    expect(within(rows[0]).getByRole("link", { name: "Ames" })).toHaveAttribute(
      "href",
      "/politicians/Ames"
    );
  });

  it("names a justice with no position yet instead of guessing one", () => {
    render(<CourtLine court={court({ justices: [...court().justices, justice("Dale", null)] })} />);
    expect(screen.getByText(/No position for the 2024 term yet/)).toHaveTextContent("Dale");
  });

  it("says so when no position is stored", () => {
    render(
      <CourtLine court={court({ justices: [justice("Ames", null)], median: [], mqTerm: null })} />
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/No Martin-Quinn position is stored yet/)).toBeInTheDocument();
  });
});

describe("DecisionLineups", () => {
  it("states every term's splits in a table", () => {
    render(<DecisionLineups court={court()} />);
    const table = screen.getByRole("table");
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((c) => c.textContent)
    ).toEqual(["Term", "3–0", "2–1", "Cases"]);
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("2024112");
  });
});

describe("RecentDecisions", () => {
  const hrefFor = (p: number) => `/court?page=${p}#decisions`;

  it("names the majority, the dissent and the author, and links the sources that exist", async () => {
    const c = court();
    render(
      <main>
        <RecentDecisions page={c.decisions} justices={c.justices} hrefFor={hrefFor} />
      </main>
    );
    expect(screen.getByText(/Majority:/).parentElement).toHaveTextContent(
      "Majority: Brook, Cole · Dissent: Ames · Opinion of the Court: Cole"
    );
    expect(screen.getByRole("link", { name: "Oyez" })).toHaveAttribute(
      "href",
      "https://www.oyez.org/cases/2025/25-1"
    );
    expect(screen.getByRole("link", { name: "In Explore" })).toHaveAttribute("href", "/explore/7");
    expect(screen.queryByRole("link", { name: /Opinion/ })).toBeNull();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("pages by link", () => {
    const c = court();
    render(
      <RecentDecisions
        page={{ ...c.decisions, total: 25, page: 2 }}
        justices={c.justices}
        hrefFor={hrefFor}
      />
    );
    expect(screen.getByRole("link", { name: /Newer/ })).toHaveAttribute(
      "href",
      "/court?page=1#decisions"
    );
    expect(screen.getByRole("link", { name: /Older/ })).toHaveAttribute(
      "href",
      "/court?page=3#decisions"
    );
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  });

  it("says so when nothing is decided yet", () => {
    render(
      <RecentDecisions
        page={{ total: 0, page: 1, perPage: 10, decisions: [] }}
        justices={[]}
        hrefFor={hrefFor}
      />
    );
    expect(screen.getByText("No decision is stored yet.")).toBeInTheDocument();
  });
});
