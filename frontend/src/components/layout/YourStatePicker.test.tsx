import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import YourStatePicker from "./YourStatePicker";
import { YOUR_STATE_KEY } from "@/hooks/useYourState";
import { __resetSessionTokenCache } from "@/hooks/useSessionToken";

const TOPO = readFileSync(join(process.cwd(), "public/data/states-10m.json"), "utf8");
const LON = -82.98765;
const LAT = 39.96123;

function geolocation(ok: boolean) {
  const getCurrentPosition = vi.fn((success: PositionCallback, failure?: PositionErrorCallback) => {
    if (ok) success({ coords: { longitude: LON, latitude: LAT } } as GeolocationPosition);
    else failure?.({ code: 1, message: "denied" } as GeolocationPositionError);
  });
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: { getCurrentPosition },
  });
  return getCurrentPosition;
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  __resetSessionTokenCache();
  fetchMock = vi.fn(async () => new Response(TOPO, { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("YourStatePicker", () => {
  it("never asks for a location by itself", () => {
    const ask = geolocation(true);
    render(<YourStatePicker />);
    expect(ask).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("places the location on the device: no request carries the coordinates", async () => {
    geolocation(true);
    render(<YourStatePicker />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use my location" }));
    });
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Ohio set for this tab.")
    );
    expect(screen.getByLabelText("Your state")).toHaveValue("OH");
    // The only request is the site's own shapes file, with no body and no
    // query: the coordinates never leave the page.
    for (const [url, init] of fetchMock.mock.calls) {
      expect(String(url)).toBe("/data/states-10m.json");
      expect(init?.body).toBeUndefined();
      expect(String(url)).not.toMatch(/82\.98|39\.96/);
    }
  });

  it("keeps only the state, in this tab's sessionStorage, never localStorage or a cookie", async () => {
    geolocation(true);
    render(<YourStatePicker />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use my location" }));
    });
    await waitFor(() => expect(sessionStorage.getItem(YOUR_STATE_KEY)).toBe("OH"));
    expect(Object.keys(sessionStorage)).toEqual([YOUR_STATE_KEY]);
    expect(localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("falls back quietly to the list when the location is refused", async () => {
    geolocation(false);
    render(<YourStatePicker />);
    fireEvent.click(screen.getByRole("button", { name: "Use my location" }));
    expect(screen.getByRole("status")).toHaveTextContent("Location not available.");
    expect(screen.getByLabelText("Your state")).toHaveValue("");
    expect(sessionStorage.length).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("takes a pick from the list", () => {
    render(<YourStatePicker />);
    fireEvent.change(screen.getByLabelText("Your state"), { target: { value: "GA" } });
    expect(sessionStorage.getItem(YOUR_STATE_KEY)).toBe("GA");
  });

  it("in the header, clears its message box when focus moves on, so it covers nothing", () => {
    geolocation(false);
    render(
      <>
        <YourStatePicker />
        <button type="button">Elsewhere</button>
      </>
    );
    fireEvent.click(screen.getByRole("button", { name: "Use my location" }));
    expect(screen.getByRole("status")).toHaveTextContent("Location not available.");
    act(() => screen.getByRole("button", { name: "Elsewhere" }).focus());
    expect(screen.getByRole("status")).toHaveTextContent("");
  });
});

describe("YourStatePicker: what setting a state leads to", () => {
  it("confirms the state and links its members and ballot, in the live region", () => {
    // Mobile spec P9: picking a state said nothing and offered nothing.
    render(<YourStatePicker size="lg" />);
    fireEvent.change(screen.getByLabelText("Your state"), { target: { value: "OH" } });
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Ohio set for this tab.");
    expect(within(status).getByRole("link", { name: "Ohio's members →" })).toHaveAttribute(
      "href",
      "/politicians?state=OH"
    );
    expect(within(status).getByRole("link", { name: "Ohio's ballot →" })).toHaveAttribute(
      "href",
      "/elections/states/OH"
    );
    for (const a of within(status).getAllByRole("link")) expect(a.className).toContain("min-h-11");
  });

  it("says nothing more when the state is cleared", () => {
    render(<YourStatePicker size="lg" />);
    fireEvent.change(screen.getByLabelText("Your state"), { target: { value: "OH" } });
    fireEvent.change(screen.getByLabelText("Your state"), { target: { value: "" } });
    expect(screen.getByRole("status")).toHaveTextContent("");
  });
});
