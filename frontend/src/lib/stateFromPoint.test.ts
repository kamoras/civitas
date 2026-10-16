import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { stateAt, type StatesTopology } from "./stateFromPoint";

// The file the site serves, the election map's own.
const topo = JSON.parse(
  readFileSync(join(process.cwd(), "public/data/states-10m.json"), "utf8")
) as StatesTopology;

describe("stateAt", () => {
  it("places points in their state, DC and islands included", () => {
    expect(stateAt(topo, -82.99, 39.96)).toBe("OH"); // Columbus
    expect(stateAt(topo, -83.05, 42.33)).toBe("MI"); // Detroit
    expect(stateAt(topo, -77.03, 38.9)).toBe("DC");
    expect(stateAt(topo, -157.86, 21.31)).toBe("HI"); // Honolulu
    expect(stateAt(topo, -149.9, 61.22)).toBe("AK"); // Anchorage
    expect(stateAt(topo, -70.0, 41.9)).toBe("MA"); // Cape Cod
  });

  it("returns null outside every state", () => {
    expect(stateAt(topo, -40, 30)).toBeNull(); // the Atlantic
    expect(stateAt(topo, -0.13, 51.5)).toBeNull(); // London
  });
});
