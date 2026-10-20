import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import SearchCombobox, { suggestionStatus } from "./SearchCombobox";
import SiteSearch from "./SiteSearch";
import type { SearchItem } from "@/types/search";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const EPA: SearchItem = {
  kind: "agency",
  group: "Agencies",
  label: "Environmental Protection Agency",
  context: "Federal agency",
  url: "/agencies/environmental-protection-agency",
};
const ENERGY: SearchItem = {
  kind: "agency",
  group: "Agencies",
  label: "Energy Department",
  context: "Federal agency",
  url: "/agencies/energy-department",
};

function answer(items: SearchItem[], direct: string | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            query: new URL(url, "http://x").searchParams.get("q"),
            direct,
            items,
          })
        )
      )
    )
  );
}

async function typeInto(input: HTMLElement, text: string) {
  fireEvent.change(input, { target: { value: text } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  push.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SearchCombobox", () => {
  it("is an ARIA 1.2 combobox controlling a listbox", () => {
    render(<SearchCombobox />);
    const input = screen.getByRole("combobox", { name: "Search Civitas" });
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
    const list = document.getElementById(input.getAttribute("aria-controls")!);
    expect(list?.getAttribute("role")).toBe("listbox");
    // A plain GET form underneath: works with no JavaScript.
    const form = input.closest("form")!;
    expect(form.getAttribute("action")).toBe("/search");
    expect(form.getAttribute("method")).toBe("get");
    expect(input.getAttribute("name")).toBe("q");
  });

  it("highlights a direct match first, and Enter opens it", async () => {
    answer([EPA, ENERGY], EPA.url);
    render(<SearchCombobox />);
    const input = screen.getByRole("combobox");
    await typeInto(input, "EPA");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0].getAttribute("aria-selected")).toBe("true");
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0].id);
    expect(screen.getByRole("status").textContent).toMatch(/2 matches\. Best match: Environmental/);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith(EPA.url);
  });

  it("highlights nothing when the matches are a list, so Enter submits the form", async () => {
    answer([EPA, ENERGY], null);
    render(<SearchCombobox />);
    const input = screen.getByRole("combobox");
    await typeInto(input, "agency");
    expect(input.getAttribute("aria-activedescendant")).toBeNull();
    const ev = fireEvent.keyDown(input, { key: "Enter" });
    expect(ev).toBe(true); // not prevented: the form's own submit goes ahead
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toMatch(/up and down arrows/);
  });

  it("moves with the arrow keys, wrapping, and Escape closes then clears", async () => {
    answer([EPA, ENERGY], null);
    render(<SearchCombobox />);
    const input = screen.getByRole("combobox") as HTMLInputElement;
    await typeInto(input, "en");
    const [first, second] = screen.getAllByRole("option");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe(first.id);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe(second.id);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe(first.id);
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input.getAttribute("aria-activedescendant")).toBe(second.id);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith(ENERGY.url);

    await typeInto(input, "ene");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(input.value).toBe("ene");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("");
  });

  it("opens a match on click", async () => {
    answer([EPA, ENERGY], null);
    render(<SearchCombobox />);
    await typeInto(screen.getByRole("combobox"), "en");
    fireEvent.mouseDown(screen.getAllByRole("option")[1]);
    expect(push).toHaveBeenCalledWith(ENERGY.url);
  });

  it("says plainly when nothing matches", () => {
    expect(suggestionStatus([], false, "xyzzy")).toBe(
      "Nothing on file matching xyzzy. Press Enter to search anyway."
    );
    expect(suggestionStatus([], false, "x")).toBe("");
  });

  it("does not ask for suggestions for the query a page opened with", async () => {
    answer([EPA], EPA.url);
    render(<SearchCombobox initialQuery="EPA" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("has no axe violations, open or closed", async () => {
    vi.useRealTimers();
    answer([EPA, ENERGY], EPA.url);
    render(
      <main>
        <SearchCombobox />
      </main>
    );
    const rules = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(document.body, rules)).violations.map((v) => v.id)).toEqual([]);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "EPA" } });
    await screen.findAllByRole("option");
    expect((await axe.run(document.body, rules)).violations.map((v) => v.id)).toEqual([]);
  });
});

describe("SiteSearch", () => {
  it("is a link to /search, so it works without JavaScript", () => {
    render(<SiteSearch />);
    const link = screen.getByRole("link", { name: /search/i });
    expect(link.getAttribute("href")).toBe("/search");
  });

  it("opens a modal dialog with the combobox focused, and Close returns focus", () => {
    const proto = HTMLDialogElement.prototype as HTMLDialogElement & Record<string, unknown>;
    proto.showModal = function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    };
    proto.close = function (this: HTMLDialogElement) {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    };
    render(<SiteSearch />);
    const link = screen.getByRole("link", { name: /search/i });
    fireEvent.click(link);
    const dialog = screen.getByRole("dialog", { name: "Search" });
    expect(dialog.hasAttribute("open")).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole("combobox"));
    fireEvent.click(screen.getByRole("button", { name: /close search/i }));
    expect(dialog.hasAttribute("open")).toBe(false);
    expect(document.activeElement).toBe(link);
  });
});
