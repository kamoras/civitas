import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/server", () => ({ connection: vi.fn(async () => undefined) }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => null }));
vi.mock("@/components/layout/Footer", () => ({ default: () => null }));

import ElectionArchiveIndexPage from "./page";

function backend(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body)))
  );
}

afterEach(() => vi.unstubAllGlobals());

const ARCHIVED = {
  cycle: 2026,
  electionDay: "2026-11-03",
  recordAsOf: "2026-11-20T15:00:00Z",
  mapAsOf: "2026-11-03T03:55:00Z",
  hasMap: true,
  stillCounting: false,
  races: 470,
  counted: 300,
  official: 120,
  measures: 40,
  states: 50,
};

describe("ElectionArchiveIndexPage", () => {
  it("before any archive, says what it will hold and when the first one starts", async () => {
    backend({ cycles: [], current: { cycle: 2026, electionDay: "2026-11-03", phase: "campaign" } });
    render(await ElectionArchiveIndexPage());
    expect(screen.getByText("Nothing archived yet")).toBeTruthy();
    expect(
      screen.getByText(/The first will be the 2026 general election, on November 3, 2026/)
    ).toBeTruthy();
    expect(screen.getByText(/the seat map as it stood the day before/)).toBeTruthy();
  });

  it("on election day, before the first copy, says it is on its way", async () => {
    backend({
      cycles: [],
      current: { cycle: 2026, electionDay: "2026-11-03", phase: "election_day" },
    });
    render(await ElectionArchiveIndexPage());
    expect(screen.getByText(/The 2026 count is being read now/)).toBeTruthy();
  });

  it("lists each archived election and names the next one", async () => {
    backend({
      cycles: [ARCHIVED],
      current: { cycle: 2028, electionDay: "2028-11-07", phase: "campaign" },
    });
    render(await ElectionArchiveIndexPage());
    expect(
      screen.getByRole("link", { name: /The 2026 general election/ }).getAttribute("href")
    ).toBe("/elections/2026");
    expect(screen.getByText(/120 listed official/)).toBeTruthy();
    expect(screen.getByText(/Next: the 2028 general election, on November 7, 2028/)).toBeTruthy();
  });
});
