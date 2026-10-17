import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RaceContext, { leanCountsSentence, leanSentence } from "./RaceContext";
import type { BallotCandidate, RaceWithCandidates } from "@/types/election";

function cand(over: Partial<BallotCandidate> = {}): BallotCandidate {
  return {
    id: "c1",
    name: "EXAMPLE, ONE",
    party: "DEM",
    confirmed: true,
    incumbentChallenge: null,
    hasRaisedFunds: true,
    candidateStatus: "C",
    contributions: 1,
    cashOnHand: 1,
    lastFinancialsSync: "2026-10-01T00:00:00Z",
    incumbentRecord: null,
    ...over,
  } as BallotCandidate;
}

function race(over: Partial<RaceWithCandidates> = {}): RaceWithCandidates {
  return {
    id: "2026-HOUSE-NC-1",
    cycleYear: 2026,
    office: "H",
    state: "NC",
    district: 1,
    isSpecial: false,
    pvi: 5,
    pviLevel: "district",
    leanContext: {
      unit: "districts",
      of: 435,
      moreDemocratic: 236,
      moreRepublican: 187,
      same: 11,
      seatLean: 3,
      distribution: DISTRIBUTION,
    },
    candidateSource: "confirmed",
    counties: null,
    candidates: [cand()],
    ...over,
  };
}

// 435 seats from D+40 to R+30: 236 left of R+3, 12 at it, 187 right.
const DISTRIBUTION = [
  { lean: -40, label: "D+40", count: 36 },
  { lean: -5, label: "D+5", count: 200 },
  { lean: 3, label: "R+3", count: 12 },
  { lean: 30, label: "R+30", count: 187 },
];

describe("RaceContext", () => {
  it("puts the lean into words and every seat on one lean axis", () => {
    render(
      <RaceContext race={race({ usualVote: { lean: 3.4, label: "R+3", source: "computed" } })} />
    );
    // The map's figure leads, Cook's is the cross-check.
    expect(screen.getByText("The seat · R+3")).toBeInTheDocument();
    expect(screen.getByText(/about 3 points more Republican/)).toBeInTheDocument();
    expect(screen.getByText(/Cook PVI, as a cross-check/)).toBeInTheDocument();
    const axis = screen.getByRole("img", {
      name: "Every district's lean, from D+40 to R+30; this seat, R+3, marked. 236 of 435 districts lean more Democratic, 187 more Republican, 11 the same.",
    });
    // One mark per seat, all in one SVG of fixed height scaled to the width:
    // a single row at 320, 375 or 1280 px, never a wrapping grid of cells.
    const svgs = axis.querySelectorAll("svg");
    expect(svgs).toHaveLength(1);
    expect(svgs[0].getAttribute("preserveAspectRatio")).toBe("none");
    expect(svgs[0].getAttribute("class")).toMatch(/\bw-full\b/);
    expect(svgs[0].getAttribute("class")).toMatch(/\bh-12\b/);
    expect(axis.querySelectorAll("rect[data-lean]")).toHaveLength(435);
    expect(axis.querySelector(".aspect-square, .grid, .flex-wrap")).toBeNull();
    // Tinted by each seat's own lean, not by which side of this seat.
    expect(axis.querySelector('rect[data-lean="-5"]')).toHaveClass("fill-dem-blue");
    expect(axis.querySelector('rect[data-lean="30"]')).toHaveClass("fill-rep-red");
    // This seat's column outlined at its own lean, left to right.
    const seat = axis.querySelector("rect[data-seat]")!;
    expect(Number(seat.getAttribute("x"))).toBeGreaterThan(
      Number(axis.querySelector('rect[data-lean="-5"]')!.getAttribute("x"))
    );
    // A text equivalent: every lean with its count.
    expect(screen.getByRole("cell", { name: "R+3 (this seat)" })).toBeInTheDocument();
  });

  it("says the lead figure is Civitas's computed lean, with its method", () => {
    render(
      <RaceContext race={race({ usualVote: { lean: 5.34, label: "R+5", source: "computed" } })} />
    );
    expect(screen.getByText(/the figure the 2026 map shows, used in no score/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How it is computed" })).toHaveAttribute(
      "href",
      "/about/data#computed-lean"
    );
  });

  it("falls back to Cook's figure, labelled as Cook's, without a computed one", () => {
    render(<RaceContext race={race({ usualVote: null })} />);
    expect(screen.getByText("The seat · Cook PVI R+5")).toBeInTheDocument();
  });

  it("says plainly it is not a forecast, and offers the data", () => {
    render(<RaceContext race={race()} />);
    expect(
      screen.getByText(/publishes no odds for this race and calls no race/)
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "CSV: every federal candidate in North Carolina" })
    ).toHaveAttribute("href", "/api/elections/states/NC/races.csv");
    expect(screen.getByRole("link", { name: "JSON: this race" })).toHaveAttribute(
      "href",
      "/api/elections/races/2026-HOUSE-NC-1"
    );
  });

  it("leaves the lean out from election day, and calls no race", () => {
    render(<RaceContext race={race()} resultsMode />);
    expect(screen.queryByText(/The seat/)).not.toBeInTheDocument();
    expect(screen.getByText(/Civitas calls no race/)).toBeInTheDocument();
  });

  it("links a running incumbent's scorecard in one plain line", () => {
    render(
      <RaceContext
        race={race({
          candidates: [
            cand({
              ballotName: "One Example",
              incumbentChallenge: "I",
              incumbentRecord: { id: "member-a", score: 61.2 },
            }),
          ],
        })}
      />
    );
    expect(screen.getByText("INCUMBENT")).toBeInTheDocument();
    expect(screen.getByText(/One Example is on the ballot/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /SCORECARD/ })).toHaveAttribute(
      "href",
      "/politicians/member-a"
    );
  });

  it("has no seat row without a lean to place", () => {
    render(<RaceContext race={race({ pviLevel: "state", leanContext: null })} />);
    expect(screen.queryByText(/The seat/)).not.toBeInTheDocument();
  });
});

describe("lean wording", () => {
  it("reads each side and even", () => {
    expect(leanSentence(-1, "states")).toBe(
      "In the last two presidential elections, this state voted about 1 point more Democratic than the nation."
    );
    expect(leanSentence(0, "districts")).toMatch(/about the same as the nation/);
    expect(
      leanCountsSentence({
        unit: "states",
        of: 50,
        moreDemocratic: 20,
        moreRepublican: 29,
        same: 0,
        seatLean: 0,
      })
    ).toBe("20 of 50 states lean more Democratic, 29 more Republican.");
  });
});
