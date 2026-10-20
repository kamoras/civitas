import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import OnThisPage from "./OnThisPage";
import { onThisPage } from "@/lib/onThisPage";

// jsdom has no layout: stand in a page six-plus screens long and headings at
// given distances from the top of the viewport.
let tops: Record<string, number> = {};
const realRect = Element.prototype.getBoundingClientRect;

function Page({ n = 4 }: { n?: number }) {
  const labels = ["Score", "Funding independence", "Votes", "Holdings", "Methods"];
  return (
    <>
      <header role="banner" />
      <main>
        <OnThisPage />
        {labels.slice(0, n).map((l, i) => (
          <h2 key={l} id={`s${i}`} {...onThisPage(l, i === 2 ? "47 recorded" : undefined)}>
            {l}
          </h2>
        ))}
      </main>
    </>
  );
}

beforeEach(() => {
  tops = { s0: 300, s1: 1200, s2: 2400, s3: 3600, s4: 4800 };
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    value: 10_000,
  });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (this.tagName === "HEADER") return { top: 0, bottom: 86, height: 86 } as DOMRect;
    if (this.id in tops) return { top: tops[this.id], bottom: tops[this.id] + 30 } as DOMRect;
    return realRect.call(this);
  });
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
    cb(0);
    // 0, so the component's "a frame is already queued" guard stays clear.
    return 0;
  });
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

function scrollTo(first: number) {
  // Every heading moves up together, as when the page scrolls.
  const d = tops.s0 - first;
  for (const k of Object.keys(tops)) tops[k] -= d;
  act(() => {
    window.dispatchEvent(new Event("scroll"));
  });
}

describe("OnThisPage", () => {
  it("shows nothing on a page with too few sections, or a short one", () => {
    const { unmount } = render(<Page n={2} />);
    expect(screen.queryByRole("navigation", { name: "Sections on this page" })).toBeNull();
    unmount();
    Object.defineProperty(document.documentElement, "scrollHeight", {
      configurable: true,
      value: 2000,
    });
    render(<Page />);
    expect(screen.queryByRole("navigation", { name: "Sections on this page" })).toBeNull();
  });

  it("pins under the header once the first section reaches it, naming the section being read", () => {
    render(<Page />);
    const nav = document.querySelector("[data-on-this-page]")!;
    expect(nav.className).toMatch(/\bhidden\b/);
    scrollTo(100);
    expect(nav.className).toMatch(/\bflex\b/);
    expect(nav.textContent).toContain("Score");
    scrollTo(-2300); // Votes is now at 100, just under header + bar
    expect(nav.textContent).toContain("Votes");
    expect(nav.textContent).toContain("47 recorded");
  });

  it("lists every section in a sheet, marks the current one, and jumps with focus on the heading", () => {
    render(<Page />);
    scrollTo(-1000); // Funding independence passed the line
    const opener = screen.getByRole("button", { name: "Sections" });
    fireEvent.click(opener);
    const sheet = screen.getByRole("dialog", { name: "On this page" });
    const links = within(sheet).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual([
      "Score",
      "▸Funding independence",
      "Votes47 recorded",
      "Holdings",
    ]);
    expect(links[1]).toHaveAttribute("aria-current", "location");
    expect(links[0]).not.toHaveAttribute("aria-current");
    // The title takes focus.
    expect(document.activeElement).toBe(
      within(sheet).getByRole("heading", { name: /on this page/i })
    );

    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    fireEvent.click(links[2]);
    expect(screen.queryByRole("dialog")).toBeNull();
    const target = document.getElementById("s2")!;
    expect(document.activeElement).toBe(target);
    expect(target).toHaveAttribute("tabindex", "-1");
    expect(scrolled).toHaveBeenCalledWith({ block: "start" });
    expect(window.location.hash).toBe("#s2");
  });

  it("closes the sheet on Escape and hands focus back to its button", () => {
    render(<Page />);
    scrollTo(100);
    const opener = screen.getByRole("button", { name: "Sections" });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("reserves the bar's height in a phone's scroll padding (2.4.11)", () => {
    const css = readFileSync(join(import.meta.dirname, "..", "..", "app", "globals.css"), "utf8");
    expect(css).toMatch(
      /@media \(max-width: 639\.98px\)\s*\{\s*html:has\(\[data-on-this-page\]\)\s*\{\s*scroll-padding-top:\s*calc\(var\(--header-clearance\) \+ 2\.75rem \+ 3px\)/
    );
  });
});
