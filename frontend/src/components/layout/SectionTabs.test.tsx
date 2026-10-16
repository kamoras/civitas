import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import SectionTabs from "./SectionTabs";

describe("SectionTabs", () => {
  it("is plain links, the current page marked, each target 44 px tall", () => {
    render(<SectionTabs section="congress" active="president" />);
    const nav = screen.getByRole("navigation", { name: "Congress sections" });
    // Pages, not an in-page tab widget.
    expect(within(nav).queryByRole("tab")).not.toBeInTheDocument();
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "Reports",
      "Latest week",
      "Latest month",
      "Bills",
      "Committees",
      "Votes with the president",
    ]);
    expect(links[5]).toHaveAttribute("aria-current", "page");
    expect(links.filter((l) => l.hasAttribute("aria-current"))).toHaveLength(1);
    for (const l of links) expect(l.className).toContain("min-h-11");
  });

  it("marks no tab for a page that isn't one of them", () => {
    render(<SectionTabs section="elections" active={null} />);
    expect(screen.getAllByRole("link").some((l) => l.hasAttribute("aria-current"))).toBe(false);
  });
});
