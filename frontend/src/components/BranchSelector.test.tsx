import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import BranchSelector, { type Branch } from "./BranchSelector";

// Frames run only when the test says so, so focus timing is deterministic.
let frames: FrameRequestCallback[] = [];
const runFrames = () =>
  act(() => {
    const queued = frames;
    frames = [];
    queued.forEach((cb) => cb(0));
  });

beforeEach(() => {
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
});
afterEach(() => vi.unstubAllGlobals());

function Harness() {
  const [branch, setBranch] = useState<Branch>("senate");
  return <BranchSelector selected={branch} onChange={setBranch} />;
}

describe("BranchSelector keyboard", () => {
  it("keeps focus on the tabs: ArrowRight twice from the first tab focuses the third", () => {
    render(<Harness />);
    const first = screen.getByRole("tab", { name: "SENATE" });
    first.focus();

    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    runFrames();
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "HOUSE" }));

    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    runFrames();
    const third = screen.getByRole("tab", { name: "PRESIDENT" });
    expect(document.activeElement).toBe(third);
    expect(third).toHaveAttribute("aria-selected", "true");
  });

  it("does not focus a tab that stopped being selected before the frame fired", () => {
    render(<Harness />);
    screen.getByRole("tab", { name: "SENATE" }).focus();

    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" }); // queues HOUSE
    fireEvent.click(screen.getByRole("tab", { name: "SCOTUS" }));
    runFrames();

    expect(document.activeElement).not.toBe(screen.getByRole("tab", { name: "HOUSE" }));
  });

  it("waits for a selection that renders a frame late, then focuses the incoming tab", () => {
    // Like the Action Center, where the selection comes back through the URL.
    function LateHarness() {
      const [branch, setBranch] = useState<Branch>("senate");
      return (
        <BranchSelector
          selected={branch}
          onChange={(b) => requestAnimationFrame(() => setBranch(b))}
        />
      );
    }
    render(<LateHarness />);
    screen.getByRole("tab", { name: "SENATE" }).focus();

    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    runFrames(); // focus check runs before the selection lands
    runFrames(); // selection has landed

    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "HOUSE" }));
  });
});
