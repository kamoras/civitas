import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import Footer from "./Footer";

afterEach(cleanup);

describe("Footer", () => {
  it("lists the site's own pages, not the header's sections again", () => {
    render(<Footer />);
    const nav = screen.getByRole("navigation", { name: "Footer navigation" });
    const names = within(nav)
      .getAllByRole("link")
      .map((a) => a.textContent);
    expect(names).toEqual([
      "Feeds",
      "API",
      "Changelog",
      "Accessibility",
      "Environmental",
      "Feedback",
      "Bluesky",
      "Source",
    ]);
    expect(screen.queryByRole("link", { name: "Leaderboard" })).toBeNull();
  });
});
