import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ballotHouseLines, districtsAt, featureAt, type Topology } from "./districtAt";
import fixture from "./districtAt.fixture.json";

// Two districts side by side, sharing the edge x = 1, the second with a
// hole (a third district inside it) — quantized like the real files.
const TOPO: Topology = {
  transform: { scale: [0.001, 0.001], translate: [0, 0] },
  arcs: [
    // 0: shared edge, (1,0) -> (1,1)
    [
      [1000, 0],
      [0, 1000],
    ],
    // 1: district 1's other three sides, (1,1) -> (0,1) -> (0,0) -> (1,0)
    [
      [1000, 1000],
      [-1000, 0],
      [0, -1000],
      [1000, 0],
    ],
    // 2: district 2's other three sides, (1,0) -> (2,0) -> (2,1) -> (1,1)
    [
      [1000, 0],
      [1000, 0],
      [0, 1000],
      [-1000, 0],
    ],
    // 3: the hole, a square (1.4,0.4)-(1.6,0.6)
    [
      [1400, 400],
      [200, 0],
      [0, 200],
      [-200, 0],
      [0, -200],
    ],
  ],
  objects: {
    house: {
      geometries: [
        { type: "Polygon", arcs: [[0, 1]], properties: { d: 1 } },
        { type: "Polygon", arcs: [[~0, 2], [3]], properties: { d: 2 } },
        { type: "Polygon", arcs: [[~3]], properties: { d: 3 } },
      ],
    },
    sldu: { geometries: [{ type: "Polygon", arcs: [[0, 1]], properties: { d: "1A" } }] },
  },
};

describe("ballotHouseLines", () => {
  it("reads the seated Congress's lines on a new map, else the sitting ones", () => {
    expect(ballotHouseLines(2026, true)).toBe("congress-120");
    expect(ballotHouseLines(2026, false)).toBe("congress-119");
    expect(ballotHouseLines(2028, false)).toBe("congress-120");
  });
});

describe("districtsAt", () => {
  it("places points on either side of a shared line", () => {
    expect(districtsAt(TOPO, 0.999, 0.5).house).toBe(1);
    expect(districtsAt(TOPO, 1.001, 0.5).house).toBe(2);
  });

  it("puts a point on the shared line in exactly one district", () => {
    const hits = TOPO.objects.house.geometries.filter(
      (g) =>
        featureAt({ ...TOPO, objects: { house: { geometries: [g] } } }, "house", 1, 0.5) !== null
    );
    expect(hits).toHaveLength(1);
  });

  it("reads a hole as not part of the district around it", () => {
    expect(districtsAt(TOPO, 1.5, 0.5).house).toBe(3);
    expect(districtsAt(TOPO, 1.3, 0.5).house).toBe(2);
  });

  it("returns null outside every district, and legislative keys as strings", () => {
    expect(districtsAt(TOPO, 3, 3)).toEqual({ house: null, sldu: null, sldl: null });
    expect(districtsAt(TOPO, 0.5, 0.5)).toEqual({ house: 1, sldu: "1A", sldl: null });
  });
});

// Real shapes: census blocks whose internal point lies within 100m of a
// district line, with the district the Census block equivalency file
// assigns each and where the build's own measurement placed it
// (districtAt.fixture.json, written with the files). The browser must place
// every one exactly where the build did, and the build's gate holds the
// whole state to its accuracy floor; here, near the lines, at least 95%.
const NEAR_LINE_POINTS = fixture.files as {
  file: string;
  points: [number, number, string, string, string | null][];
}[];

describe("the shipped district files near their lines", () => {
  it("has points to check", () => expect(NEAR_LINE_POINTS.length).toBeGreaterThan(0));
  for (const { file, points } of NEAR_LINE_POINTS) {
    it(`${file}: the browser places near-line blocks as the build measured`, () => {
      const path = join(process.cwd(), "public/data/lookup", file);
      expect(existsSync(path)).toBe(true);
      const topo = JSON.parse(readFileSync(path, "utf8")) as Topology;
      const got = points.map(([lon, lat, layer]) => {
        const d = districtsAt(topo, lon, lat)[layer as "house" | "sldu" | "sldl"];
        return d == null ? null : String(d);
      });
      expect(got).toEqual(points.map((p) => p[4]));
      const right = points.filter((p, i) => got[i] === p[3]).length;
      expect(right / points.length).toBeGreaterThanOrEqual(0.95);
    });
  }
});
