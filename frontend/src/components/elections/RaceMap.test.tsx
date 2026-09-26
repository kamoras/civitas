import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import RaceMap from "./RaceMap";

// The real vendored atlas, so the callouts are placed from real geometry.
const ATLAS = JSON.parse(readFileSync(join(__dirname, "../../../public/data/states-10m.json"), "utf8"));

afterEach(() => vi.unstubAllGlobals());

describe("RaceMap", () => {
  it("gives every state too small to tap its own labelled box", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ATLAS }));
    const onStateClick = vi.fn();
    render(
      <RaceMap
        selectedState={null}
        onStateClick={onStateClick}
        getFillColor={() => "#222"}
        getHoverFillColor={() => "#333"}
      />,
    );

    // Two buttons for each small state: the shape and the callout.
    const ri = await screen.findAllByRole("button", { name: "RI" });
    expect(ri).toHaveLength(2);
    for (const st of ["VT", "NH", "MA", "CT", "NJ", "DE", "MD"]) {
      expect(screen.getAllByRole("button", { name: st })).toHaveLength(2);
    }
    // A large state gets no callout.
    expect(screen.getAllByRole("button", { name: "TX" })).toHaveLength(1);

    const callout = ri.find((el) => el.tagName.toLowerCase() === "g")!;
    await userEvent.click(callout);
    expect(onStateClick).toHaveBeenCalledWith("RI");
    callout.focus();
    await userEvent.keyboard("{Enter}");
    expect(onStateClick).toHaveBeenCalledTimes(2);
  });
});
