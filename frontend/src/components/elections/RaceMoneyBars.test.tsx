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
    lastFinancialsSync: "2026-10-01T00:00:00Z",
    incumbentRecord: null,
    ...over,
  } as BallotCandidate;
}

function widths(container: HTMLElement) {
  return [...container.querySelectorAll<HTMLElement>("[style*='width']")].map(
    (el) => el.style.width
  );
}

describe("RaceMoneyBars", () => {
  it("scales bars to the leader of this race, not an absolute figure", () => {
    const { container } = render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[
          cand({ id: "a", name: "Leader", contributions: 2_000_000 }),
          cand({ id: "b", name: "Trailer", party: "REP", contributions: 200_000 }),
        ]}
      />
    );
    expect(widths(container)).toEqual(["100%", "10%"]);
  });

  it("orders by money raised regardless of input order", () => {
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[
          cand({ id: "a", name: "Small", contributions: 5_000 }),
          cand({ id: "b", name: "Big", party: "REP", contributions: 900_000 }),
        ]}
      />
    );
    const names = screen.getAllByText(/Small|Big/).map((n) => n.textContent);
    expect(names[0]).toBe("Big");
  });

  it("does not divide by zero when nobody has reported money", () => {
    // A real case in states whose filers have raised nothing: every bar
    // empty is the truthful picture, not a crash and not a full bar.
    const { container } = render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[
          cand({ id: "a", contributions: 0, hasRaisedFunds: false }),
          cand({ id: "b", contributions: 0, hasRaisedFunds: false }),
        ]}
      />
    );
    expect(widths(container)).toEqual(["0%", "0%"]);
    expect(screen.getAllByText("no funds reported")).toHaveLength(2);
  });

  it("never shows $0 for a candidate with no figure yet", () => {
    // A null figure is "not synced" or "no report for this election",
    // never $0 — the label used to read hasRaisedFunds and format null as $0.
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[
          cand({ id: "a", contributions: 50_000 }),
          cand({ id: "b", contributions: null, cashOnHand: null, lastFinancialsSync: null }),
          cand({ id: "c", contributions: null, cashOnHand: null }),
        ]}
      />
    );
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
    expect(screen.getByText("awaiting FEC sync")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "awaiting FEC sync" })).toBeInTheDocument();
    expect(screen.getAllByText("no funds reported")).toHaveLength(1);
  });

  it("shows reported money even when the FEC's roster flag says none", () => {
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[cand({ contributions: 871_029, hasRaisedFunds: false })]}
      />
    );
    expect(screen.getByText("$871K")).toBeInTheDocument();
  });

  it("marks the incumbent", () => {
    render(<RaceMoneyBars cycleYear={2026} candidates={[cand({ incumbentChallenge: "I" })]} />);
    expect(screen.getByText("Incumbent")).toBeInTheDocument();
  });

  it("calls an incumbent on a redrawn seat a sitting member, not the incumbent", () => {
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[cand({ incumbentChallenge: "I" })]}
        redrawnSeat
      />
    );
    expect(screen.getByText("Sitting member")).toBeInTheDocument();
    expect(screen.queryByText("Incumbent")).not.toBeInTheDocument();
  });

  it("calls them the member before this election from election day on", () => {
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[cand({ incumbentChallenge: "I" })]}
        redrawnSeat
        resultsMode
      />
    );
    expect(screen.getByText("Member before this election")).toBeInTheDocument();
    expect(screen.queryByText(/Sitting member|incumbent/)).not.toBeInTheDocument();
  });

  it("links an incumbent's representation score, shown as the scorecard shows it", () => {
    // 42.5 is 43 on the scorecard; one decimal here read 42.5.
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[cand({ incumbentRecord: { id: "member-a", score: 42.5 } })]}
      />
    );
    const link = screen.getByRole("link", { name: /where this member stands among peers/ });
    expect(link).toHaveAttribute("href", "/politicians/member-a");
    // No composite score on the ballot (redesign 2026-10).
    expect(screen.queryByText("43")).not.toBeInTheDocument();
  });

  it("badges unconfirmed entries only when the race asks for it", () => {
    const { rerender } = render(
      <RaceMoneyBars cycleYear={2026} candidates={[cand({ confirmed: false })]} showUnconfirmed />
    );
    expect(screen.getByText("Unconfirmed")).toBeInTheDocument();
    rerender(<RaceMoneyBars cycleYear={2026} candidates={[cand({ confirmed: false })]} />);
    expect(screen.queryByText("Unconfirmed")).not.toBeInTheDocument();
  });

  it("splits contributions into the FEC's categories, in blocks and in words", () => {
    const fundingSources = {
      addsUp: true,
      parts: [
        { key: "smallDonors" as const, amount: 35, share: 0.35, shown: "35%", blocks: 7 },
        { key: "largerDonors" as const, amount: 50, share: 0.5, shown: "50%", blocks: 10 },
        { key: "committees" as const, amount: 15, share: 0.15, shown: "15%", blocks: 3 },
        { key: "party" as const, amount: 0, share: 0.0, shown: "0%", blocks: 0 },
        { key: "candidate" as const, amount: 0, share: 0.0, shown: "0%", blocks: 0 },
      ],
    };
    const { container } = render(
      <RaceMoneyBars cycleYear={2026} candidates={[cand({ fundingSources })]} />
    );
    expect(screen.getByText(/Where this campaign.s contributions came from/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "35% from donors of $200 or less · 50% from donors of more than $200 · 15% from PACs and other committees"
      )
    ).toBeInTheDocument();
    expect(container.querySelectorAll("[style*='background-color']").length).toBe(20 + 5);
  });

  it("gives no split when the filing's categories don't add up", () => {
    const parts = (
      ["smallDonors", "largerDonors", "committees", "party", "candidate"] as const
    ).map((key) => ({ key, amount: 1, share: null, shown: null, blocks: null }));
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[cand({ fundingSources: { addsUp: false, parts } })]}
      />
    );
    expect(screen.getByText(/don't add up to its total/)).toBeInTheDocument();
    expect(
      screen.queryByText(/Where this campaign.s contributions came from/)
    ).not.toBeInTheDocument();
  });

  it("dates each candidate's figures and links that election's FEC page", () => {
    render(
      <RaceMoneyBars
        cycleYear={2026}
        candidates={[
          cand({ id: "H6XX00001", financialsThrough: "2026-06-30", candidateLoans: 2_400_000 }),
        ]}
      />
    );
    const link = screen.getByRole("link", { name: /FEC figures through 2026-06-30/ });
    expect(link).toHaveAttribute(
      "href",
      "https://www.fec.gov/data/candidate/H6XX00001/?cycle=2026&election_full=true"
    );
    expect(screen.getByText(/lent own campaign \$2\.4M/)).toBeInTheDocument();
  });
});
