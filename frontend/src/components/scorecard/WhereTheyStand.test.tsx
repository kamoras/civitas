import { describe, expect, it, vi } from "vitest";
import { olderApi } from "@/lib/olderApi";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import WhereTheyStand from "./WhereTheyStand";
import type { PeerComparison, PeerMeasure } from "@/types/peers";

const fetchPeerComparison = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchPeerComparison: (...a: unknown[]) => fetchPeerComparison(...a),
}));

function measure(over: Partial<PeerMeasure>): PeerMeasure {
  return {
    key: "smallGifts",
    label: "Money from small donors",
    question: "Who funds the campaign?",
    why: "Share of contributions from people who gave the campaign $200 or less in total.",
    measured: "The FEC's unitemized individual contributions over all contributions.",
    window: "election",
    direction: "higher means more of the money came in gifts of $200 or less",
    unit: "share",
    value: 0.13,
    shown: "13%",
    expected: 0.24,
    expectedBasis: "seat",
    median: 0.2,
    peerGroup: { key: "caucus", label: "Senate Democrats", labelOne: "Senate Democrat", n: 3 },
    distribution: [0.13, 0.2, 0.3],
    distributionIds: ["pat-lee", "sam-ray", "ari-cole"],
    distributionNames: ["Pat Lee", "Sam Ray", "Ari Cole"],
    distributionShown: ["13%", "20%", "30%"],
    distributionSeats: ["D-OR", "D-WA", "D-CA"],
    distributionRanks: ["Lowest of 3", "2nd highest of 3", "Highest of 3"],
    record: 1,
    source: { name: "FEC filings", url: "https://www.fec.gov/data/" },
    percentile: 16.7,
    word: "well below peers",
    sentence:
      "Gifts of $200 or less were 13% of the money. 2 of the other 2 Senate Democrats had a larger share.",
    thin: false,
    axisMax: 0.4,
    axisStartShown: "0%",
    axisEndShown: "40%",
    expectedShown: "24% seat average",
    overTime: null,
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
      label: "Votes against the party",
      question: "How does this member vote?",
      why: "Share of party-line votes where this member sided with most of the other party.",
      window: "congress",
      unit: "rate",
      shown: "0 of 6",
      thin: true,
      word: null,
      record: 6,
      overTime: {
        points: [
          { x: 0, y: 100 },
          { x: 100, y: 0 },
        ],
        marks: [
          { id: "2026-10-19", label: "Oct 19, 2026", shown: "1.0%" },
          { id: "2026-10-26", label: "Oct 26, 2026", shown: "2.0%" },
        ],
        sentence: "Over time: 1.0% on Oct 19, 2026, 2.0% on Oct 26, 2026.",
      },
    }),
  ],
  standsOut: "Money from small donors: Donors who gave $200 or less gave 13% of the money.",
  standsOutKey: "smallGifts",
  standsOutWhy:
    "Picked by rule: of the four figures, this one is furthest from the middle of the group.",
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
    expect(screen.getByText(DATA.standsOutWhy)).toBeInTheDocument();
    // Each cell is headed by its measure; its question and why it is there
    // sit behind "How this is measured".
    expect(
      screen.getByRole("heading", { level: 3, name: DATA.measures[0].label })
    ).toBeInTheDocument();
    expect(screen.getAllByText("Who funds the campaign?")[0]).toBeInTheDocument();
    expect(screen.getAllByText("How does this member vote?")[0]).toBeInTheDocument();
    expect(screen.getByText(DATA.measures[0].why)).toBeInTheDocument();
    expect(screen.getAllByText("How this is measured")).toHaveLength(2);
    const caption = screen.getByText(/Gifts of \$200 or less were 13% of the money\. 2 of/);
    expect(caption.closest("figure")).toHaveAttribute(
      "aria-labelledby",
      caption.closest("figcaption")?.id
    );
    expect(screen.getAllByText("well below peers")[0]).toBeInTheDocument();
    // The table's caption keeps the group's capitals; the tick is a seat average.
    expect(
      screen.getAllByText(/every Senate Democrat's figure, lowest first/)[0]
    ).toBeInTheDocument();
    expect(screen.getAllByText(/seat average$/)[0]).toBeInTheDocument();
    // The measure over time, as the API sends it; each night reads out.
    expect(
      screen.getByText("Over time: 1.0% on Oct 19, 2026, 2.0% on Oct 26, 2026.")
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Over time:/)).toHaveLength(1);
    const line = screen.getByRole("group", { name: /^Votes against the party over time: 2 marks/ });
    line.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByText("Oct 26, 2026: 2.0%.")).toBeInTheDocument();
    line.blur();
    // The axis and the table print the API's figures.
    expect(screen.getAllByText("40%")[0]).toBeInTheDocument();
    // A thin row keeps its figure and draws no word.
    expect(
      screen.getByText(/Not enough record yet to compare \(6 party-line votes\)/)
    ).toBeInTheDocument();
    // The table is there for screen readers before it is shown.
    expect(screen.getAllByRole("table")).toHaveLength(2);
    // Its links stay out of the Tab order until the table is on screen.
    expect(screen.getAllByRole("link", { name: "Sam Ray" })[0]).toHaveAttribute("tabindex", "-1");
    await userEvent.click(screen.getByRole("button", { name: "Show as a table" }));
    expect(screen.getByRole("button", { name: "Hide the tables" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getAllByRole("link", { name: "Sam Ray" })[0]).toHaveAttribute(
      "href",
      "/politicians/sam-ray"
    );
    expect(screen.getAllByRole("link", { name: "Sam Ray" })[0]).not.toHaveAttribute("tabindex");
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

describe("stands out", () => {
  it("says nothing when no measure is genuinely unusual", () => {
    render(
      <main>
        <WhereTheyStand
          memberId="pat-lee"
          name="Pat Lee"
          chamber="senate"
          initial={{ ...DATA, standsOut: "Close to peers on every measure.", standsOutKey: null }}
        />
      </main>
    );
    expect(screen.queryByText(/Stands out/)).toBeNull();
  });
});

describe("an older API, during a rolling update", () => {
  it("draws each strip plain on its own figures, with no axis labels and no line", () => {
    const { container } = render(
      <main>
        <WhereTheyStand
          memberId="pat-lee"
          name="Pat Lee"
          chamber="senate"
          initial={olderApi(DATA)}
        />
      </main>
    );
    expect(screen.queryByText(/Over time:/)).toBeNull();
    // The strips still place every peer.
    expect(screen.getAllByRole("group", { name: /: 3 marks/ }).length).toBeGreaterThan(0);
    // Plain: drawn, with no readout, nothing printed from a missing field.
    expect(container.textContent).not.toMatch(/undefined|NaN/);
  });
});

describe("a whole-number measure", () => {
  const BILLS = measure({
    key: "billsMoved",
    label: "Bills past committee",
    unit: "count",
    value: 3,
    shown: "3 of 20",
    expected: 3,
    expectedBasis: "peer median",
    expectedShown: "median 3",
    distribution: [0, 3, 3],
    distributionIds: ["sam-ray", "pat-lee", "ari-cole"],
    distributionShown: ["0 of 9", "3 of 20", "3 of 41"],
  });
  const one = (m: PeerMeasure) => ({ ...DATA, measures: [m] });

  it("is drawn as a dot histogram: ties side by side, the member ringed", () => {
    const { container } = render(
      <WhereTheyStand memberId="pat-lee" name="Pat Lee" chamber="senate" initial={one(BILLS)} />
    );
    // One circle per peer in one drawing, the member's ringed.
    const circles = container.querySelectorAll("figure svg circle");
    expect(circles).toHaveLength(3);
    expect([...circles].filter((c) => c.getAttribute("stroke") === "var(--phos)")).toHaveLength(1);
    expect(screen.getByText("median 3")).toBeInTheDocument();
    // The table stays the text form.
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("keeps the strip for a response without the unit", () => {
    const noUnit = { ...BILLS, unit: undefined } as unknown as PeerMeasure;
    const { container } = render(
      <WhereTheyStand memberId="pat-lee" name="Pat Lee" chamber="senate" initial={one(noUnit)} />
    );
    // The strip: a small SVG per peer, and the member's larger dot.
    expect(container.querySelectorAll("figure svg circle")).toHaveLength(4);
    expect(screen.queryByText("median 3")).toBeInTheDocument();
  });

  it("still draws from an older API, its tick unlabelled", () => {
    const { container } = render(
      <WhereTheyStand
        memberId="pat-lee"
        name="Pat Lee"
        chamber="senate"
        initial={olderApi(one(BILLS))}
      />
    );
    expect(container.querySelectorAll("figure svg circle")).toHaveLength(3);
    expect(screen.queryByText("median 3")).toBeNull();
    expect(container.textContent).not.toMatch(/undefined|NaN/);
  });
});
