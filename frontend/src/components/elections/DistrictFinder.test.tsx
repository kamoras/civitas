import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DistrictFinder from "./DistrictFinder";
import type { RaceWithCandidates } from "@/types/election";

function race(district: number, counties: string[]): RaceWithCandidates {
  return {
    id: `2026-HOUSE-GA-${district}`,
    cycleYear: 2026,
    office: "H",
    state: "GA",
    district,
    isSpecial: false,
    pvi: 0,
    pviLevel: "district",
    candidateSource: "confirmed",
    counties,
    candidates: [],
  } as unknown as RaceWithCandidates;
}

const RACES = [
  race(1, ["Appling County", "Bryan County", "Effingham County (part)"]),
  race(3, ["Carroll County", "Coweta County"]),
  race(12, ["Effingham County (part)", "Emanuel County"]),
];

describe("DistrictFinder", () => {
  it("picks a district from a county with no typing", async () => {
    const onPick = vi.fn();
    render(<DistrictFinder races={RACES} onPick={onPick} picked={null} />);

    await userEvent.click(screen.getByRole("button", { name: "C" }));
    await userEvent.click(screen.getByRole("button", { name: "Carroll County" }));

    expect(onPick).toHaveBeenCalledWith("2026-HOUSE-GA-3");
  });

  it("offers the choices when a county spans districts instead of guessing", async () => {
    // 13% of US counties span more than one district — Effingham is one.
    const onPick = vi.fn();
    render(<DistrictFinder races={RACES} onPick={onPick} picked={null} />);

    await userEvent.click(screen.getByRole("button", { name: "E" }));
    expect(screen.getByText("Effingham County")).toBeInTheDocument();
    expect(screen.getByText("split:")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "GA-12" }));
    expect(onPick).toHaveBeenCalledWith("2026-HOUSE-GA-12");
  });

  it("treats a (part) county as the same place as the whole county", async () => {
    render(<DistrictFinder races={RACES} onPick={vi.fn()} picked={null} />);
    await userEvent.click(screen.getByRole("button", { name: "E" }));
    // One entry, not "Effingham County" and "Effingham County (part)".
    expect(screen.getAllByText(/^Effingham County$/)).toHaveLength(1);
    expect(screen.queryByText(/\(part\)/)).not.toBeInTheDocument();
  });

  it("can clear the choice and show every district again", async () => {
    const onPick = vi.fn();
    render(<DistrictFinder races={RACES} onPick={onPick} picked="2026-HOUSE-GA-3" />);
    await userEvent.click(screen.getByRole("button", { name: /show all districts/i }));
    expect(onPick).toHaveBeenCalledWith(null);
  });

  it("renders nothing when no race carries counties", () => {
    const { container } = render(
      <DistrictFinder races={[race(1, [])]} onPick={vi.fn()} picked={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("never asks for an address", () => {
    render(<DistrictFinder races={RACES} onPick={vi.fn()} picked={null} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText(/Nothing is typed, sent or stored/i)).toBeInTheDocument();
  });
});
