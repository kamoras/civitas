import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import DistrictLocator, {
  __resetDistrictLookupCache,
  type FoundDistricts,
} from "./DistrictLocator";
import { useYourDistrict, YOUR_DISTRICT_KEY } from "@/hooks/useYourDistrict";
import { YOUR_STATE_KEY, useYourState } from "@/hooks/useYourState";
import { __resetSessionTokenCache } from "@/hooks/useSessionToken";
import type { Topology } from "@/lib/districtAt";

const STATES = readFileSync(join(process.cwd(), "public/data/states-10m.json"), "utf8");

// Providence, Rhode Island, and a made-up line through it at -71.41.
const LON = -71.4321;
const LAT = 41.8234;
const LINE = -71.41;

/** A topology of axis-aligned rectangles, one arc each, quantized at 1e-5°. */
function rects(
  objects: Record<string, { x0: number; y0: number; x1: number; y1: number; props: object }[]>
): Topology {
  const q = (v: number) => Math.round(v * 1e5);
  const arcs: [number, number][][] = [];
  const out: Topology["objects"] = {};
  for (const [name, list] of Object.entries(objects)) {
    out[name] = {
      geometries: list.map(({ x0, y0, x1, y1, props }) => {
        const pts = [
          [q(x0), q(y0)],
          [q(x1), q(y0)],
          [q(x1), q(y1)],
          [q(x0), q(y1)],
          [q(x0), q(y0)],
        ];
        arcs.push(
          pts.map((p, i) => (i === 0 ? p : [p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]])) as [
            number,
            number,
          ][]
        );
        return { type: "Polygon" as const, arcs: [[arcs.length - 1]], properties: props };
      }),
    };
  }
  return { transform: { scale: [1e-5, 1e-5], translate: [0, 0] }, arcs, objects: out };
}

const LOOKUP = rects({
  house: [
    { x0: -71.9, y0: 41.1, x1: LINE, y1: 42.1, props: { d: 1 } },
    { x0: LINE, y0: 41.1, x1: -71.1, y1: 42.1, props: { d: 2 } },
  ],
});
const TOWNS = rects({
  towns: [{ x0: -71.47, y0: 41.77, x1: -71.37, y1: 41.86, props: { n: "Rivertown city" } }],
  roads: [],
});
const LEGISLATURE = rects({
  sldu: [{ x0: -71.9, y0: 41.1, x1: -71.1, y1: 42.1, props: { d: "7" } }],
  sldl: [{ x0: -71.9, y0: 41.1, x1: -71.1, y1: 42.1, props: { d: "12" } }],
});

const DISTRICTS = [
  { district: 1, places: ["Rivertown city", "Westfield town"] },
  { district: 2, places: ["Rivertown city", "Eastport city"] },
];

function geolocation(ok: boolean) {
  const getCurrentPosition = vi.fn((success: PositionCallback, failure?: PositionErrorCallback) => {
    if (ok)
      success({ coords: { longitude: LON, latitude: LAT, accuracy: 20 } } as GeolocationPosition);
    else failure?.({ code: 1, message: "denied" } as GeolocationPositionError);
  });
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: { getCurrentPosition },
  });
  return getCurrentPosition;
}

