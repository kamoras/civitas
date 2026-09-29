import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { IssueTags, WhatYouCanDo } from "./IssueEnrichment";
import type { ActionIssue } from "@/types/action";

function issue(overrides: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 1,
    publicId: "abc123",
    date: "2026-09-29",
    firstSurfaced: "2026-09-29",
    rank: 1,
    title: "A story",
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
    status: "confirmed",
    ...overrides,
  };
}

const rows = () => screen.getAllByRole("listitem");

describe("WhatYouCanDo", () => {
  it("sends a reader to the directory when the coverage names no member", () => {
    // Never "your state": nothing asks or remembers where the reader lives.
    render(<WhatYouCanDo issue={issue()} today="2026-09-29" />);
    const link = screen.getByRole("link", { name: "Directory →" });
    expect(link).toHaveAttribute("href", "/politicians");
  });

  it("contacts each named member through their own form, by chamber", () => {
    render(
      <WhatYouCanDo
        issue={issue({
          relatedSenators: [
            {
              id: "S1",
              name: "Maria Alvarez",
              state: "NM",
              party: "D",
              overallScore: 74.4,
              leadershipScore: null,
              chamber: "senate",
              contactFormUrl: "https://alvarez.senate.gov/contact",
            },
            {
              id: "H1",
              name: "Tom Reed",
              state: "KS",
              party: "R",
              overallScore: 61,
              leadershipScore: null,
              chamber: "house",
            },
          ],
        })}
        today="2026-09-29"
      />
    );
    const [sen, rep] = rows();
    expect(within(sen).getByText(/Sen\. Maria Alvarez/)).toBeInTheDocument();
    expect(within(sen).getByRole("link", { name: "Contact ↗" })).toHaveAttribute(
      "href",
      "https://alvarez.senate.gov/contact"
    );
    // No contact URL on file: the scorecard, not a generic senate.gov page
    // (wrong for a House member).
    expect(within(rep).getByText(/Rep\. Tom Reed/)).toBeInTheDocument();
    expect(within(rep).getByRole("link", { name: "Scorecard →" })).toHaveAttribute(
      "href",
      "/politicians/H1"
    );
    expect(screen.queryByRole("link", { name: "Directory →" })).toBeNull();
  });

  it("lists a bill once, even when a track action points at it too", () => {
    const url = "https://www.congress.gov/bill/119th-congress/house-bill/5371";
    render(
      <WhatYouCanDo
        issue={issue({
          relatedBills: [
            {
              id: "H.R.5371",
              name: "Continuing Appropriations Act",
              url,
              internalUrl: "/congress/bills/HR.5371",
            },
          ],
          actions: [{ type: "track_legislation", text: "Track H.R.5371 on Congress.gov", url }],
        })}
        today="2026-09-29"
      />
    );
    const follows = rows().filter((r) => within(r).queryByText("Follow"));
    expect(follows).toHaveLength(1);
    expect(within(follows[0]).getByRole("link", { name: "Bill page →" })).toHaveAttribute(
      "href",
      "/congress/bills/HR.5371"
    );
  });

  it("offers a comment only while the period is open", () => {
    const doc = {
      title: "Proposed rule",
      docType: "proposed_rule",
      date: "2026-09-01",
      url: null,
      commentUrl: "https://www.regulations.gov/x",
    };
    render(
      <WhatYouCanDo
        issue={issue({
          relatedExploreDocs: [
            { ...doc, id: 7, commentsCloseOn: "2026-10-11" },
            { ...doc, id: 8, commentsCloseOn: "2026-09-28" },
          ],
        })}
        today="2026-09-29"
      />
    );
    expect(screen.getByRole("link", { name: "Comment →" })).toHaveAttribute(
      "href",
      "/explore/7#comment"
    );
    // Closed yesterday: still worth reading, no longer commentable.
    expect(screen.getByRole("link", { name: "Document →" })).toHaveAttribute("href", "/explore/8");
  });
});

describe("IssueTags", () => {
  it("links a monitor to its row on the monitors tab", () => {
    render(<IssueTags issue={issue({ relatedMonitorSlugs: ["government-funding"] })} />);
    expect(screen.getByRole("link", { name: /Government funding/ })).toHaveAttribute(
      "href",
      "/action?tab=monitors&monitor=government-funding"
    );
  });

  it("opens the monitor in place when the page supplies a handler", () => {
    // On /action itself a same-route <Link> is a soft navigation the page
    // never re-reads, so it must be a button that sets the state directly.
    const onMonitor = vi.fn();
    render(
      <IssueTags
        issue={issue({ relatedMonitorSlugs: ["government-funding"] })}
        onMonitor={onMonitor}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Government funding/ }));
    expect(onMonitor).toHaveBeenCalledWith("government-funding");
  });
});
