import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DevelopingDisclosure, IssueMeta } from "./IssueEnrichment";
import type { ActionIssue } from "@/types/action";

function draft(overrides: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 1,
    publicId: "i0b93c2d1",
    date: "2026-10-14",
    firstSurfaced: "2026-10-14",
    rank: 4,
    title: "House vote on S 2403: Passed, 401-14",
    summary: "A summary.",
    facts: [],
    factSources: [],
    newFacts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: [],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    isTrending: false,
    status: "developing",
    sourceType: "house_roll_call_vote",
    ...overrides,
  };
}

describe("a developing draft retired unconfirmed", () => {
  it("is flagged Not confirmed, never Developing", () => {
    render(<IssueMeta issue={draft({ isCurrent: false })} />);
    expect(screen.getByText("Not confirmed")).toBeInTheDocument();
    expect(screen.queryByText("Developing")).toBeNull();
  });

  it("says it was taken off the Action Center, not that coverage is still to come", () => {
    render(<DevelopingDisclosure sourceType="house_roll_call_vote" retired />);
    expect(
      screen.getByText(/taken off the Action Center before broader news coverage/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/has not yet confirmed/)).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("links the reported issue that covers the same vote", () => {
    render(
      <DevelopingDisclosure sourceType="house_roll_call_vote" retired coveredBy="i12ab34cd" />
    );
    expect(screen.getByRole("link", { name: /reported issue on the same vote/ })).toHaveAttribute(
      "href",
      "/issue/i12ab34cd"
    );
  });

  it("a live draft still reads Developing", () => {
    render(<IssueMeta issue={draft({ isCurrent: true })} />);
    expect(screen.getByText("Developing")).toBeInTheDocument();
  });
});
