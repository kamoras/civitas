import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import ElectionsChapter from "./page";
import LimitationsChapter from "../limitations/page";
import { ACTION_CENTER_HREF } from "@/lib/routes";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("next/navigation", () => ({ usePathname: () => "/about/elections" }));

/** The backend's results endpoint, answering with `liveStates` — or down. */
function backend(liveStates: string[] | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      liveStates === null
        ? Promise.reject(new Error("ECONNREFUSED"))
        : { ok: true, status: 200, json: async () => ({ liveStates, races: [] }) }
    )
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the elections methodology chapter", () => {
  it("links the Action Center by its named-tab href and doesn't claim every moment is posted", async () => {
    backend(["GA"]);
    render(await ElectionsChapter());
    const section = document.getElementById("election-night")!;
    expect(within(section).getByRole("link", { name: "Action Center" })).toHaveAttribute(
      "href",
      ACTION_CENTER_HREF
    );
    expect(section).not.toHaveTextContent(/The same moments are posted/);
    expect(section).toHaveTextContent(/posts fewer moments than the feed shows/);
    expect(section).toHaveTextContent(/redrawn for this election/);
    expect(screen.getAllByRole("heading").length).toBeGreaterThan(0);
  });

  it("lists the states read live from the backend's own list, not a hand-typed one", async () => {
    backend(["WV", "GA", "NM"]);
    render(await ElectionsChapter());
    expect(document.getElementById("election-night")).toHaveTextContent(
      /Three states publish a count we can read this way: Georgia, New Mexico and West Virginia\./
    );
  });

  it("says the sync slows to hourly once counts settle, not five minutes throughout", async () => {
    backend(["GA"]);
    render(await ElectionsChapter());
    expect(document.getElementById("election-night")).toHaveTextContent(
      /Every five minutes while counts are moving \(hourly once none has moved for a day\)/
    );
  });

  it("names no number when the list can't be read", async () => {
    backend(null);
    render(await ElectionsChapter());
    const section = document.getElementById("election-night")!;
    expect(section).toHaveTextContent(/Only some states publish a count we can read this way/);
    expect(section).not.toHaveTextContent(/Fifteen|fifteen/);
  });
});

describe("the limitations chapter's live-results entry", () => {
  it("counts the states the backend reads live", async () => {
    backend([
      "AR",
      "CO",
      "GA",
      "IA",
      "ID",
      "MT",
      "ND",
      "NE",
      "NM",
      "RI",
      "SC",
      "UT",
      "VA",
      "WA",
      "WV",
      "OH",
    ]);
    render(await LimitationsChapter());
    expect(screen.getByRole("heading", { name: "Live results cover sixteen states" })).toBeTruthy();
    expect(document.body).toHaveTextContent(/only sixteen publish one in a form we can read/);
  });

  it("says 'some states' rather than a stale number when the backend is down", async () => {
    backend(null);
    render(await LimitationsChapter());
    expect(
      screen.getByRole("heading", { name: "Live results cover only some states" })
    ).toBeTruthy();
  });
});