const status = () => document.querySelector("[data-locator-status]") as HTMLElement;

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  __resetSessionTokenCache();
  __resetDistrictLookupCache();
  fetchMock = vi.fn(async (url: string) => {
    if (url === "/data/states-10m.json") return new Response(STATES);
    if (url === "/data/lookup/congress-119/RI.json") return new Response(JSON.stringify(LOOKUP));
    if (url === "/data/lookup/congress-119/RI.towns.json")
      return new Response(JSON.stringify(TOWNS));
    if (url === "/data/lookup/election-2026/RI.legislature.json")
      return new Response(JSON.stringify(LEGISLATURE));
    return new Response("", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The directory's use of it: the found district kept for the tab. */
function Directory({ query = "" }: { query?: string }) {
  const { district, setDistrict } = useYourDistrict();
  const { setState } = useYourState();
  return (
    <>
      <DistrictLocator
        state="RI"
        lines="congress-119"
        linesLabel="the lines today's members were elected on"
        districts={DISTRICTS}
        query={query}
        onFound={(f: FoundDistricts) => {
          setState(f.state);
          setDistrict({ state: f.state, lines: "congress-119", house: f.house! });
        }}
        onOtherState={() => {}}
      />
      <span data-testid="kept">{district ? district.house : ""}</span>
    </>
  );
}

describe("DistrictLocator", () => {
  it("never asks for a location or fetches anything by itself", () => {
    const ask = geolocation(true);
    render(<Directory />);
    expect(ask).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("finds the district on the device: no request carries the coordinates", async () => {
    geolocation(true);
    render(<Directory />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Find my district from my location" }));
    });
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        "Your location is in district 1, on the lines today's members were elected on."
      )
    );
    expect(screen.getByTestId("kept")).toHaveTextContent("1");
    for (const [url, init] of fetchMock.mock.calls) {
      expect(["/data/states-10m.json", "/data/lookup/congress-119/RI.json"]).toContain(url);
      expect(init?.body).toBeUndefined();
      expect(String(url)).not.toMatch(/71\.4|41\.8/);
    }
  });

  it("keeps only the district and the state, in this tab's sessionStorage", async () => {
    geolocation(true);
    render(<Directory />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Find my district from my location" }));
    });
    await waitFor(() => expect(sessionStorage.getItem(YOUR_DISTRICT_KEY)).not.toBeNull());
    expect(Object.keys(sessionStorage).sort()).toEqual([YOUR_DISTRICT_KEY, YOUR_STATE_KEY]);
    expect(JSON.parse(sessionStorage.getItem(YOUR_DISTRICT_KEY)!)).toEqual({
      state: "RI",
      lines: "congress-119",
      house: 1,
    });
    expect(sessionStorage.getItem(YOUR_STATE_KEY)).toBe("RI");
    // No coordinate anywhere in what is kept.
    expect(JSON.stringify({ ...sessionStorage })).not.toMatch(/71\.4|41\.8/);
    expect(localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("falls back to the town filter when the location is refused", async () => {
    geolocation(false);
    render(<Directory />);
    fireEvent.click(screen.getByRole("button", { name: "Find my district from my location" }));
    await waitFor(() => expect(status()).toHaveTextContent("Location not available."));
    expect(sessionStorage.length).toBe(0);
  });

  it("names the state legislative districts on the ballot's legislature section", async () => {
    geolocation(true);
    const found = vi.fn();
    render(
      <DistrictLocator
        state="RI"
        lines="election-2026"
        linesLabel="the 2026 election's lines"
        districts={[]}
        query=""
        legislature
        onFound={found}
        officialLookup={{
          href: "https://vote.example.gov/",
          label: "Rhode Island's own voter lookup",
        }}
      />
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Find my district from my location" }));
    });
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        "Your location is in state senate district 7 and state house district 12"
      )
    );
    expect(found).toHaveBeenCalledWith(
      expect.objectContaining({ state: "RI", sldu: "7", sldl: "12" }),
      "location"
    );
  });

  it("links the official lookup, saying that site would get the address", () => {
    render(<Directory />);
    const link = screen.getByRole("link", { name: /house\.gov's Find Your Representative/ });
    expect(link).toHaveAttribute(
      "href",
      "https://www.house.gov/representatives/find-your-representative"
    );
    expect(link.closest("p")).toHaveTextContent(
      "You would be giving your address to that site, not to Civitas."
    );
  });
});

describe("a split town", () => {
  it("offers the town's map only when the typed town is split", () => {
    const { rerender } = render(<Directory query="Westfield" />);
    expect(screen.queryByRole("button", { name: /on a map/ })).toBeNull();
    rerender(<Directory query="rivertown" />);
    expect(screen.getByRole("button", { name: "Show Rivertown on a map" })).toBeInTheDocument();
  });

  it("settles the district from the town's map or its list, with nothing sent", async () => {
    function Page() {
      const [q] = useState("Rivertown");
      return <Directory query={q} />;
    }
    const { container } = render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Show Rivertown on a map" }));
    const second = await screen.findByRole("button", { name: /District 2: the part of Rivertown/ });
    expect(
      screen.getByRole("button", { name: /District 1: the part of Rivertown/ })
    ).toHaveTextContent("the district also covers Westfield");
    // A tap on the map's part is the same choice as the list's button.
    fireEvent.click(container.querySelector('svg path[data-district="1"]')!);
    expect(screen.getByTestId("kept")).toHaveTextContent("1");
    fireEvent.click(second);
    expect(screen.getByTestId("kept")).toHaveTextContent("2");
    expect(second).toHaveAttribute("aria-pressed", "true");
    expect(status()).toHaveTextContent("You picked district 2 for your part of Rivertown.");
    // The only requests are the state's own files, no query, no body.
    expect(fetchMock.mock.calls.map(([u]) => u)).toEqual([
      "/data/lookup/congress-119/RI.json",
      "/data/lookup/congress-119/RI.towns.json",
    ]);
    // The map is hidden from assistive technology; the list is its text
    // alternative, and the whole thing passes axe.
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // jsdom computes no colours, so contrast is checked in the browser.
    const r = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});
