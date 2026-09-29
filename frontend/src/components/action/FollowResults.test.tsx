import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Coverage, WhatYouCanDo, followResultsActions } from "./IssueEnrichment";
import type { ActionIssue } from "@/types/action";

afterEach(cleanup);

const issue = (actions: ActionIssue["actions"]) => ({ actions }) as unknown as ActionIssue;

describe("the live count in What you can do", () => {
  it("links a seat-flip issue to its live count", () => {
    render(
      <WhatYouCanDo
        today="2026-11-04"
        issue={issue([
          {
            text: "Follow the count for Georgia's U.S. Senate",
            type: "follow_results",
            url: "/elections/states/GA#race-2026-SEN-GA",
          },
        ])}
      />
    );
    expect(screen.getByText("Follow the count for Georgia's U.S. Senate")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Live count →" })).toHaveAttribute(
      "href",
      "/elections/states/GA#race-2026-SEN-GA"
    );
  });

  it("takes only same-site election paths", () => {
    expect(
      followResultsActions(
        issue([
          { text: "x", type: "follow_results", url: "https://evil.example/elections/" },
          { text: "y", type: "follow_results", url: "javascript:alert(1)" },
          { text: "z", type: "track_legislation", url: "/elections/states/GA" },
        ])
      )
    ).toEqual([]);
  });
});

describe("a count issue's actions and facts", () => {
  const count = {
    actions: [
      {
        text: "Follow the count for Georgia's U.S. Senate",
        type: "follow_results",
        url: "/elections/states/GA#race-2026-SEN-GA",
      },
    ],
    facts: ["Jane Doe (D): 101,234 votes, 50.4%"],
    factSources: [],
    newFacts: [],
    status: "developing",
    sourceType: "election_results",
    title: "Democrat leads Georgia's U.S. Senate count in a seat Republicans hold",
  } as unknown as ActionIssue;

  it("offers no directory row: no coverage named anyone, and the count is the action", () => {
    render(<WhatYouCanDo today="2026-11-04" issue={count} />);
    expect(screen.queryByText(/Find your senators/)).toBeNull();
  });

  it("says, on the card too, that the count is not final and when Civitas read it", () => {
    render(
      <Coverage issue={{ ...count, countAsOf: "2026-11-04T01:15:00Z", countOfficial: false }} />
    );
    expect(document.body.textContent).toMatch(/NOT FINAL/);
    expect(document.body.textContent).toMatch(
      /the count as of Nov 3, 8:15 PM ET, when Civitas read it/
    );
  });

  it("names no time it doesn't have", () => {
    render(<Coverage issue={count} />);
    expect(document.body.textContent).toMatch(/NOT FINAL · The state's own results site/);
    expect(document.body.textContent).not.toMatch(/count as of/);
  });
});
