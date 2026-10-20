import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import Navbar, { useHideOnScroll } from "./Navbar";
import { SECTIONS, sectionSubpages, type SectionKey } from "@/lib/routes";

vi.mock("next/navigation", () => ({
  usePathname: () => "/elections/map",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("./RecordsBand", () => ({ default: () => null, RunStatusSquare: () => null }));

const KEYS = Object.keys(SECTIONS) as SectionKey[];

describe("Navbar search", () => {
  function stubDialog() {
    const proto = HTMLDialogElement.prototype as HTMLDialogElement & Record<string, unknown>;
    proto.showModal = function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    };
    proto.close = function (this: HTMLDialogElement) {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    };
  }

  it("is a link to /search (no JavaScript needed) that opens the search, focused", () => {
    stubDialog();
    render(<Navbar />);
    const [toggle] = screen.getAllByRole("link", { name: "Search" });
    expect(toggle).toHaveAttribute("href", "/search");
    fireEvent.click(toggle);
    const box = screen.getByRole("combobox", { name: "Search the whole site" });
    expect(box).toHaveFocus();
    expect(box).toHaveAttribute("name", "q");
    const form = box.closest("form")!;
    expect(form).toHaveAttribute("action", "/search");
    expect(form).toHaveAttribute("method", "get");
    // A clean name: no brackets read out.
    expect(within(form).getByRole("button", { name: "Go" })).toHaveAttribute("type", "submit");
  });

  it("closes on Escape from anywhere in it and gives focus back to its link", () => {
    stubDialog();
    render(<Navbar />);
    const [toggle] = screen.getAllByRole("link", { name: "Search" });
    fireEvent.click(toggle);
    const dialog = screen.getByRole("combobox").closest("dialog")!;
    // The close button stands in for any control in the dialog; the native
    // modal's Escape is its `cancel`, which closes it.
    fireEvent.click(within(dialog).getByRole("button", { name: /close search/i }));
    expect(dialog).not.toHaveAttribute("open");
    expect(toggle).toHaveFocus();
  });
});

describe("Navbar", () => {
  it("marks the section a record names over the one its path would", () => {
    // A record's URL is shared by several sections (recordTrail): the
    // profile passes its branch's.
    render(<Navbar section="court" />);
    const row = screen.getByTestId("nav-row");
    expect(within(row).getByRole("link", { name: /^SUPREME COURT/ })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(within(row).getByRole("button", { name: /^ELECTIONS/ })).not.toHaveTextContent(
      "current section"
    );
  });

  it("opens a section's pages from one trigger, not a menu, and Escape closes it", () => {
    render(<Navbar />);
    const row = screen.getByTestId("nav-row");
    // One control per section: the name is the trigger, no separate link.
    const button = within(row).getByRole("button", { name: /^Elections/ });
    expect(within(row).queryByRole("link", { name: "Elections" })).toBeNull();
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
    fireEvent.click(within(row).getByRole("button", { name: /^Congress/ }));
    fireEvent.click(within(row).getByRole("button", { name: /^Elections/ }));
    expect(within(row).getByRole("button", { name: /^Congress/ })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  it("marks the section of the page, and a section with one page is a plain link", () => {
    render(<Navbar />);
    const row = screen.getByTestId("nav-row");
    expect(
      within(row).getByRole("button", { name: /^Elections,? ?current section$/ })
    ).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Explore" })).toHaveAttribute("href", "/explore");
    expect(within(row).queryByRole("button", { name: /^Explore/ })).toBeNull();
  });

  it("lists the sections first, each one's pages behind its own disclosure, then the state", () => {
    // Mobile spec P8: 25 caps links at one weight, pages beside sections.
    render(<Navbar />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    const menu = document.getElementById("mobile-menu")!;
    // Focus starts on the panel itself, never the state picker: a phone
    // opens a native select's list when it takes focus.
    expect(menu).toHaveFocus();
    expect(menu).toHaveAccessibleName("Site menu");
    expect(document.activeElement?.tagName).not.toBe("Select");
    // Shift+Tab from the panel stays in it, on its last control.
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(within(menu).getAllByRole("link").at(-1)).toHaveFocus();
    // Every section is a 52px row, its name as written, before the picker.
    const sections = KEYS.map((key) =>
      within(menu).getByRole("link", { name: SECTIONS[key].label })
    );
    for (const link of sections) expect(link.className).toMatch(/min-h-13/);
    const picker = within(menu).getByLabelText("Your state");
    expect(
      sections.at(-1)!.compareDocumentPosition(picker) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    const toggle = (key: SectionKey) =>
      within(menu).getByRole("button", { name: `${SECTIONS[key].label} pages` });
    // The current page's section (here Elections) opens with its pages.
    for (const key of KEYS.filter((k) => sectionSubpages(k).length))
      expect(toggle(key)).toHaveAttribute("aria-expanded", key === "elections" ? "true" : "false");
    for (const key of KEYS) {
      const subs = sectionSubpages(key);
      if (!subs.length) continue;
      if (toggle(key).getAttribute("aria-expanded") !== "true") fireEvent.click(toggle(key));
      expect(toggle(key)).toHaveAttribute("aria-expanded", "true");
      const list = within(menu).getByRole("list", { name: `${SECTIONS[key].label} pages` });
      expect(
        within(list)
          .getAllByRole("link")
          .map((l) => l.getAttribute("href"))
      ).toEqual(subs.map((t) => t.href));
    }
    for (const link of within(menu).getAllByRole("link"))
      expect(link.className).toMatch(/min-h-1[13]/);
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

describe("Navbar markup with every menu closed", () => {
  // The footer no longer repeats the sections' pages, so the header's closed
  // desktop panels (`hidden`, display: none) are where a crawler finds them:
  // they must be in the markup before anything opens.
  it("holds a link to every section's pages", () => {
    render(<Navbar />);
    const hrefs = [...document.querySelectorAll("header a")].map((a) => a.getAttribute("href"));
    for (const key of KEYS) {
      expect(hrefs).toContain(SECTIONS[key].href);
      for (const t of sectionSubpages(key)) expect(hrefs).toContain(t.href);
    }
  });
});

describe("useHideOnScroll (a phone's header)", () => {
  function Harness({ menuOpen = false }: { menuOpen?: boolean }) {
    const ref = React.useRef<HTMLElement>(null);
    const hidden = useHideOnScroll(ref, menuOpen);
    return (
      <header ref={ref} data-hidden={String(hidden)}>
        <button type="button">in the header</button>
      </header>
    );
  }
  function media(reduced: boolean) {
    // jsdom has no matchMedia: the hook leaves the header alone without it.
    window.matchMedia = ((q: string) => ({
      matches: q.includes("reduce") ? reduced : true,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      cb(0);
      return 0;
    });
  }
  function scroll(y: number) {
    act(() => {
      Object.defineProperty(window, "scrollY", { configurable: true, value: y });
      window.dispatchEvent(new Event("scroll"));
    });
  }
  const state = () => document.querySelector("header")!.getAttribute("data-hidden");

  it("hides on scroll down past the first screen and comes back on scroll up", () => {
    media(false);
    render(<Harness />);
    scroll(300);
    expect(state()).toBe("false"); // still within the first screen
    scroll(2000);
    scroll(2400);
    expect(state()).toBe("true");
    expect(document.documentElement.hasAttribute("data-header-hidden")).toBe(true);
    scroll(2300);
    expect(state()).toBe("false");
    expect(document.documentElement.hasAttribute("data-header-hidden")).toBe(false);
    vi.restoreAllMocks();
  });

  it("shows when focus goes into it", () => {
    media(false);
    render(<Harness />);
    scroll(2000);
    scroll(2400);
    expect(state()).toBe("true");
    act(() => screen.getByRole("button", { name: "in the header" }).focus());
    expect(state()).toBe("false");
    vi.restoreAllMocks();
  });

  it("never hides with reduced motion, or with the menu open", () => {
    media(true);
    const { unmount } = render(<Harness />);
    scroll(2000);
    scroll(2400);
    expect(state()).toBe("false");
    unmount();
    vi.restoreAllMocks();
    media(false);
    render(<Harness menuOpen />);
    scroll(2000);
    scroll(2400);
    expect(state()).toBe("false");
    vi.restoreAllMocks();
  });
});
