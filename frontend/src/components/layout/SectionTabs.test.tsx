import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import SectionTabs from "./SectionTabs";

describe("SectionTabs", () => {
  it("is plain links, the current page marked, each target 44 px tall", () => {
    render(<SectionTabs section="congress" active="president" />);
    const nav = screen.getByRole("navigation", { name: "Congress sections" });
    // Pages, not an in-page tab widget.
    expect(within(nav).queryByRole("tab")).not.toBeInTheDocument();
    const row = nav.querySelector<HTMLElement>("[data-section-row]")!;
    expect(row.className).toMatch(/\bhidden\b.*\bsm:flex\b/);
    const links = within(row).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "Reports",
      "Latest week",
      "Latest month",
      "Members",
      "Measure by measure",
      "Compare members",
      "Bills",
      "Committees",
      "Nominations",
      "Votes with the president",
    ]);
    expect(links[9]).toHaveAttribute("aria-current", "page");
    expect(links.filter((l) => l.hasAttribute("aria-current"))).toHaveLength(1);
    for (const l of links) expect(l.className).toContain("min-h-11");
  });

  it("marks no tab for a page that isn't one of them", () => {
    render(<SectionTabs section="elections" active={null} />);
    expect(screen.getAllByRole("link").some((l) => l.hasAttribute("aria-current"))).toBe(false);
    expect(screen.getByText("Pages")).toBeTruthy();
  });

  it("is one 44px row on a phone that names the page and opens the others", () => {
    // Mobile spec P3: the wrapped rows took 130-250px of a phone's first screen.
    render(<SectionTabs section="elections" active="map" />);
    const nav = screen.getByRole("navigation", { name: "Elections sections" });
    const switcher = nav.querySelector("details")!;
    expect(switcher.className).toMatch(/\bsm:hidden\b/);
    expect(switcher.open).toBe(false);
    const summary = switcher.querySelector("summary")!;
    expect(summary.className).toContain("min-h-11");
    expect(summary.textContent).toBe("Elections▸Seat map▾");
    const links = within(switcher).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "Ballots by state",
      "Seat map",
      "New House maps",
      "Calendar",
      "Archive",
    ]);
    expect(links[1]).toHaveAttribute("aria-current", "page");
    for (const l of links) expect(l.className).toContain("min-h-11");
  });

  it("names a one-page section's page without a disclosure", () => {
    render(<SectionTabs section="court" active="court" />);
    const nav = screen.getByRole("navigation", { name: /sections$/ });
    expect(nav.querySelector("details")).toBeNull();
  });
});
