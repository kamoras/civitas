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
      "Members",
      "Bills",
      "Committees",
      "Nominations",
    ]);
    // The page is a view of Reports: the tab holds it, the view is it.
    expect(links[0]).toHaveAttribute("aria-current", "true");
    expect(links.filter((l) => l.hasAttribute("aria-current"))).toHaveLength(1);
    for (const l of links) expect(l.className).toContain("min-h-11");

    const views = nav.querySelector<HTMLElement>("[data-section-views]")!;
    const reports = within(views).getByRole("group", { name: "Reports pages" });
    expect(
      within(reports)
        .getAllByRole("link")
        .map((l) => [l.textContent, l.getAttribute("aria-current")])
    ).toEqual([
      ["Floor", null],
      ["Members", "true"],
    ]);
    const members = within(views).getByRole("group", { name: "Members pages" });
    const pages = within(members).getAllByRole("link");
    expect(pages.map((l) => l.textContent)).toEqual(["By the numbers", "Votes with the president"]);
    expect(pages[1]).toHaveAttribute("aria-current", "page");
    expect(pages[1]).toHaveAttribute("href", "/congress/president/house");
    for (const l of [...within(views).getAllByRole("link")])
      expect(l.className).toContain("min-h-11");
  });

  it("leaves the floor's Day, Week and Month to the page's own toggle", () => {
    render(<SectionTabs section="congress" active="week" />);
    const views = document.querySelector<HTMLElement>("[data-section-views]")!;
    expect(within(views).getAllByRole("group")).toHaveLength(1);
    expect(within(views).getByRole("link", { name: "Floor" })).toHaveAttribute(
      "aria-current",
      "true"
    );
  });

  it("draws no view row for a tab without views", () => {
    render(<SectionTabs section="congress" active="bills" />);
    expect(document.querySelector("[data-section-views]")).toBeNull();
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
    expect(links.map((l) => l.textContent)).toEqual(["Ballots", "Seat map", "Calendar", "Archive"]);
    expect(links[1]).toHaveAttribute("aria-current", "true");
    for (const l of links) expect(l.className).toContain("min-h-11");
  });

  it("names a one-page section's page without a disclosure", () => {
    render(<SectionTabs section="court" active="court" />);
    const nav = screen.getByRole("navigation", { name: /sections$/ });
    expect(nav.querySelector("details")).toBeNull();
  });
});
