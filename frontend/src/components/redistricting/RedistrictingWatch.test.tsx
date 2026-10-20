import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RedistrictingWatch from "./RedistrictingWatch";
import OddYearStates from "@/components/elections/season/OddYearStates";
import type { RedistrictingWatch as Watch } from "@/types/redistricting";

afterEach(cleanup);

const WATCH: Watch = {
  nextCycle: 2028,
  confirmed: false,
  inForceCycle: 2026,
  lastSettled: "2026-09-25",
  maps: [
    {
      state: "MO",
      stateName: "Missouri",
      map: "CD119",
      kind: "court_order",
      settled: "2026-09-25",
      reason: "2025 map stayed by the U.S. Supreme Court on 2026-09-25",
      source: {
        title: "Order in 26A388",
        issuedBy: "Supreme Court of the United States",
        url: "https://www.supremecourt.gov/x",
      },
    },
    {
      state: "CA",
      stateName: "California",
      map: "CD120",
      kind: "ballot_measure",
      settled: "2025-11-04",
      reason: "Proposition 50 (approved 2025-11-04) adopted the AB 604 map",
      source: {
        title: "Statement of Vote",
        issuedBy: "California Secretary of State",
        url: "https://www.sos.ca.gov/x",
      },
    },
  ],
};

describe("RedistrictingWatch", () => {
  it("says an unconfirmed cycle keeps the lines in force, never 'no change'", () => {
    render(<RedistrictingWatch watch={WATCH} />);
    expect(screen.getByText(/The 2028 House maps aren't confirmed yet/)).toBeTruthy();
    expect(screen.getByText(/lines in force for 2026/)).toBeTruthy();
    expect(screen.getByText(/recorded since September 25, 2026/)).toBeTruthy();
    expect(screen.getByText(/Missouri · Set or held by a Court · September 25, 2026/)).toBeTruthy();
    expect(screen.getByText(/California · Approved by voters/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "[ Supreme Court of the United States ↗ ]" })
    ).toHaveAttribute("href", "https://www.supremecourt.gov/x");
  });

  it("compact lists the states in a line, with the court ones marked", () => {
    const { container } = render(
      <RedistrictingWatch watch={{ ...WATCH, confirmed: true }} compact />
    );
    expect(screen.getByText(/confirmed in the record/)).toBeTruthy();
    expect(container.textContent).toMatch(/In force: Missouri \(court\), California\./);
    expect(screen.getByRole("link", { name: "How each was set →" })).toHaveAttribute(
      "href",
      "/elections/redistricting"
    );
  });

  it("renders nothing for an older backend", () => {
    const { container } = render(<RedistrictingWatch watch={undefined} />);
    expect(container.textContent).toBe("");
  });
});

describe("OddYearStates", () => {
  it("lists each state's contests and says plainly what isn't covered", () => {
    const { container } = render(
      <OddYearStates
        data={{
          year: 2027,
          states: [
            {
              state: "KY",
              stateName: "Kentucky",
              kinds: ["statewide_executive"],
              words: ["its statewide executive offices (the governor's among them)"],
              why: null,
              office: { url: "https://elect.ky.gov/", label: "x", sourceName: "y" },
            },
            {
              state: "MS",
              stateName: "Mississippi",
              kinds: ["statewide_executive", "legislature"],
              words: [
                "its statewide executive offices (the governor's among them)",
                "its legislature",
              ],
              why: null,
              office: { url: "https://www.sos.ms.gov/", label: "x", sourceName: "y" },
            },
          ],
        }}
      />
    );
    expect(screen.getByRole("heading", { name: "State elections in 2027" })).toBeTruthy();
    expect(container.textContent).toMatch(
      /Mississippi votes on its statewide executive offices \(the governor's among them\) and its legislature\./
    );
    expect(container.textContent).toMatch(
      /doesn't list these contests' candidates or read their counts/
    );
    expect(screen.getByRole("link", { name: "Kentucky's election office ↗" })).toHaveAttribute(
      "href",
      "https://elect.ky.gov/"
    );
  });
});
