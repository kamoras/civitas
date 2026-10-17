import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SeatLean } from "./MethodsLedger";

const lean = { cookPvi: 0, computedLean: null, linesCongress: null };

describe("SeatLean", () => {
  it("names a state's lean as Civitas's own, computed with Cook's formula", () => {
    render(
      <dl>
        <SeatLean lean={lean} chamber="senate" />
      </dl>
    );
    expect(screen.getByText(/State lean/)).toBeInTheDocument();
    expect(screen.getByText(/computed by Civitas with Cook's formula/)).toBeInTheDocument();
    expect(screen.queryByText(/Cook PVI/)).toBeNull();
  });

  it("names a House seat's lean as Cook's published PVI", () => {
    render(
      <dl>
        <SeatLean lean={{ ...lean, cookPvi: 5 }} chamber="house" />
      </dl>
    );
    expect(screen.getByText(/Cook PVI/)).toBeInTheDocument();
  });
});
