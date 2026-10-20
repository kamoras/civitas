import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import ClosestSeats, { closestSeats } from "./ClosestSeats";
import type { MapSeat, RangedSeat } from "@/types/electionMap";

const base = (id: string, stateName: string, lean: number | null): MapSeat =>
  ({
    id,
    state: id.slice(0, 2),
    stateName,
    special: false,
    district: null,
    lean,
    leanLabel: lean == null ? null : lean > 0 ? `D+${lean}` : `R+${-lean}`,
    usualBin: null,
    dem: null,
    rep: null,
    incumbentParty: null,
    status: "no_lean",
    reason: "",
  }) as MapSeat;

const ranged = (id: string, stateName: string, lean: number, mean: number, strength: 0 | 1 | 2) =>
  ({
    ...base(id, stateName, lean),
    status: "range",
    mean,
    range80Label: `range ${id}`,
    difference: { strength, direction: mean > lean ? "D" : "R", seatPolls: true },
  }) as unknown as RangedSeat;

const seats: MapSeat[] = [
  ranged("GA", "Georgia", 2, -1, 1),
  ranged("OH", "Ohio", -9, -4, 2),
  ranged("MI", "Michigan", 1, 3, 0),
  base("TX", "Texas", -12),
  base("ME", "Maine", null),
];

describe("closestSeats", () => {
  it("orders the usual view by distance from even in the usual vote", () => {
    expect(closestSeats(seats, "usual").map((s) => s.id)).toEqual(["MI", "GA", "OH", "TX"]);
  });
  it("orders the polls view by the middle of the range, ranged seats only", () => {
    expect(closestSeats(seats, "polls").map((s) => s.id)).toEqual(["GA", "MI", "OH"]);
  });
  it("lists the Difference view's disagreeing seats, strongest first", () => {
    expect(closestSeats(seats, "diff").map((s) => s.id)).toEqual(["OH", "GA"]);
  });
});

describe("ClosestSeats", () => {
  it("is 44px rows that say the lean in words and open the seat", () => {
    const onSelect = vi.fn();
    render(<ClosestSeats seats={seats} view="usual" onSelect={onSelect} />);
    const list = screen.getByRole("region", { name: "Closest seats" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((r) => r.textContent)).toEqual([
      "MichiganD+1›",
      "GeorgiaD+2›",
      "OhioR+9›",
      "TexasR+12›",
    ]);
    for (const r of rows) expect(r.className).toContain("min-h-11");
    fireEvent.click(rows[1]);
    expect(onSelect).toHaveBeenCalledWith("GA");
  });
});
