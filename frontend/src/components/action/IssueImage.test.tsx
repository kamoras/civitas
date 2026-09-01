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

  it("omits the figcaption and falls back to the issue title for alt when there is no caption or credit", () => {
    const { container } = render(
      <IssueImage issue={issue({ imageUrl: "https://rollcall.com/img.jpg", title: "A story" })} />
    );
    expect(container.querySelector("figcaption")).toBeNull();
    // No visible caption exists to stand in for a description, so the
    // image must not be alt="" — that would silently drop it for
    // screen-reader users entirely.
    expect(container.querySelector("img")).toHaveAttribute("alt", "A story");
  });

  it("falls back to the issue title for alt when there is credit but no caption", () => {
    // media:text (caption) and mi:licensorName (credit) are parsed
    // independently from the feed — a real item can carry one without the
    // other. A credit-only figcaption is attribution, not a description,
    // so it must not suppress the image's alt text.
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

  it("uses an empty alt when there is a caption but no credit", () => {
    const { container } = render(
      <IssueImage
        issue={issue({
          imageUrl: "https://rollcall.com/img.jpg",
          imageAlt: "A member of Congress speaks.",
        })}
      />
    );
    expect(screen.getByText(/A member of Congress speaks\./)).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("alt", "");
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
