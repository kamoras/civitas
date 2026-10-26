import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { SourceHealthView } from "./SourceHealth";
import { SourcesTable } from "@/components/admin/SourcesDashboard";
import type { AdminSourceHealth, SourceHealth } from "@/types/sources";

const walled = {
  host: "efdsearch.senate.gov",
  reads: ["Fetch + parse Senate STOCK Act periodic transaction reports."],
  state: "walled" as const,
  stateLabel: "Behind a bot wall",
  since: "2026-10-23",
  lastGood: "2026-10-22",
  reason: "a bot wall's challenge (HTTP 403)",
  line: "Not readable since Oct 23, 2026: a bot wall's challenge (HTTP 403). Last good read Oct 22, 2026.",
};
const ok = {
  host: "api.congress.gov",
  reads: ["Fetch modules for Congress.gov and Senate.gov APIs."],
  state: "ok" as const,
  stateLabel: "Read normally",
  since: null,
  lastGood: "2026-10-26",
  reason: null,
  line: "Answered when last asked. Last good read Oct 26, 2026.",
};
const notRead = {
  host: "scdb.la.psu.edu",
  reads: ["The academic records the justice appointer estimate reads."],
  state: "not_read" as const,
  stateLabel: "Not read this run",
  since: null,
  lastGood: null,
  reason: null,
  line: "No read on record yet.",
};
const health: SourceHealth = {
  asOf: "2026-10-26T12:00",
  counts: { ok: 1, walled: 1, refused: 0, unavailable: 0, not_read: 1 },
  summary:
    "Of 3 sources, 1 answered when last asked, 1 can't be read now and 1 have no recent read on record.",
  sources: [walled, notRead, ok],
};

describe("SourceHealthView", () => {
  it("boxes the sources that can't be read, and lists every source", async () => {
    render(
      <main>
        <h3>Can each source be read now</h3>
        <SourceHealthView health={health} />
      </main>
    );
    const box = screen.getByRole("heading", { level: 4, name: "efdsearch.senate.gov" });
    expect(box).toBeInTheDocument();
    expect(screen.getAllByText(/Not readable since Oct 23, 2026/).length).toBeGreaterThan(0);
    // A source not asked is not boxed as a failure.
    expect(screen.queryByRole("heading", { level: 4, name: "scdb.la.psu.edu" })).toBeNull();
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[2]).toHaveTextContent("Not read this run");
    expect(rows[2]).toHaveTextContent("none on record");
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("SourcesTable (admin)", () => {
  it("adds the runs and the failure counts", () => {
    const admin: AdminSourceHealth = {
      ...health,
      windowDays: 30,
      sources: [
        {
          ...walled,
          lastAttempt: "2026-10-26T03:10:00+00:00",
          runs: ["stock trades · senate_ptr"],
          requests30d: 4,
          failedRequests30d: 4,
          failedSteps30d: 3,
        },
      ],
    };
    render(<SourcesTable data={admin} />);
    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(row).toHaveTextContent("stock trades · senate_ptr");
    expect(row).toHaveTextContent("3 steps, 4 of 4 requests");
  });
});
