import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { WhatYouCanDo, followResultsActions } from "./IssueEnrichment";
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
