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

describe("a retired developing draft", () => {
  it("retired unconfirmed: flagged Not confirmed, never Developing", () => {
    render(<IssueMeta issue={draft({ isCurrent: false, retiredReason: "unconfirmed" })} />);
    expect(screen.getByText("Not confirmed")).toBeInTheDocument();
    expect(screen.queryByText("Developing")).toBeNull();
  });

  it("retired unconfirmed: taken off before coverage confirmed it, not still to come", () => {
    render(
      <DevelopingDisclosure sourceType="house_roll_call_vote" retired retiredReason="unconfirmed" />
    );
    expect(
      screen.getByText(
        /^Not confirmed\. .*taken off the Action Center before broader news coverage/
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/has not yet confirmed/)).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("retired covered: superseded by the reporting, which it links, never 'not confirmed'", () => {
    render(<IssueMeta issue={draft({ isCurrent: false, retiredReason: "covered" })} />);
    expect(screen.getByText("Superseded")).toBeInTheDocument();
    expect(screen.queryByText("Not confirmed")).toBeNull();
    render(
      <DevelopingDisclosure
        sourceType="house_roll_call_vote"
        retired
        retiredReason="covered"
        coveredBy="i12ab34cd"
      />
    );
    expect(screen.getByText(/^Superseded by broader coverage\./)).toBeInTheDocument();
    expect(screen.queryByText(/Not confirmed|before broader news coverage confirmed/)).toBeNull();
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
