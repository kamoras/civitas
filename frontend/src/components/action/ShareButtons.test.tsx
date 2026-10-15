import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ShareButtons, { buildShareText } from "./ShareButtons";
import type { ActionIssue } from "@/types/action";
import { BSKY_HANDLE } from "@/lib/site";

const issue = { id: 1, publicId: "abc", title: "A bill passes" } as unknown as ActionIssue;

function intentText(name: RegExp): string {
  const href = screen.getByRole("link", { name }).getAttribute("href") ?? "";
  return new URL(href).searchParams.get("text") ?? "";
}

describe("issue share text", () => {
  it("credits Civitas's own account on Bluesky, and no account elsewhere", () => {
    render(<ShareButtons issue={issue} imageShare={false} />);
    expect(intentText(/Bluesky/)).toContain(`via @${BSKY_HANDLE}`);
    expect(intentText(/on X/)).not.toContain("via @");
  });

  it("drops the hashtag, then the credit, to stay within length", () => {
    const url = "https://civitas-research.org/issue/abc";
    expect(buildShareText("T", url, BSKY_HANDLE)).toBe(
      `T. The record and what you can do: ${url} via @${BSKY_HANDLE} #CivicTransparency`
    );
    const long = "x".repeat(150);
    expect(buildShareText(long, url, BSKY_HANDLE)).toBe(
      `${long}. The record and what you can do: ${url}`
    );
  });
});
