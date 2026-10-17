import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import AgreementHeatmap from "./AgreementHeatmap";
import { court } from "./court.fixture";

describe("AgreementHeatmap", () => {
  it("is a grid with one tab stop, a value label in every pair's cell and its counts in its name", () => {
    render(<AgreementHeatmap court={court()} />);
    const grid = screen.getByRole("grid", { name: "Who votes with whom" });
    const cells = within(grid).getAllByRole("gridcell");
    expect(cells).toHaveLength(9);
    expect(cells.filter((c) => c.tabIndex === 0)).toHaveLength(1);
    // Not colour alone: the share is written in the cell.
    const pair = within(grid).getByRole("gridcell", {
      name: "50%: Ames and Cole voted the same way in 2 of the 4 cases both decided (50%)",
    });
    expect(pair).toHaveTextContent("50");
    expect(within(grid).getAllByRole("columnheader")).toHaveLength(3);
    expect(within(grid).getAllByRole("rowheader")).toHaveLength(3);
  });

  it("moves focus cell to cell with the arrow keys and states the focused pair", () => {
    render(<AgreementHeatmap court={court()} />);
    const grid = screen.getByRole("grid");
    const start = within(grid)
      .getAllByRole("gridcell")
      .find((c) => c.tabIndex === 0)!;
    start.focus();
    expect(start).toHaveAccessibleName(/Ames and Brook/);
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(document.activeElement).toHaveAccessibleName(/Ames and Cole/);
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    expect(document.activeElement).toHaveAccessibleName(
      /Brook and Cole voted the same way in 3 of the 3/
    );
    fireEvent.keyDown(grid, { key: "Home" });
    expect(document.activeElement).toHaveAccessibleName(/Brook and Ames/);
    fireEvent.keyDown(grid, { key: "End", ctrlKey: true });
    expect(document.activeElement).toHaveAccessibleName("Cole: the same justice");
    // The roving stop follows focus.
    expect((document.activeElement as HTMLElement).tabIndex).toBe(0);
    expect(screen.getByText("Cole: the same justice.")).toBeInTheDocument();
  });

  it("lists every pair as a plain table, most agreement first", () => {
    render(<AgreementHeatmap court={court()} />);
    const table = screen.getByRole("table", { name: /most first/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "Brook and Cole33100.0%",
      "Ames and Brook3475.0%",
      "Ames and Cole2450.0%",
    ]);
  });

  it("passes axe", async () => {
    render(
      <main>
        <AgreementHeatmap court={court()} />
      </main>
    );
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("says so when no pair has a case", () => {
    render(<AgreementHeatmap court={court({ agreement: [] })} />);
    expect(screen.queryByRole("grid")).toBeNull();
    expect(screen.getByText(/No decided case with two sitting justices/)).toBeInTheDocument();
  });
});
