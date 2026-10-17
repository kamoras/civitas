import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CommentWindowBar from "./CommentWindowBar";
import PaperworkFold from "./PaperworkFold";

const period = {
  opened: "2026-10-01",
  closes: "2026-10-31",
  totalDays: 30,
  daysLeft: 10,
  elapsedPercent: 67,
};

describe("CommentWindowBar", () => {
  it("says in words what the bar draws, and hides the bar from screen readers", () => {
    const { container } = render(<CommentWindowBar period={period} />);
    expect(screen.getByText("10 of 30 days left to comment")).toBeTruthy();
    const bar = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("67%");
  });

  it("says when today is the last day", () => {
    render(<CommentWindowBar period={{ ...period, daysLeft: 0, elapsedPercent: 100 }} />);
    expect(screen.getByText("Closes today, the last of 30 days")).toBeTruthy();
  });
});

describe("PaperworkFold", () => {
  it("folds the notices under a count, and says how many it lists", () => {
    render(
      <PaperworkFold count={12} shown={8}>
        <p>list</p>
      </PaperworkFold>
    );
    expect(screen.getByText("Paperwork notices (12)").tagName).toBe("SUMMARY");
    expect(screen.getByText(/These are the 8 closing soonest/)).toBeTruthy();
  });

  it("renders nothing without any", () => {
    const { container } = render(
      <PaperworkFold count={0} shown={0}>
        <p>list</p>
      </PaperworkFold>
    );
    expect(container.innerHTML).toBe("");
  });
});
