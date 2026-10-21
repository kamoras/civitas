import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import axe from "axe-core";
import RaceMap from "./RaceMap";
import { STATE_TILES } from "@/lib/electionMap";
import type { ReadoutMark } from "@/types/readout";

// The real vendored atlas, so the callouts are placed from real geometry.
const ATLAS = JSON.parse(
  readFileSync(join(__dirname, "../../../public/data/states-10m.json"), "utf8")
);

const MARKS: Record<string, ReadoutMark> = Object.fromEntries(
  Object.keys(STATE_TILES).map((st) => [
    st,
    { id: st, label: `State ${st}`, shown: "R+1", href: `/elections/states/${st}` },
  ])
);

afterEach(() => vi.unstubAllGlobals());

function map() {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ATLAS }));
  return render(
    <main>
      <RaceMap
        label="Partisan lean by state"
        marks={MARKS}
        getFillColor={() => "#222"}
        getHoverFillColor={() => "#333"}
      />
    </main>
  );
}

describe("RaceMap", () => {
  it("is one Tab stop per drawing, not one per state, and every state is named", async () => {
    const { container } = map();
    await waitFor(() =>
      expect(container.querySelectorAll("path[data-mark]").length).toBeGreaterThan(48)
    );
    // The paths react-simple-maps makes Tab stops are taken out.
    expect(container.querySelectorAll("path[tabindex='0'], [role=button]")).toHaveLength(0);
    // Tiles for a phone, shapes from 640px: one group each (CSS shows one).
    const groups = screen.getAllByRole("group", { name: /^Partisan lean by state: \d+ marks/ });
    expect(groups).toHaveLength(2);
    expect(container.querySelectorAll("[tabindex='0']")).toHaveLength(3); // + the tiles' scroll box
    // Arrow keys walk the states row by row, west to east; Enter opens one.
    groups[1].focus();
    await userEvent.keyboard("{Home}");
    const first = Object.entries(STATE_TILES).sort(
      ([, [ac, ar]], [, [bc, br]]) => ar - br || ac - bc
    )[0][0];
    expect(screen.getAllByText(`State ${first}: R+1.`).length).toBeGreaterThan(0);
    expect(document.querySelector("a[aria-hidden='true']")).toHaveAttribute(
      "href",
      `/elections/states/${first}`
    );
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("gives every state too small to tap its own labelled box, the box a mark", async () => {
    const { container } = map();
    await waitFor(() => expect(container.querySelectorAll("rect[data-mark='RI']")).toHaveLength(1));
    // Each small state: the tile and the callout box; a large one only the tile.
    for (const st of ["VT", "NH", "MA", "RI", "CT", "NJ", "DE", "MD"]) {
      expect(container.querySelectorAll(`rect[data-mark='${st}']`)).toHaveLength(1);
      expect(container.querySelectorAll(`g[data-mark='${st}']`)).toHaveLength(1);
    }
    expect(container.querySelectorAll("rect[data-mark='TX']")).toHaveLength(0);
  });

  it("outlines the open state on the map itself", async () => {
    const { container } = map();
    await waitFor(() => expect(container.querySelector("path[data-mark='TX']")).not.toBeNull());
    const texas = container.querySelector<SVGPathElement>("path[data-mark='TX']")!;
    expect(texas.style.strokeWidth).toBe("0.5");
    const shapes = screen.getAllByRole("group", { name: /^Partisan lean by state/ })[1];
    await userEvent.hover(texas);
    await userEvent.pointer({ target: texas, coords: { clientX: 1, clientY: 1 } });
    expect(texas.style.strokeWidth).toBe("2.5");
    shapes.focus();
    await userEvent.keyboard("{Escape}");
    expect(texas.style.strokeWidth).toBe("0.5");
  });

  // A phone's map is tiles, each a 24px target (WCAG 2.5.8): the shapes
  // put Alabama at 17x27 and New Jersey at 6x13 pixels.
  it("draws every state as a 24px tile for phones", () => {
    map();
    const region = screen.getByRole("region", { name: "Map of the states, as tiles" });
    const tiles = [...region.querySelectorAll("g[data-mark]")];
    expect(tiles.length).toBeGreaterThanOrEqual(50);
    for (const t of tiles) {
      const r = t.querySelector("rect")!;
      expect(Number(r.getAttribute("width"))).toBeGreaterThanOrEqual(24);
      expect(Number(r.getAttribute("height"))).toBeGreaterThanOrEqual(24);
    }
  });
});
