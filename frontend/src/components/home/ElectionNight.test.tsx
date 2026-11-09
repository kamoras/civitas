import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ResultsSummary, ResultsSummaryState } from "@/types/election";

vi.mock("next/server", () => ({ connection: async () => undefined }));
import ElectionNight, { ElectionNightList } from "./ElectionNight";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function state(code: string, name: string, extra: Partial<ResultsSummaryState> = {}) {
  return {
    state: code,
    stateName: name,
    racesCounted: 2,
    official: 0,
    status: "not_final" as const,
    lines: [`${name}'s U.S. Senate: Pat Placeholder (D) leads with 52.1%.`],
    source: `${name} Secretary of State`,
    lastChangeAt: "2026-11-04T03:50:00Z",
    href: `/elections/states/${code}`,
    ...extra,
  };
}

const phase = {
  phase: "election_day" as const,
  electionDate: "2026-11-03",
  resultsUntil: "2026-11-17",
  lastResultChange: "2026-11-04T03:50:00Z",
};

describe("ElectionNightList", () => {
  it("is one line: the states counting, the last change, the way to the count", () => {
    const data: ResultsSummary = { phase, states: [state("CO", "Colorado")] };
    render(<ElectionNightList data={data} />);
    expect(screen.getByText(/1 state counting · last change Nov 3, 10:50 PM ET/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "See the count" }).getAttribute("href")).toBe(
      "/elections"
    );
    expect(screen.getByRole("link", { name: "How results are read" }).getAttribute("href")).toBe(
      "/about/elections"
    );
    // No state's lines: the count itself is on /elections.
    expect(screen.queryByText(data.states[0].lines[0])).toBeNull();
  });

  it("counts every state without listing them", () => {
    const states = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf"].map((n, i) =>
      state(`S${i}`, n)
    );
    render(<ElectionNightList data={{ phase: { ...phase, lastResultChange: null }, states }} />);
    expect(screen.getByText("7 states counting")).toBeTruthy();
    expect(document.querySelectorAll("li")).toHaveLength(0);
  });
});

describe("ElectionNight", () => {
  it("renders nothing outside the results window", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ phase: { ...phase, phase: "campaign" }, states: [] }),
      }))
    );
    expect(await ElectionNight()).toBeNull();
  });

  it("renders nothing when the backend can't answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("down");
      })
    );
    expect(await ElectionNight()).toBeNull();
  });
});
