import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import JusticeScorecard from "./JusticeScorecard";
import type { Justice } from "@/types/justice";

// Samuel A. Alito, Jr. as the local API served it on 2026-09-28, from the
// 2026 Supreme Court Database release.
const justice: Justice = {
  id: "samuel_a_alito_jr",
  name: "Samuel A. Alito, Jr.",
  lastName: "Alito",
  roleTitle: "Associate Justice of the Supreme Court of the United States",
  appointingPresident: "George W. Bush",
  appointingParty: "R",
  dateStart: "2006-01-31",
  isActive: true,
  thumbnailUrl: null,
  score: { loyalty: 16.1, overall: 16.1 },
  casesDecided: 117,
  majorityPct: 82.9,
  dissentPct: 15.4,
  unanimousPct: 49.6,
  authoredMajority: 7,
  authoredDissent: 7,
  authoredConcurrence: 6,
  closeCaseMajorityPct: 46.2,
  agreement: [
    { id: "clarence_thomas", name: "Clarence Thomas", share: 91.2 },
    { id: "brett_m_kavanaugh", name: "Brett M. Kavanaugh", share: 88.0 },
  ],
  loyalty: {
    estimate: 0.1434,
    se: 0.0501,
    votesIn: 56,
    votesOut: 399,
    rateIn: 0.75,
    rateOut: 0.5714,
    throughTerm: 2025,
  },
  idealPoints: [
    [2005, 1.42],
    [2024, 2.5],
  ],
};

describe("JusticeScorecard", () => {
  it("states the loyalty estimate, its range and the rates behind it", () => {
    render(
      <main>
        <JusticeScorecard justice={justice} rank={{ rank: 9, of: 9 }} />
      </main>
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Samuel A. Alito, Jr." })
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#9 of 9 justices" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /75% of 56 votes while George W\. Bush was president, and in 57% of 399 under other presidents.*14\.3 points more often.*give or take 5\.0\./
      )
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "14.3 points, plus or minus 5.0" })).toBeInTheDocument();
    expect(screen.getByText(/through the 2025 term/)).toBeInTheDocument();
    expect(screen.getByText(/\+2\.50 in the 2024 term/)).toBeInTheDocument();
    expect(screen.getByText("Clarence Thomas")).toBeInTheDocument();
    // The API's names, punctuation included; never rebuilt from an id.
    expect(screen.getByText("Brett M. Kavanaugh")).toBeInTheDocument();
  });

  it("an unmeasured justice is not given a score", () => {
    render(
      <main>
        <JusticeScorecard
          justice={{ ...justice, score: { loyalty: null, overall: null }, loyalty: null }}
        />
      </main>
    );
    expect(screen.getAllByText(/Not yet measured/).length).toBe(2);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("has no structural accessibility violations", async () => {
    render(
      <main>
        <JusticeScorecard justice={justice} rank={{ rank: 9, of: 9 }} />
      </main>
    );
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});
