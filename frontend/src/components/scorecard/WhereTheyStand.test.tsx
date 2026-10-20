import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import WhereTheyStand from "./WhereTheyStand";
import type { PeerComparison, PeerMeasure } from "@/types/peers";

const fetchPeerComparison = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchPeerComparison: (...a: unknown[]) => fetchPeerComparison(...a),
  fetchSenatorHistory: vi.fn(async () => ({
    snapshots: [
      { date: "2026-10-18", overallScore: 60, algorithmVersion: "v7", scores: {}, facts: null },
      {
        date: "2026-10-19",
        overallScore: 60,
        algorithmVersion: "v7",
        scores: {},
        facts: { smallGifts: 0.13 },
      },
      {
        date: "2026-10-26",
        overallScore: 60,
        algorithmVersion: "v7",
        scores: {},
        facts: { smallGifts: 0.2 },
      },
    ],
  })),
  fetchRepresentativeHistory: vi.fn(),
}));

function measure(over: Partial<PeerMeasure>): PeerMeasure {
  return {
    key: "smallGifts",
    label: "Small gifts",
    direction: "higher means more of the money came in gifts of $200 or less",
    unit: "share",
    value: 0.13,
    shown: "13%",
    expected: 0.24,
    expectedBasis: "seat",
    median: 0.2,
    peerGroup: { key: "caucus", label: "Senate Democrats", n: 3 },
    distribution: [0.13, 0.2, 0.3],
    distributionIds: ["pat-lee", "sam-ray", "ari-cole"],
    distributionNames: ["Pat Lee", "Sam Ray", "Ari Cole"],
    record: 1,
    source: { name: "FEC filings", url: "https://www.fec.gov/data/" },
    percentile: 16.7,
    word: "well below peers",
    sentence:
      "Gifts of $200 or less were 13% of the money. 2 of the other 2 Senate Democrats had a larger share.",
    thin: false,
    ...over,
  };
}

const DATA: PeerComparison = {
  group: "caucus",
  rule: { wellOutside: [10, 90], outside: [25, 75], wellGapSd: 1, minRecord: 20 },
  measures: [
    measure({}),
    measure({
      key: "breaks",
      label: "Breaks with party",
      unit: "rate",
      shown: "0 of 6",
      thin: true,
      word: null,
      record: 6,
    }),
  ],
  standsOut: "Small gifts: Gifts of $200 or less were 13% of the money.",
};

describe("WhereTheyStand", () => {
  it("draws each measure from the API, with a caption, a word and a table", async () => {
    render(
      <main>
        <WhereTheyStand memberId="pat-lee" name="Pat Lee" chamber="senate" initial={DATA} />
      </main>
    );
    expect(screen.getByRole("heading", { name: "Where Pat Lee stands" })).toBeInTheDocument();
    expect(screen.getByText(DATA.standsOut)).toBeInTheDocument();
    const caption = screen.getByText(/Gifts of \$200 or less were 13% of the money\. 2 of/);
    expect(caption.closest("figure")).toHaveAttribute(
      "aria-labelledby",
      caption.closest("figcaption")?.id
    );
    expect(screen.getAllByText("well below peers")[0]).toBeInTheDocument();
    // The measure over time, from the snapshots that kept it.
    expect(
      await screen.findByText(/Over time: 13% on Mon, Oct 19, 20% on Mon, Oct 26\./)
    ).toBeInTheDocument();
    expect(screen.getByText(/Over time: kept nightly from the next run\./)).toBeInTheDocument();
    // A thin row keeps its figure and draws no word.
    expect(
      screen.getByText(/Not enough record yet to compare \(6 party-line votes\)/)
    ).toBeInTheDocument();
    // The table is there for screen readers before it is shown.
    expect(screen.getAllByRole("table")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Show as a table" }));
    expect(screen.getByRole("button", { name: "Hide the tables" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getAllByRole("link", { name: "Sam Ray" })[0]).toHaveAttribute(
      "href",
      "/politicians/sam-ray"
    );
    expect(screen.getByRole("radio", { name: "Senate Democrats" })).toBeChecked();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("fetches the whole chamber when asked, and says so in the URL", async () => {
    fetchPeerComparison.mockResolvedValue({ ...DATA, group: "chamber" });
    render(<WhereTheyStand memberId="pat-lee" name="Pat Lee" chamber="senate" initial={DATA} />);
    await userEvent.click(screen.getByRole("radio", { name: "Whole Senate" }));
    expect(fetchPeerComparison).toHaveBeenCalledWith("senate", "pat-lee", "chamber");
    expect(window.location.search).toContain("peers=chamber");
  });
});
