import { describe, expect, it, vi } from "vitest";
import { keepFocusOnSelectedTab, retryKeepingFocus } from "./tabFocus";

describe("retryKeepingFocus", () => {
  it("moves focus to the enclosing tab panel before retrying", () => {
    // The retry unmounts the button; focus must not fall to <body>.
    document.body.innerHTML = `
      <div role="tabpanel" tabindex="0" id="panel">
        <button id="retry">Try again</button>
      </div>`;
    const button = document.getElementById("retry")!;
    button.focus();
    const retry = vi.fn(() => {
      expect(document.activeElement?.id).toBe("panel");
      button.remove();
    });

    retryKeepingFocus(retry)({ currentTarget: button });

    expect(retry).toHaveBeenCalledOnce();
    expect(document.activeElement?.id).toBe("panel");
  });

  it("still retries outside a tab panel", () => {
    document.body.innerHTML = `<button id="retry">Try again</button>`;
    const retry = vi.fn();
    retryKeepingFocus(retry)({ currentTarget: document.getElementById("retry")! });
    expect(retry).toHaveBeenCalledOnce();
  });

  it("prefers the caller's nearer target", () => {
    document.body.innerHTML = `
      <div role="tabpanel" tabindex="0">
        <ul><li><button id="toggle" aria-expanded="true">Row</button>
          <button id="retry">Try again</button></li></ul>
      </div>`;
    const retry = vi.fn();
    retryKeepingFocus(retry, (b) =>
      b.closest("li")?.querySelector<HTMLElement>("button[aria-expanded]")
    )({ currentTarget: document.getElementById("retry")! });
    expect(document.activeElement?.id).toBe("toggle");
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe("keepFocusOnSelectedTab", () => {
  const tabs = () => {
    document.body.innerHTML = `
      <div role="tablist">
        <button role="tab" id="a" tabindex="-1" aria-selected="false">A</button>
        <button role="tab" id="b" tabindex="0" aria-selected="true">B</button>
      </div>
      <div role="tabpanel" tabindex="0"><button id="inside">x</button></div>`;
  };

  it("moves focus off a tab that stopped being selected (Back from B to A's history entry)", () => {
    tabs();
    document.getElementById("a")!.focus();
    keepFocusOnSelectedTab("b");
    expect(document.activeElement?.id).toBe("b");
  });

  it("leaves focus inside a panel where it is", () => {
    tabs();
    document.getElementById("inside")!.focus();
    keepFocusOnSelectedTab("b");
    expect(document.activeElement?.id).toBe("inside");
  });

  it("does nothing with focus on the body", () => {
    tabs();
    (document.activeElement as HTMLElement | null)?.blur();
    keepFocusOnSelectedTab("b");
    expect(document.activeElement).toBe(document.body);
  });
});
