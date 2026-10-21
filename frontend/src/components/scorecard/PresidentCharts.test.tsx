import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import userEvent from "@testing-library/user-event";
import PresidentChartsSection from "./PresidentCharts";
import type { PresidentCharts } from "@/types/presidentCharts";

const data: PresidentCharts = {
  minBand: 5,
  charts: [
    {
      key: "approval",
      kind: "line",
      title: "Approval through the term",
      why: "How the public's approval moved over the term.",
      xLabel: "Month of the term",
      yLabel: "Approve",
      window: "Polls averaged by month of the term.",
      source: { name: "American Presidency Project", url: "https://www.presidency.ucsb.edu/" },
      caption: "Approval in month 2 of the term: 40%.",
      yMin: 35,
      yMax: 70,
      yTop: "70%",
      yBottom: "35%",
      n: 2,
      series: {
        id: "doe-47",
        name: "Jane Doe",
        points: [
          [0, 45],
          [1, 40],
        ],
      },
      others: [
        {
          id: "roe-40",
          name: "John Roe",
          points: [
            [0, 60],
            [1, 70],
          ],
        },
      ],
      band: [],
      xMax: 1,
      table: [
        { x: 0, value: "45%", band: null },
        { x: 1, value: "40%", band: "50% to 65% (n=6)" },
      ],
      marks: [
        { id: "0", label: "Month 1", detail: "Jane Doe", shown: "45%" },
        {
          id: "1",
          label: "Month 2",
          detail: "Jane Doe",
          shown: "40%",
          context: "other presidencies' middle half 50% to 65% (n=6)",
        },
      ],
    },
    {
      key: "growth",
      kind: "bars",
      title: "Growth in each year of the term",
      why: "How fast the economy grew each calendar year of the term.",
      xLabel: "Year",
      yLabel: "Real GDP growth",
      window: "Calendar years of the term.",
      source: { name: "MeasuringWorth", url: "https://www.measuringworth.com/" },
      caption: "Real growth in each year of the term ran from -2.6% to 2.9%.",
      yMin: -2.6,
      yMax: 4.6,
      yTop: "4.6%",
      yBottom: "-2.6%",
      n: 13,
      bars: [
        { label: "2009", value: -2.6, shown: "-2.6%", first: true },
        { label: "2010", value: 2.9, shown: "2.9%", first: false },
      ],
      eraBand: { low: 2.1, median: 3.2, high: 4.6, years: 65, presidencies: 13 },
      bandShown: "middle half 2.1% to 4.6%, median 3.2%",
      marks: [
        {
          id: "2009",
          label: "2009",
          shown: "-2.6% real growth",
          context: "first year of the term",
        },
        { id: "2010", label: "2010", shown: "2.9% real growth" },
      ],
    },
  ],
};

describe("PresidentChartsSection", () => {
  it("draws each chart with its caption, source and a table in words", async () => {
    render(
      <main>
        <PresidentChartsSection data={data} />
      </main>
    );
    const approval = screen.getByRole("figure", { name: /Approval in month 2/ });
    expect(
      within(approval).getByRole("link", { name: "American Presidency Project" })
    ).toBeInTheDocument();
    expect(within(approval).getByRole("cell", { name: "50% to 65% (n=6)" })).toBeInTheDocument();
    const growth = screen.getByRole("figure", { name: /Real growth/ });
    expect(within(growth).getByRole("cell", { name: "-2.6% (first year)" })).toBeInTheDocument();
    // Never a causal claim.
    expect(screen.queryByText(/because of|caused|thanks to/i)).not.toBeInTheDocument();
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("draws every election, this presidency's marked, with both sources", () => {
    render(
      <PresidentChartsSection
        data={{
          minBand: 5,
          charts: [
            {
              key: "elections",
              kind: "elections",
              title: "Elections won",
              why: "How decisively the country chose the president.",
              xLabel: "Election",
              yLabel: "Popular margin",
              window: "Every presidential election since 1789.",
              source: {
                name: "National Archives (electoral votes)",
                url: "https://www.archives.gov/",
              },
              source2: {
                name: "American Presidency Project (popular margins)",
                url: "https://x.org/",
              },
              caption: "Won 1 presidential election. 1840: 234 of 294 electoral votes (79.6%).",
              yMin: -3,
              yMax: 6.1,
              yTop: "+6",
              yBottom: "-3",
              n: 1,
              medianShare: 68.6,
              medianShareShown: "68.6%",
              medianMargin: 1.6,
              marks: [
                {
                  id: "1824",
                  label: "1824",
                  detail: "John Roe",
                  shown: "84 of 261 electoral votes (32.2%)",
                  href: "/politicians/roe-6",
                },
                {
                  id: "1840",
                  label: "1840",
                  detail: "Jane Doe",
                  shown: "234 of 294 electoral votes (79.6%)",
                  href: "/politicians/doe-9",
                },
              ],
              rows: [
                {
                  year: 1824,
                  president: "John Roe",
                  presidentId: "roe-6",
                  evShare: 32.2,
                  evShown: "84 of 261 (32.2%)",
                  popularMargin: null,
                  marginShown: "not tabulated",
                  mine: false,
                  decidedByHouse: true,
                },
                {
                  year: 1840,
                  president: "Jane Doe",
                  presidentId: "doe-9",
                  evShare: 79.6,
                  evShown: "234 of 294 (79.6%)",
                  popularMargin: 6.1,
                  marginShown: "+6.1",
                  mine: true,
                  decidedByHouse: false,
                },
              ],
            },
          ],
        }}
      />
    );
    const fig = screen.getByRole("figure", { name: /Won 1 presidential election/ });
    expect(
      within(fig).getByRole("rowheader", { name: "1840 (this presidency)" })
    ).toBeInTheDocument();
    expect(
      within(fig).getByRole("cell", { name: "84 of 261 (32.2%), decided by the House" })
    ).toBeInTheDocument();
    expect(within(fig).getByRole("link", { name: /National Archives/ })).toBeInTheDocument();
    expect(
      within(fig).getByRole("link", { name: /American Presidency Project/ })
    ).toBeInTheDocument();
  });

  it("reads each point, bar and election from the keyboard, starting at the latest", async () => {
    render(
      <main>
        <PresidentChartsSection data={data} />
      </main>
    );
    const line = screen.getByRole("group", {
      name: /^Approval through the term, Jane Doe: 2 marks/,
    });
    line.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(
      within(line).getByText(
        "Month 2, Jane Doe: 40%, other presidencies' middle half 50% to 65% (n=6)."
      )
    ).toBeInTheDocument();
    const bars = screen.getByRole("group", { name: /^Growth in each year of the term: 2 marks/ });
    expect(bars.querySelectorAll("[data-mark]")).toHaveLength(2);
    bars.focus();
    await userEvent.keyboard("{Home}");
    expect(
      within(bars).getByText("2009: -2.6% real growth, first year of the term.")
    ).toBeInTheDocument();
  });

  it("draws nothing without a chart", () => {
    const { container } = render(<PresidentChartsSection data={{ minBand: 5, charts: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
