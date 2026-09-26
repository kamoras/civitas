import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LineChart from "./LineChart";

const base = {
  title: "VISITORS",
  xLabels: ["Sep 24", "Sep 25", "Sep 26"],
  formatValue: (v: number) => `${v} v`,
};

describe("LineChart", () => {
  it("shows the empty message when every value is null", () => {
    render(
      <LineChart
        {...base}
        series={[{ key: "a", label: "A", color: "#000", values: [null, null, null] }]}
        emptyMessage="Nothing yet"
      />
    );
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("omits the legend for one series and shows it for two", () => {
    const { rerender } = render(
      <LineChart
        {...base}
        series={[{ key: "a", label: "Alpha", color: "#000", values: [1, 2, 3] }]}
      />
    );
    expect(screen.queryByRole("list", { name: /legend/ })).toBeNull();

    rerender(
      <LineChart
        {...base}
        series={[
          { key: "a", label: "Alpha", color: "#000", values: [1, 2, 3] },
          { key: "b", label: "Beta", color: "#111", values: [3, 2, 1] },
        ]}
      />
    );
    const legend = screen.getByRole("list", { name: /legend/ });
    expect(within(legend).getByText("Alpha")).toBeInTheDocument();
    expect(within(legend).getByText("Beta")).toBeInTheDocument();
  });

  it("reads points with the arrow keys, reporting gaps as no data", () => {
    render(
      <LineChart
        {...base}
        series={[{ key: "a", label: "Alpha", color: "#000", values: [5, null, 9] }]}
      />
    );
    const slider = screen.getByRole("slider");
    // Starts on the latest point.
    expect(slider).toHaveAttribute("aria-valuetext", "Sep 26: Alpha 9 v");
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    expect(slider).toHaveAttribute("aria-valuetext", "Sep 25: Alpha no data");
    fireEvent.keyDown(slider, { key: "Home" });
    expect(slider).toHaveAttribute("aria-valuetext", "Sep 24: Alpha 5 v");
  });

  it("lists every value in the table view", () => {
    render(
      <LineChart
        {...base}
        series={[{ key: "a", label: "Alpha", color: "#000", values: [5, null, 9] }]}
      />
    );
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4); // header + 3 points
    expect(within(rows[2]).getByText("—")).toBeInTheDocument();
    expect(within(rows[3]).getByText("9 v")).toBeInTheDocument();
  });
});
