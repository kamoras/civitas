import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { FollowResults, followResultsActions } from "./IssueEnrichment";
import type { ActionIssue } from "@/types/action";

afterEach(cleanup);

const issue = (actions: ActionIssue["actions"]) => ({ actions }) as unknown as ActionIssue;

describe("FollowResults", () => {
  it("links a seat-flip issue to its live count", () => {
    render(
      <FollowResults
        issue={issue([{ text: "Follow the count for Georgia's U.S. Senate", type: "follow_results", url: "/elections/states/GA#race-2026-SEN-GA" }])}
      />
    );
    expect(screen.getByRole("link", { name: /Follow the count for Georgia/ })).toHaveAttribute(
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
