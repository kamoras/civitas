import { describe, expect, it } from "vitest";
import { inOtherDialog, trapTab } from "./focusedDialog";

function keyFrom(target: Element): KeyboardEvent {
  const e = new KeyboardEvent("keydown", { key: "Escape" });
  Object.defineProperty(e, "target", { value: target });
  return e;
}

describe("inOtherDialog", () => {
  document.body.innerHTML = `
    <div role="dialog" id="drawer"><button id="in-drawer"></button></div>
    <div role="dialog" id="share"><button id="in-share"></button></div>
  `;
  const drawer = document.getElementById("drawer")!;
  const share = document.getElementById("share")!;

  it("is false for a key pressed inside the dialog itself", () => {
    expect(inOtherDialog(keyFrom(document.getElementById("in-drawer")!), drawer)).toBe(false);
  });

  // The bug: the drawer and the share dialog both listen on the document,
  // so Escape in the share dialog closed the drawer under it too.
  it("is true for a key pressed inside a different dialog", () => {
    expect(inOtherDialog(keyFrom(document.getElementById("in-share")!), drawer)).toBe(true);
    expect(inOtherDialog(keyFrom(document.getElementById("in-drawer")!), share)).toBe(true);
  });

  it("is false when focus has fallen to the page, so Escape still closes", () => {
    expect(inOtherDialog(keyFrom(document.body), drawer)).toBe(false);
  });
});

describe("trapTab", () => {
  function setup() {
    document.body.innerHTML = `<button id="out">out</button>
      <div id="dlg" tabindex="-1"><button id="a">a</button><button id="b">b</button></div>`;
    const [a, b] = ["a", "b"].map((id) => document.getElementById(id) as HTMLElement);
    return { a, b, dlg: document.getElementById("dlg") as HTMLElement };
  }
  const tab = (shiftKey = false) =>
    new KeyboardEvent("keydown", { key: "Tab", shiftKey, cancelable: true });

  it("keeps Shift+Tab inside when the dialog itself holds focus", () => {
    const { a, b, dlg } = setup();
    dlg.focus();
    const e = tab(true);
    trapTab(e, [a, b]);
    expect(e.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(b);
  });

  it("wraps from last to first and leaves the middle to the browser", () => {
    const { a, b } = setup();
    b.focus();
    trapTab(tab(), [a, b]);
    expect(document.activeElement).toBe(a);
    const e = tab();
    trapTab(e, [a, b]);
    expect(e.defaultPrevented).toBe(false);
  });
});
