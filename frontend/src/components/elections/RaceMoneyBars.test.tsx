import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RaceMoneyBars from "./RaceMoneyBars";
import type { BallotCandidate } from "@/types/election";

function cand(over: Partial<BallotCandidate> = {}): BallotCandidate {
  return {
    id: "c1",
    name: "A Candidate",
    party: "DEM",
    confirmed: true,
    incumbentChallenge: null,
    hasRaisedFunds: true,
    candidateStatus: "C",
    contributions: 100_000,
    cashOnHand: 10_000,
    lastFinancialsSync: null,
    incumbentRecord: null,
    ...over,
  } as BallotCandidate;
}

function widths(container: HTMLElement) {
  return [...container.querySelectorAll<HTMLElement>("[style*='width']")].map(
    (el) => el.style.width,
  );
}

describe("RaceMoneyBars", () => {
  it("scales bars to the leader of this race, not an absolute figure", () => {
    const { container } = render(
      <RaceMoneyBars
        candidates={[
          cand({ id: "a", name: "Leader", contributions: 2_000_000 }),
          cand({ id: "b", name: "Trailer", party: "REP", contributions: 200_000 }),
        ]}
      />,
    );
    expect(widths(container)).toEqual(["100%", "10%"]);
  });

  it("orders by money raised regardless of input order", () => {
    render(
      <RaceMoneyBars
        candidates={[
          cand({ id: "a", name: "Small", contributions: 5_000 }),
          cand({ id: "b", name: "Big", party: "REP", contributions: 900_000 }),
        ]}
      />,
    );
    const names = screen.getAllByText(/Small|Big/).map((n) => n.textContent);
    expect(names[0]).toBe("Big");
  });

  it("does not divide by zero when nobody has reported money", () => {
    // A real case in states whose filers have raised nothing: every bar
    // empty is the truthful picture, not a crash and not a full bar.
    const { container } = render(
      <RaceMoneyBars
        candidates={[
          cand({ id: "a", contributions: 0, hasRaisedFunds: false }),
          cand({ id: "b", contributions: 0, hasRaisedFunds: false }),
        ]}
      />,
    );
    expect(widths(container)).toEqual(["0%", "0%"]);
    expect(screen.getAllByText("no funds reported")).toHaveLength(2);
  });

  it("marks the incumbent", () => {
    render(<RaceMoneyBars candidates={[cand({ incumbentChallenge: "I" })]} />);
    expect(screen.getByText("INCUMBENT")).toBeInTheDocument();
  });

  it("links an incumbent's representation score", () => {
    render(
      <RaceMoneyBars
        candidates={[cand({ incumbentRecord: { id: "brian-jack", score: 42.42 } })]}
      />,
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/politicians/brian-jack");
    expect(screen.getByText("42.4")).toBeInTheDocument();
  });

  it("badges unconfirmed entries only when the race asks for it", () => {
    const { rerender } = render(
      <RaceMoneyBars candidates={[cand({ confirmed: false })]} showUnconfirmed />,
    );
    expect(screen.getByText("UNCONFIRMED")).toBeInTheDocument();
    rerender(<RaceMoneyBars candidates={[cand({ confirmed: false })]} />);
    expect(screen.queryByText("UNCONFIRMED")).not.toBeInTheDocument();
  });
});
