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
  it("prints the backend's sentences beside a not-final tag and links each state's results", () => {
    const data: ResultsSummary = { phase, states: [state("CO", "Colorado")] };
    render(<ElectionNightList data={data} />);
    expect(screen.getByText(data.states[0].lines[0])).toBeTruthy();
    expect(screen.getByText("LEADING · NOT FINAL")).toBeTruthy();
    expect(screen.getByRole("link", { name: "[ COLORADO RESULTS ]" }).getAttribute("href")).toBe(
      "/elections/states/CO"
    );
  });

  it("says official only when the state lists the count as official", () => {
    render(
      <ElectionNightList
        data={{ phase, states: [state("WY", "Wyoming", { status: "official", official: 2 })] }}
      />
    );
    expect(screen.getByText("LEADS · OFFICIAL COUNT")).toBeTruthy();
    expect(screen.queryByText(/NOT FINAL/)).toBeNull();
  });

  it("keeps every counting state one tap away", () => {
    const names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf"];
    const states = names.map((n, i) => state(`S${i}`, n));
    render(<ElectionNightList data={{ phase, states }} />);
    for (const s of states) {
      expect(document.querySelector(`a[href="${s.href}"]`)).toBeTruthy();
    }
    expect(screen.getByText(/7 states counting/)).toBeTruthy();
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
