import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import RaceMap from "./RaceMap";

// The real vendored atlas, so the callouts are placed from real geometry.
const ATLAS = JSON.parse(
  readFileSync(join(__dirname, "../../../public/data/states-10m.json"), "utf8")
);

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
      />
    );

    // Three buttons for each small state: the shape, the callout, and the
    // phone's tile (CSS shows the tiles or the shapes; jsdom has both).
    await waitFor(() => expect(screen.getAllByRole("button", { name: "RI" })).toHaveLength(3));
    const ri = screen.getAllByRole("button", { name: "RI" });
    expect(ri).toHaveLength(3);
    for (const st of ["VT", "NH", "MA", "CT", "NJ", "DE", "MD"]) {
      expect(screen.getAllByRole("button", { name: st })).toHaveLength(3);
    }
    // A large state gets no callout.
    expect(screen.getAllByRole("button", { name: "TX" })).toHaveLength(2);

    const callout = ri.find((el) => el.querySelector("line"))!;
    await userEvent.click(callout);
    expect(onStateClick).toHaveBeenCalledWith("RI");
    callout.focus();
    await userEvent.keyboard("{Enter}");
    expect(onStateClick).toHaveBeenCalledTimes(2);
  });

  it("outlines the state that has keyboard focus (paths have no outline of their own)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ATLAS }));
    render(
      <RaceMap
        selectedState={null}
        onStateClick={vi.fn()}
        getFillColor={() => "#222"}
        getHoverFillColor={() => "#333"}
      />
    );
    await waitFor(() => expect(screen.getAllByRole("button", { name: "TX" })).toHaveLength(2));
    const texas = screen
      .getAllByRole("button", { name: "TX" })
      .find((el) => el.tagName.toLowerCase() === "path")!;
    expect(texas.style.strokeWidth).toBe("0.5");
    act(() => texas.focus());
    expect(texas.style.strokeWidth).toBe("2.5");
    expect(texas.style.stroke).not.toBe("#0a1a0a");
    act(() => texas.blur());
    expect(texas.style.strokeWidth).toBe("0.5");
  });

  // A phone's map is tiles, each a 24px target (WCAG 2.5.8): the shapes
  // put Alabama at 17x27 and New Jersey at 6x13 pixels.
  it("draws every state as a 24px tile button for phones", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ATLAS }));
    const onStateClick = vi.fn();
    render(
      <RaceMap
        selectedState={null}
        onStateClick={onStateClick}
        getFillColor={() => "#222"}
        getHoverFillColor={() => "#333"}
      />
    );
    const region = screen.getByRole("region", { name: "Map of the states, as tiles" });
    const tiles = [...region.querySelectorAll("g[role=button]")];
    expect(tiles.length).toBeGreaterThanOrEqual(50);
    for (const t of tiles) {
      const r = t.querySelector("rect")!;
      expect(Number(r.getAttribute("width"))).toBeGreaterThanOrEqual(24);
      expect(Number(r.getAttribute("height"))).toBeGreaterThanOrEqual(24);
    }
    const nj = tiles.find((t) => t.getAttribute("aria-label") === "NJ") as HTMLElement;
    act(() => nj.focus());
    await userEvent.keyboard(" ");
    expect(onStateClick).toHaveBeenCalledWith("NJ");
  });
});
