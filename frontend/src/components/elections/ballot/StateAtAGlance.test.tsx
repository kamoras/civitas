import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import StateAtAGlance from "./StateAtAGlance";

const fetchMapSeats = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchMapSeats }));

const SEATS = {
  cycle: 2026,
  asOf: "2026-10-16T12:00:00Z",
  stale: false,
  pollSource: {
    attribution: "Wikipedia contributors",
    license: "CC BY-SA 4.0",
    url: "https://creativecommons.org/licenses/by-sa/4.0/",
  },
  seats: [
    {
      id: "2026-SEN-ZZ",
      chamber: "senate",
      state: "ZZ",
      district: null,
      special: false,
      leanLabel: "R+6",
      status: "range",
      range80Label: "D+9 to R+4",
      close: true,
      reason: null,
    },
    {
      id: "2026-HOUSE-ZZ-1",
      chamber: "house",
      state: "ZZ",
      district: 1,
      special: false,
      leanLabel: "R+20",
      status: "range",
      range80Label: "R+10 to R+30",
      close: false,
      reason: null,
    },
  ],
};

afterEach(() => {
  cleanup();
  fetchMapSeats.mockReset();
});

describe("StateAtAGlance", () => {
  it("links each Senate seat's place on the map, with the map's own labels", async () => {
    fetchMapSeats.mockResolvedValue(SEATS);
    render(<StateAtAGlance state="ZZ" stateName="Zedland" redrawn />);
    const seat = await screen.findByRole("link", {
      name: /SENATE.*USUAL VOTE R\+6.*POLLS D\+9 TO R\+4/,
    });
    expect(seat).toHaveAttribute(
      "href",
      "/elections/map?chamber=senate&view=polls&seat=2026-SEN-ZZ"
    );
    // House seats are the district drawers' business, not this strip's.
    expect(screen.queryByText(/R\+10 TO R\+30/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /NEW HOUSE MAP: WHAT CHANGED/ })).toHaveAttribute(
      "href",
      "/elections/redistricting/ZZ"
    );
    // A range shown carries its source and licence beside it.
    expect(screen.getByText(/Wikipedia contributors/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CC BY-SA 4.0" })).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by-sa/4.0/"
    );
    expect(fetchMapSeats).toHaveBeenCalledWith({ state: "ZZ" });
  });

  it("keeps the new-map link when the map isn't shown, and is absent with neither", async () => {
    fetchMapSeats.mockResolvedValue(null);
    const { rerender } = render(<StateAtAGlance state="ZZ" stateName="Zedland" redrawn />);
    expect(await screen.findByRole("link", { name: /NEW HOUSE MAP/ })).toBeInTheDocument();
    expect(screen.queryByText(/USUAL VOTE/)).not.toBeInTheDocument();
    rerender(<StateAtAGlance state="ZZ" stateName="Zedland" redrawn={false} />);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});
