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
});
