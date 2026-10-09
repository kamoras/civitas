import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import JusticeScorecard from "./JusticeScorecard";
import type { Justice } from "@/types/justice";

// A justice as the API serves one under justice v3: no score, the
// appointer estimate with its interval.
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
  loyalty: {
    estimate: 0.1916,
    se: 0.062,
    ciLow: 0.0701,
    ciHigh: 0.3131,
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
  it("says the justice is not scored, why, and links the research", () => {
    render(
      <main>
        <JusticeScorecard justice={justice} />
      </main>
    );
    expect(screen.getByRole("heading", { level: 1, name: "Jane Q. Example" })).toBeInTheDocument();
    // The header and each of the three columns.
    expect(screen.getAllByText("Not scored")).toHaveLength(4);
    expect(screen.getByText(/No method yet separates loyalty/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "The research" })).toHaveAttribute(
      "href",
      expect.stringContaining("docs/research/justice-scores.md")
    );
    // No rank and no number standing in for a score.
    expect(screen.queryByText(/#\d+ of/)).not.toBeInTheDocument();
  });

  it("shows the appointer estimate with its interval, as information", () => {
    render(<JusticeScorecard justice={justice} />);
    expect(
      screen.getByText(
        /75% of 56 votes while A\. President was president, and in 57% of 399 under other presidents.*19\.2 points more often.*95% confidence interval 7\.0 to 31\.3/
      )
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "19.2 points, 95% interval 7.0 to 31.3" })
    ).toBeInTheDocument();
    expect(screen.getByText(/through the 2025 term/)).toBeInTheDocument();
    expect(screen.getByText(/\+2\.50 in the 2024 term/)).toBeInTheDocument();
    // The API's names, punctuation included; never rebuilt from an id.
    expect(screen.getByText("Richard R. Roe")).toBeInTheDocument();
  });

  it("an unmeasured justice has no estimate and no stand-in number", () => {
    render(
      <main>
        <JusticeScorecard justice={{ ...justice, loyalty: null }} />
      </main>
    );
    expect(screen.getByText(/Not yet measured/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("renders a response cached before agreement replaced agreementMatrix", () => {
    render(<JusticeScorecard justice={{ ...justice, agreement: undefined }} />);
    expect(screen.queryByRole("heading", { name: /agree/i })).not.toBeInTheDocument();
  });

  it("has no structural accessibility violations", async () => {
    render(
      <main>
        <JusticeScorecard justice={justice} />
      </main>
    );
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});
