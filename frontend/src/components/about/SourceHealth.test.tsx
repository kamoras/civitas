import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { SourceHealthView } from "./SourceHealth";
import { SourcesTable } from "@/components/admin/SourcesDashboard";
import type { AdminSourceHealth, SourceHealth } from "@/types/sources";

const walled = {
  host: "efdsearch.senate.gov",
  publisher: "Senate Office of Public Records (eFD)",
  takes: "Senators' stock trade reports and annual financial disclosures",
  state: "refused" as const,
  stateLabel: "Refusing Civitas",
  since: "2026-10-23",
  lastGood: "2026-10-22",
  line: "Not readable since Oct 23, 2026: the site is turning Civitas away. Last good read Oct 22, 2026.",
};
const ok = {
  host: "api.congress.gov",
  publisher: "Congress.gov (Library of Congress)",
  takes: "Bills, their actions and cosponsors, members and nominations",
  state: "ok" as const,
  stateLabel: "Read normally",
  since: null,
  lastGood: "2026-10-26",
  line: "Answered when last asked. Last good read Oct 26, 2026.",
};
const notRead = {
  host: "scdb.la.psu.edu",
  publisher: "Supreme Court Database",
  takes: "Every justice's vote in every argued case",
  state: "not_read" as const,
  stateLabel: "Not read this run",
  since: null,
  lastGood: null,
  line: "No read on record yet.",
};
const health: SourceHealth = {
  asOf: "2026-10-26T12:00",
  counts: { ok: 1, walled: 0, refused: 1, unavailable: 0, not_read: 1 },
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
    const box = screen.getByRole("heading", {
      level: 4,
      name: "Senate Office of Public Records (eFD)",
    });
    expect(box).toBeInTheDocument();
    expect(screen.getAllByText(/Not readable since Oct 23, 2026/).length).toBeGreaterThan(0);
    // A source not asked is not boxed as a failure.
    expect(screen.queryByRole("heading", { level: 4, name: "Supreme Court Database" })).toBeNull();
    const rows = within(
      screen.getByRole("list", { name: "Every source Civitas reads, and whether it answered" })
    ).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("Not read this run");
    expect(rows[1]).toHaveTextContent("No read on record yet.");
    expect(rows[2]).toHaveTextContent("Last good read Oct 26, 2026.");
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
      outbound: [{ host: "ntfy.example", modules: ["app.ops_alerts"] }],
      sources: [
        {
          ...walled,
          reads: ["Fetch + parse Senate STOCK Act periodic transaction reports."],
          reason: "Akamai's Access Denied to the terms agreement (browser)",
          lastAttempt: "2026-10-26T03:10:00+00:00",
          lastAttemptText: "Oct 26, 2026",
          readBy: ["Stock trades"],
          runs: ["stock trades · senate_ptr"],
          requests30d: 4,
          failedRequests30d: 4,
          failedSteps30d: 3,
          failuresText: "3 failed steps; 4 of 4 requests failed",
          alsoSendsTo: [],
        },
      ],
    };
    render(<SourcesTable data={admin} />);
    const row = within(
      screen.getByRole("list", { name: "Every external source and its health" })
    ).getAllByRole("listitem")[0];
    expect(row).toHaveTextContent("Last asked Oct 26, 2026 · read by Stock trades");
    expect(row).toHaveTextContent("Steps on record: stock trades · senate_ptr");
    expect(row).toHaveTextContent("3 failed steps; 4 of 4 requests failed");
    expect(row).toHaveTextContent("Recorded: Akamai's Access Denied");
    const outbound = screen.getByRole("list", { name: "Outbound services" });
    expect(outbound).toHaveTextContent("ntfy.example");
  });
});
