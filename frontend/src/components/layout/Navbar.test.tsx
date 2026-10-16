import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import Navbar from "./Navbar";
import Footer from "./Footer";
import { SECTIONS, sectionSubpages, type SectionKey } from "@/lib/routes";

vi.mock("next/navigation", () => ({ usePathname: () => "/elections/map" }));
vi.mock("./RecordsBand", () => ({ default: () => null }));

const KEYS = Object.keys(SECTIONS) as SectionKey[];

describe("Navbar", () => {
  it("discloses a section's pages with a button, not a menu, and Escape closes it", () => {
    render(<Navbar />);
    const button = screen.getByRole("button", { name: "Elections pages" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    const panel = document.getElementById(button.getAttribute("aria-controls")!)!;
    expect(panel).not.toBeVisible();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panel).toBeVisible();
    const seatMap = within(panel).getByRole("link", { name: /Seat map/ });
    expect(seatMap).toHaveAttribute("aria-current", "page");

    seatMap.focus();
    fireEvent.keyDown(seatMap, { key: "Escape" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("opens one section at a time", () => {
    render(<Navbar />);
    fireEvent.click(screen.getByRole("button", { name: "Congress pages" }));
    fireEvent.click(screen.getByRole("button", { name: "Elections pages" }));
    expect(screen.getByRole("button", { name: "Congress pages" })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  it("marks the section of the page, and a section with no other pages has no button", () => {
    render(<Navbar />);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getByRole("link", { name: "ELECTIONS" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.queryByRole("button", { name: "Explore pages" })).not.toBeInTheDocument();
  });

  it("lists every section's pages one level down in the phone menu", () => {
    render(<Navbar />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    const menu = document.getElementById("mobile-menu")!;
    for (const key of KEYS) {
      const subs = sectionSubpages(key);
      if (!subs.length) continue;
      const list = within(menu).getByRole("list", { name: `${SECTIONS[key].label} pages` });
      expect(
        within(list)
          .getAllByRole("link")
          .map((l) => l.getAttribute("href"))
      ).toEqual(subs.map((t) => t.href));
    }
    for (const link of within(menu).getAllByRole("link"))
      expect(link.className).toMatch(/min-h-11/);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(document.getElementById("mobile-menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveFocus();
  });
});

describe("Navbar row overflow guard", () => {
  // jsdom lays nothing out: give the bar, wordmark and row the widths a
  // browser would measure, then fire the ResizeObserver.
  function measured(bar: number, logo: number, row: number) {
    let fire = () => {};
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(cb: () => void) {
          fire = cb;
        }
        observe() {}
        disconnect() {}
      }
    );
    const set = (el: Element, props: Record<string, number>) => {
      for (const [k, v] of Object.entries(props))
        Object.defineProperty(el, k, { configurable: true, value: v });
    };
    render(<Navbar />);
    const rowEl = screen.getByTestId("nav-row");
    set(rowEl.parentElement!, { clientWidth: bar });
    set(rowEl.parentElement!.querySelector('a[href="/"]')!, { offsetWidth: logo });
    set(rowEl, { offsetWidth: row, scrollWidth: row });
    act(() => fire());
    return rowEl;
  }

  it("keeps the row where it fits with the gap to spare", () => {
    const row = measured(900, 116, 599);
    expect(row.className).toContain("min-[56rem]:flex");
    expect(screen.getByRole("button", { name: "Open navigation menu" }).className).toContain(
      "min-[56rem]:hidden"
    );
    vi.unstubAllGlobals();
  });

  it("takes the MENU layout at any width where the row would overflow", () => {
    // A longer label, a larger font or a translation: 760 px of row in a
    // 896 px bar beside the wordmark leaves less than the gap.
    const row = measured(896, 116, 760);
    expect(row.className).toContain("invisible");
    expect(row).toHaveAttribute("inert");
    const menu = screen.getByRole("button", { name: "Open navigation menu" });
    expect(menu.className).not.toContain("min-[56rem]:hidden");
    fireEvent.click(menu);
    expect(document.getElementById("mobile-menu")!.className).not.toContain("min-[56rem]:hidden");
    vi.unstubAllGlobals();
  });
});

describe("Footer", () => {
  it("has a column for every section, with its pages", () => {
    render(<Footer />);
    const nav = screen.getByRole("navigation", { name: "Footer navigation" });
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((l) => l.getAttribute("href"));
    for (const key of KEYS) {
      expect(hrefs).toContain(SECTIONS[key].href);
      for (const t of sectionSubpages(key)) expect(hrefs).toContain(t.href);
    }
  });
});
