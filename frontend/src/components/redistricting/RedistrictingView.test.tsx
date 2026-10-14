import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import RedistrictingStateView from "./RedistrictingView";
import RedrawnSeatNote from "./RedrawnSeatNote";
import type { RedistrictedSeat, StateRedistricting } from "@/types/redistricting";

const fetchRedistrictedSeat = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  fetchRedistrictedSeat,
  redistrictingDownloads: (st: string) => ({
    csv: `/api/elections/redistricting/${st}/overlap.csv`,
    json: `/api/elections/redistricting/${st}`,
  }),
}));

const share = (district: string, sharePct: number, residents = 1000) => ({
  district,
  number: Number(district.split("-")[1]),
  residents,
  sharePct,
});

const data: StateRedistricting = {
  state: "ZZ",
  stateName: "Examplia",
  cycle: "2026",
  fromCongress: 119,
  toCongress: 120,
  minResidents: 50,
  map: {
    reason: "Act 1",
    title: "Act 1 of 2026",
    issuedBy: "Examplia Legislature",
    url: "https://example.gov/act1",
  },
  summary: {
    seats: 2,
    population: 2000,
    unchangedSeats: 0,
    oldWithoutSuccessor: 0,
    residentsMoved: 400,
    residentsMovedPct: 20,
    leanBefore: { R: 1, EVEN: 0, D: 1 },
    leanAfter: { R: 2, EVEN: 0, D: 0 },
    seatsChangedLean: 1,
    meanPviBefore: 1,
    meanPviAfter: 4,
    meanResidentShift: 3.2,
  },
  newDistricts: [
    {
      district: "ZZ-1",
      number: 1,
      population: 1000,
      sources: [share("ZZ-1", 78, 780), share("ZZ-2", 22, 220)],
      sliverResidents: 0,
      predecessor: "ZZ-1",
      predecessorNumber: 1,
      predecessorSharePct: 78,
      pviBefore: 3,
      pviBeforeWeighted: 1.4,
      pviAfter: 9,
      pviShift: 6,
      leanBefore: "R",
      leanAfter: "R",
      unchanged: false,
      counties: ["North County"],
      places: ["Rivertown city"],
    },
    {
      district: "ZZ-2",
      number: 2,
      population: 1000,
      sources: [share("ZZ-2", 82, 820), share("ZZ-1", 18, 180)],
      sliverResidents: 0,
      predecessor: "ZZ-2",
      predecessorNumber: 2,
      predecessorSharePct: 82,
      pviBefore: -2,
      pviBeforeWeighted: -1.1,
      pviAfter: 1,
      pviShift: 3,
      leanBefore: "D",
      leanAfter: "R",
      unchanged: false,
      counties: ["South County"],
      places: ["Lakeside town"],
    },
  ],
  oldDistricts: [
    {
      district: "ZZ-1",
      number: 1,
      population: 960,
      destinations: [share("ZZ-1", 81), share("ZZ-2", 19)],
      sliverResidents: 0,
      pvi: 3,
      hasSuccessor: true,
      counties: ["North County"],
      places: ["Rivertown city"],
    },
    {
      district: "ZZ-2",
      number: 2,
      population: 1040,
      destinations: [share("ZZ-2", 79), share("ZZ-1", 21)],
      sliverResidents: 0,
      pvi: -2,
      hasSuccessor: true,
      counties: ["South County"],
      places: ["Lakeside town"],
    },
  ],
  cells: [
    ["ZZ-1", "ZZ-1", 780],
    ["ZZ-1", "ZZ-2", 180],
    ["ZZ-2", "ZZ-1", 220],
    ["ZZ-2", "ZZ-2", 820],
  ],
  sources: { overlap: "Census", pviBefore: null, pviAfter: null },
  fromCongressSitting: true,
};

describe("RedistrictingStateView", () => {
  it("says what changed per district, findable by a place", async () => {
    render(<RedistrictingStateView data={data} />);
    expect(
      screen.getByText(
        "Most of this district's residents (78%) were in the old 1st; its lean moved from R+3 to R+9."
      )
    ).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox"), "Lakeside");
    expect(screen.queryByText(/were in the old 1st/)).not.toBeInTheDocument();
    expect(screen.getByText(/were in the old 2nd/)).toBeInTheDocument();
  });

  it("lists where each old district went", async () => {
    render(<RedistrictingStateView data={data} />);
    await userEvent.click(screen.getByRole("button", { name: /OLD DISTRICTS/ }));
    expect(screen.getByRole("button", { name: /OLD DISTRICTS/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(
      screen.getByText("Of the old 1st's residents, 81% are in the new 1st and 19% in the new 2nd.")
    ).toBeInTheDocument();
  });

  it("offers the matrix and the downloads, and passes axe", async () => {
    const { container } = render(<RedistrictingStateView data={data} />);
    expect(screen.getByText("[ DOWNLOAD CSV ]")).toHaveAttribute(
      "href",
      "/api/elections/redistricting/ZZ/overlap.csv"
    );
    await userEvent.click(screen.getByText(/OVERLAP MATRIX/));
    expect(screen.getByRole("table")).toBeInTheDocument();
    const result = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("RedrawnSeatNote", () => {
  const seat: RedistrictedSeat = {
    seat: data.oldDistricts[0],
    state: "ZZ",
    stateName: "Examplia",
    cycle: "2026",
    fromCongressSitting: true,
  };

  it("says where the seat's residents went while its member sits", async () => {
    fetchRedistrictedSeat.mockResolvedValueOnce(seat);
    render(<RedrawnSeatNote state="ZZ" district={1} />);
    await waitFor(() => expect(screen.getByText(/81% are in the new 1st/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /WHAT CHANGED/ })).toHaveAttribute(
      "href",
      "/elections/redistricting/ZZ#old-1"
    );
  });

  it("says nothing once the new Congress sits, or for a state that kept its lines", async () => {
    fetchRedistrictedSeat.mockResolvedValueOnce({ ...seat, fromCongressSitting: false });
    const { container, rerender } = render(<RedrawnSeatNote state="ZZ" district={1} />);
    await waitFor(() => expect(fetchRedistrictedSeat).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    fetchRedistrictedSeat.mockResolvedValueOnce({ ...seat, seat: null });
    rerender(<RedrawnSeatNote state="WY" district={0} />);
    await waitFor(() => expect(fetchRedistrictedSeat).toHaveBeenCalledWith("WY", 0));
    expect(container).toBeEmptyDOMElement();
  });
});
