import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import JusticeProfile from "./JusticeProfile";
import { stripDomain } from "./JusticeFigures";
import type { Justice, JusticeMeasure } from "@/types/justice";

const peers = [
  { id: "john_doe", name: "John Doe", value: 0.7, shown: "70%" },
  { id: "jane_q_example", name: "Jane Q. Example", value: 0.83, shown: "83%" },
  { id: "richard_r_roe", name: "Richard R. Roe", value: 0.9, shown: "90%" },
];

const majority: JusticeMeasure = {
  key: "majority",
  question: "How often on the winning side?",
  label: "In the majority",
  why: "A justice often in dissent is outvoted more than most.",
  unit: "share",
  value: 0.83,
  shown: "83%",
  record: 117,
  peers,
  median: 0.83,
  medianShown: "83%",
  below: 1,
  sentence: "In the majority in 83% of 117 cases, in dissent in 15%.",
  source: { name: "Supreme Court Database and Oyez", url: "http://scdb.la.psu.edu" },
};

// A justice as the profile API serves one: the record, and each figure
// beside the sitting justices'.
const justice: Justice = {
  id: "jane_q_example",
  name: "Jane Q. Example",
  lastName: "Example",
  roleTitle: "Associate Justice of the Supreme Court of the United States",
  appointingPresident: "A. President",
  appointingParty: "R",
  dateStart: "2006-01-31",
  isActive: true,
  thumbnailUrl: null,
  score: { loyalty: null, overall: null },
  casesDecided: 117,
  majorityPct: 82.9,
  dissentPct: 15.4,
  unanimousPct: 49.6,
  authoredMajority: 7,
  authoredDissent: 7,
  authoredConcurrence: 6,
  closeCaseMajorityPct: 46.2,
  agreement: [
    { id: "john_doe", name: "John Doe", share: 91.2 },
    { id: "richard_r_roe", name: "Richard R. Roe", share: 88.0 },
  ],
  loyalty: null,
  idealPoints: [[2024, 2.5]],
  figures: {
    appointedBy: { id: "a-president-1", name: "A. President", party: "R", year: 2006 },
    peers: 3,
    terms: [2022, 2023, 2024, 2025],
    measures: [majority],
    appointer: {
      rows: [
        { id: "john_doe", name: "John Doe", estimate: -0.02, ciLow: -0.1, ciHigh: 0.06 },
        {
          id: "jane_q_example",
          name: "Jane Q. Example",
          estimate: 0.19,
          ciLow: 0.07,
          ciHigh: 0.31,
        },
      ],
      sentence: "With the government's side held fixed, 19.2 points more often.",
      throughTerm: 2025,
    },
  },
};

function page(j: Justice = justice) {
  return render(
    <main>
      <JusticeProfile justice={j} />
    </main>
  );
}

describe("JusticeProfile", () => {
  it("links the appointing president's page, with the year, and shows no score", () => {
    page();
    expect(screen.getByRole("heading", { level: 1, name: "Jane Q. Example" })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Appointed by\s*A\. President.*2006/ });
    expect(link).toHaveAttribute("href", "/politicians/a-president-1");
    expect(document.body.textContent).not.toMatch(/scored|scorecard|\bscore\b/i);
  });

  it("draws each figure beside every sitting justice, with a caption and a table", () => {
    page();
    const figure = screen.getByRole("figure", { name: /In the majority in 83% of 117 cases/ });
    expect(within(figure).getByText("A justice often in dissent is outvoted more than most."));
    const table = within(figure).getByRole("table", { name: /each sitting justice/ });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByRole("link", { name: "Richard R. Roe" })).toHaveAttribute(
      "href",
      "/politicians/richard_r_roe"
    );
  });

  it("shows the appointer estimate beside every justice's interval, with its caveat", () => {
    page();
    expect(screen.getByRole("figure", { name: /19\.2 points more often/ })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "+7.0 to +31.0" })).toBeInTheDocument();
    expect(screen.getByText(/Not a measure of loyalty/)).toBeInTheDocument();
  });

  it("links each colleague on the agreement chart", () => {
    page();
    const section = screen.getByRole("region", { name: "Agreement with each justice" });
    expect(within(section).getByRole("link", { name: "John Doe" })).toHaveAttribute(
      "href",
      "/politicians/john_doe"
    );
  });

  it("renders a response cached before the figures shipped", () => {
    page({ ...justice, figures: undefined, agreement: undefined });
    expect(screen.queryByRole("figure")).not.toBeInTheDocument();
    // The appointing president is still named, without a link to guess.
    expect(screen.getByText("A. President")).toBeInTheDocument();
  });

  it("has no structural accessibility violations", async () => {
    page();
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("stripDomain", () => {
  it("reads each measure on its own scale", () => {
    expect(stripDomain({ unit: "share", peers })).toEqual([0, 1]);
    expect(stripDomain({ unit: "count", peers: [{ ...peers[0], value: 12 }] })).toEqual([0, 15]);
    expect(stripDomain({ unit: "position", peers: [{ ...peers[0], value: -2.4 }] })).toEqual([
      -3, 3,
    ]);
  });
});
