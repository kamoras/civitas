import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import Navbar from "./Navbar";
import Footer from "./Footer";
import { SECTIONS, sectionSubpages, type SectionKey } from "@/lib/routes";

vi.mock("next/navigation", () => ({ usePathname: () => "/elections/map" }));
vi.mock("./RecordsBand", () => ({ default: () => null }));

const KEYS = Object.keys(SECTIONS) as SectionKey[];

describe("Navbar search", () => {
  it("opens a plain labelled GET form to /search?q=, focused", () => {
    render(<Navbar />);
    const [toggle] = screen.getAllByRole("button", { name: "SEARCH" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const box = screen.getByLabelText("Search the record");
    expect(box).toHaveFocus();
    expect(box).toHaveAttribute("name", "q");
    const form = box.closest("form")!;
    expect(form).toHaveAttribute("action", "/search");
    expect(form).toHaveAttribute("method", "get");
    expect(within(form).getByRole("button", { name: "[ SEARCH ]" })).toHaveAttribute(
      "type",
      "submit"
    );
  });

  it("closes on Escape and gives focus back to its button", () => {
    render(<Navbar />);
    const [toggle] = screen.getAllByRole("button", { name: "SEARCH" });
    fireEvent.click(toggle);
    fireEvent.keyDown(screen.getByLabelText("Search the record"), { key: "Escape" });
    expect(screen.queryByLabelText("Search the record")).toBeNull();
    expect(toggle).toHaveFocus();
  });
});

describe("Navbar", () => {
  it("opens a section's pages from one trigger, not a menu, and Escape closes it", () => {
    render(<Navbar />);
    const row = screen.getByTestId("nav-row");
    // One control per section: the name is the trigger, no separate link.
    const button = within(row).getByRole("button", { name: /^ELECTIONS/ });
    expect(within(row).queryByRole("link", { name: "ELECTIONS" })).toBeNull();
    expect(button).toHaveAttribute("aria-expanded", "false");
    const panel = document.getElementById(button.getAttribute("aria-controls")!)!;
    expect(panel).not.toBeVisible();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panel).toBeVisible();
    // Every page of the section, the hub first, the current one marked.
    expect(
      within(panel)
        .getAllByRole("link")
        .map((l) => l.getAttribute("href"))
    ).toEqual(SECTIONS.elections.tabs.map((t) => t.href));
    const seatMap = within(panel).getByRole("link", { name: /Seat map/ });
    expect(seatMap).toHaveAttribute("aria-current", "page");
    expect(within(panel).getByRole("link", { name: "Ballots by state" })).not.toHaveAttribute(
      "aria-current"
    );

    seatMap.focus();
    fireEvent.keyDown(seatMap, { key: "Escape" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("opens one section at a time", () => {
    render(<Navbar />);
    const row = screen.getByTestId("nav-row");
    fireEvent.click(within(row).getByRole("button", { name: /^CONGRESS/ }));
    fireEvent.click(within(row).getByRole("button", { name: /^ELECTIONS/ }));
    expect(within(row).getByRole("button", { name: /^CONGRESS/ })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  it("marks the section of the page, and a section with one page is a plain link", () => {
    render(<Navbar />);
    const row = screen.getByTestId("nav-row");
    expect(
      within(row).getByRole("button", { name: /^ELECTIONS,? ?current section$/ })
    ).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "EXPLORE" })).toHaveAttribute("href", "/explore");
    expect(within(row).queryByRole("button", { name: /^EXPLORE/ })).toBeNull();
  });

  it("lists every section's pages one level down in the phone menu", () => {
    render(<Navbar />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    const menu = document.getElementById("mobile-menu")!;
    // Focus starts on the panel's first control, the state picker.
    expect(within(menu).getByLabelText("Your state")).toHaveFocus();
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
    // MENU and the MENU layout's SEARCH share one wrapper, hidden with the row shown.
    const menu = screen.getByRole("button", { name: "Open navigation menu" });
    expect(menu.parentElement!.className).toContain("min-[56rem]:hidden");
    vi.unstubAllGlobals();
  });

  it("takes the MENU layout at any width where the row would overflow", () => {
    // A longer label, a larger font or a translation: 760 px of row in a
    // 896 px bar beside the wordmark leaves less than the gap.
    const row = measured(896, 116, 760);
    expect(row.className).toContain("invisible");
    expect(row).toHaveAttribute("inert");
    const menu = screen.getByRole("button", { name: "Open navigation menu" });
    expect(menu.parentElement!.className).not.toContain("min-[56rem]:hidden");
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
