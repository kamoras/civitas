import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import BranchSelector, { type Branch } from "./BranchSelector";

function Harness() {
  const [branch, setBranch] = useState<Branch>("senate");
  return (
    <>
      <BranchSelector selected={branch} onChange={setBranch} />
      <div role="tabpanel" id={`branch-panel-${branch}`} tabIndex={0}>
        {branch}
      </div>
    </>
  );
}

afterEach(cleanup);

describe("BranchSelector", () => {
  it("moves focus to the incoming tab, so every arrow press keeps working", () => {
    // Focus used to move into the panel, outside the tablist's key handler:
    // the second ArrowRight did nothing.
    render(<Harness />);
    screen.getByRole("tab", { name: "SENATE" }).focus();

    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });

    const president = screen.getByRole("tab", { name: "PRESIDENT" });
    expect(president).toHaveAttribute("aria-selected", "true");
    expect(president).toHaveFocus();
  });

  it("wraps with Home and End", () => {
    render(<Harness />);
    screen.getByRole("tab", { name: "SENATE" }).focus();

    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(screen.getByRole("tab", { name: "SCOTUS" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "SENATE" })).toHaveFocus();
  });
});
