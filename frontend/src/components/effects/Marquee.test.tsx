import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import Marquee from "./Marquee";

function mockReducedMotion(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

describe("Marquee", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("exposes every item to assistive tech via a real list, not just the scrolling text", () => {
    mockReducedMotion(false);
    render(<Marquee items={["First item", "Second item"]} />);

    const list = screen.getByText("First item").closest("ul");
    expect(list).not.toBeNull();
    expect(screen.getByText("Second item")).toBeInTheDocument();
  });

  it("pauses on hover instead of forcing a user to catch fast-moving text", () => {
    // This content is substantive (data sources, the no-tracking privacy
    // stance), not decorative — a user has to be able to stop and read it
    // (2026-08 review: this was previously impossible to pause).
    mockReducedMotion(false);
    const { container } = render(<Marquee items={["Some real content here"]} />);

    const animated = container.querySelector('[aria-hidden="true"]');
    expect(animated?.className).toContain("animate-marquee");
    expect(animated?.className).toContain("group-hover:[animation-play-state:paused]");
  });

  it("does not animate at all when the user prefers reduced motion", () => {
    mockReducedMotion(true);
    const { container } = render(<Marquee items={["Some real content here"]} />);

    const animated = container.querySelector('[aria-hidden="true"]');
    expect(animated?.className).not.toContain("animate-marquee");
  });
});
