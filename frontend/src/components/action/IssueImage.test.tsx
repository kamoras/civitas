import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { IssueImage } from "./IssueEnrichment";
import type { ActionIssue } from "@/types/action";

function issue(overrides: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 1,
    publicId: "abc123",
    date: "2026-08-31",
    firstSurfaced: "2026-08-31",
    rank: 1,
    title: "A story",
    summary: "A summary.",
    facts: [],
    newFacts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: [],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    concernedCount: 0,
    notPriorityCount: 0,
    isTrending: false,
    status: "confirmed",
    ...overrides,
  };
}

// Alt text is always populated, no alt="" case at all — a prior version
// suppressed it whenever an equivalent visible caption was shown (WCAG
// technically permits this), but that conditional logic already shipped
// one real bug (a credit-only figcaption, which is attribution rather
// than a description, still suppressed alt and silently dropped the
// image for screen-reader users). Always-populate can't have that
// failure mode.
describe("IssueImage", () => {
  it("renders nothing when there is no image", () => {
    const { container } = render(<IssueImage issue={issue()} />);
    expect(container.firstChild).toBeNull();
  });

  it("uses the source caption as alt text when one is present", () => {
    const { container } = render(
      <IssueImage
        issue={issue({
          imageUrl: "https://rollcall.com/img.jpg",
          imageAlt: "A member of Congress speaks.",
          imageCredit: "Tom Williams/CQ Roll Call",
        })}
      />
    );
    expect(container.querySelector("img")).toHaveAttribute("alt", "A member of Congress speaks.");
    // The same caption is still shown visibly too, alongside credit —
    // a deliberate duplication for sighted readers, not an oversight.
    expect(screen.getByText(/A member of Congress speaks\./)).toBeInTheDocument();
    expect(screen.getByText(/Tom Williams\/CQ Roll Call/)).toBeInTheDocument();
  });

  it("falls back to the issue title for alt when there is no caption or credit", () => {
    const { container } = render(
      <IssueImage issue={issue({ imageUrl: "https://rollcall.com/img.jpg", title: "A story" })} />
    );
    expect(container.querySelector("figcaption")).toBeNull();
    expect(container.querySelector("img")).toHaveAttribute("alt", "A story");
  });

  it("falls back to the issue title for alt when there is credit but no caption", () => {
    // media:text (caption) and mi:licensorName (credit) are parsed
    // independently from the feed — a real item can carry one without
    // the other.
    const { container } = render(
      <IssueImage
        issue={issue({
          imageUrl: "https://rollcall.com/img.jpg",
          title: "A story",
          imageCredit: "Tom Williams/CQ Roll Call",
        })}
      />
    );
    expect(screen.getByText(/Tom Williams\/CQ Roll Call/)).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("alt", "A story");
  });

  it("renders a thumbnail with real alt text, no caption", () => {
    const { container } = render(
      <IssueImage
        issue={issue({
          imageUrl: "https://rollcall.com/img.jpg",
          imageAlt: "A member of Congress speaks.",
          imageCredit: "Tom Williams/CQ Roll Call",
        })}
        size="thumbnail"
      />
    );
    expect(container.querySelector("figcaption")).toBeNull();
    expect(container.querySelector("img")).toHaveAttribute("alt", "A member of Congress speaks.");
  });

  it("falls back to the issue title for a thumbnail with no caption", () => {
    const { container } = render(
      <IssueImage
        issue={issue({ imageUrl: "https://rollcall.com/img.jpg", title: "A story" })}
        size="thumbnail"
      />
    );
    expect(container.querySelector("img")).toHaveAttribute("alt", "A story");
  });
});
