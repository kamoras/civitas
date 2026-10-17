import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import HeaderClearance from "./HeaderClearance";

let observed: (() => void) | null = null;
vi.stubGlobal(
  "ResizeObserver",
  class {
    constructor(cb: () => void) {
      observed = cb;
    }
    observe() {}
    disconnect() {}
  }
);

function header(height: number) {
  const h = document.createElement("header");
  h.setAttribute("role", "banner");
  h.getBoundingClientRect = () => ({ height }) as DOMRect;
  document.body.appendChild(h);
  return h;
}

afterEach(() => {
  document.body.innerHTML = "";
  document.documentElement.style.cssText = "";
});

describe("HeaderClearance", () => {
  it("leaves the stylesheet's clearance alone while the header fits it", () => {
    document.documentElement.style.setProperty("font-size", "16px");
    header(86);
    // jsdom has no stylesheet: the 6rem the real one sets is read as NaN,
    // which never asks for more, so stand it in through a computed style.
    const real = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation((el) => {
      const s = real(el);
      return new Proxy(s, {
        get: (t, k) =>
          k === "getPropertyValue"
            ? (p: string) => (p === "--header-clearance" ? "6rem" : t.getPropertyValue(p))
            : k === "fontSize"
              ? "16px"
              : Reflect.get(t, k),
      });
    });
    const { unmount } = render(<HeaderClearance />);
    expect(document.documentElement.style.getPropertyValue("--header-clearance")).toBe("");
    // The header's own height, for the phone's section bar under it.
    expect(document.documentElement.style.getPropertyValue("--header-h")).toBe("86px");
    // The bar wraps (a phone at 200% text): the clearance follows the header.
    document.querySelector("header")!.getBoundingClientRect = () => ({ height: 222 }) as DOMRect;
    observed?.();
    expect(document.documentElement.style.getPropertyValue("--header-clearance")).toBe("230px");
    expect(document.documentElement.style.getPropertyValue("--header-h")).toBe("222px");
    unmount();
    expect(document.documentElement.style.getPropertyValue("--header-clearance")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--header-h")).toBe("");
    vi.restoreAllMocks();
  });
});
