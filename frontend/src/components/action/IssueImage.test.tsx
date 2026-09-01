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

describe("IssueImage", () => {
  it("renders nothing when there is no image", () => {
    const { container } = render(<IssueImage issue={issue()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the image with an empty alt when a visible caption is shown", () => {
    const { container } = render(
      <IssueImage
        issue={issue({
          imageUrl: "https://rollcall.com/img.jpg",
          imageAlt: "A member of Congress speaks.",
          imageCredit: "Tom Williams/CQ Roll Call",
        })}
      />
    );
    // alt="" removes the image from the accessibility tree's img role
    // entirely (confirmed by this query failing with getByRole) — exactly
    // the intended behavior for an image with an equivalent visible
    // caption, so this asserts on the raw element instead.
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("alt", "");
    expect(screen.getByText(/A member of Congress speaks\./)).toBeInTheDocument();
    expect(screen.getByText(/Tom Williams\/CQ Roll Call/)).toBeInTheDocument();
  });

  it("omits the figcaption entirely when the source gave no caption or credit", () => {
    const { container } = render(
      <IssueImage issue={issue({ imageUrl: "https://rollcall.com/img.jpg" })} />
    );
    expect(container.querySelector("figcaption")).toBeNull();
  });

  it("renders a bare thumbnail with no caption at the thumbnail size", () => {
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
    expect(container.querySelector("img")).toHaveAttribute("alt", "");
  });
});
