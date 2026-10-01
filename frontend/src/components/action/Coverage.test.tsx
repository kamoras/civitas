import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Coverage, SummarySource } from "./IssueEnrichment";
import type { ActionIssue } from "@/types/action";

function issue(overrides: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 1,
    publicId: "abc123",
    date: "2026-10-01",
    firstSurfaced: "2026-10-01",
    rank: 1,
    title: "A story",
    summary: "Renee Good's family sues U.S. government and immigration officials.",
    facts: ["Family of US woman killed by ICE agent sues Trump officials."],
    factSources: ["BBC World"],
    newFacts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: [],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    isTrending: false,
    status: "confirmed",
    ...overrides,
  };
}

// 2026-10-01: an issue built from a PBS and a BBC article listed both as
// sources but only BBC under "In the coverage": PBS's line was the summary,
// which carried no outlet. Every quoted line now names and links its own.
describe("quoted lines name and link their articles", () => {
  it("links each coverage line to the article it was quoted from", () => {
    render(<Coverage issue={issue({ factSourceUrls: ["https://www.bbc.com/news/a"] })} />);
    expect(screen.getByRole("link", { name: "BBC World" })).toHaveAttribute(
      "href",
      "https://www.bbc.com/news/a"
    );
  });

  it("names the outlet without a link for an issue that predates links", () => {
    render(<Coverage issue={issue()} />);
    expect(screen.getByText("BBC World")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("names and links the summary's outlet", () => {
    render(
      <SummarySource
        issue={issue({ summarySource: "PBS NewsHour", summarySourceUrl: "https://www.pbs.org/b" })}
      />
    );
    expect(screen.getByRole("link", { name: "PBS NewsHour" })).toHaveAttribute(
      "href",
      "https://www.pbs.org/b"
    );
  });

  it("shows nothing for a summary with no recorded outlet", () => {
    const { container } = render(<SummarySource issue={issue()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
