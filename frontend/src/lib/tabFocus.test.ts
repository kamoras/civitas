import { describe, expect, it, vi } from "vitest";
import { retryKeepingFocus } from "./tabFocus";

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
