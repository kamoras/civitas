import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import ComparePresidentsPage from "./page";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));

const score = (overall: number, legacy: number | null, dims: number) => ({
  publicMandate: 50,
  effectiveness: 44,
  agencyAlignment: 26,
  historicalLegacy: legacy,
  overall,
  dimensionsAvailable: dims,
});

const presidents = [
  {
    id: "trump-47",
    name: "Donald J. Trump",
    party: "R",
    number: 47,
    termStart: "2025-01-20",
    termEnd: null,
    score: score(50.67, null, 3),
  },
  {
    id: "obama-44",
    name: "Barack Obama",
    party: "D",
    number: 44,
    termStart: "2009-01-20",
    termEnd: "2017-01-20",
    score: score(48.2, 72, 4),
  },
];

const obamaBreakdown = {
  publicMandate: { score: 50, components: [], facts: { approval: 47.97, approvalMean: 50.887 } },
  effectiveness: {
    score: 44,
    components: [],
    facts: { jobsPerYear: 2.257, jobsMean: 1.4414, gdpGrowth: 2.3016, gdpMean: 3.0832 },
  },
  agencyAlignment: { score: 26, components: [], facts: { finalizedPct: 50, finalizedMean: 53.7 } },
  historicalLegacy: { score: 72, components: [], facts: { points: 664, pointsMean: 549.4 } },
};

function serve(breakdowns: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/presidents")) return { ok: true, json: async () => presidents };
      const id = url.match(/presidents\/([^/]+)\/score-breakdown/)?.[1] ?? "";
      return id in breakdowns
        ? { ok: true, json: async () => breakdowns[id] }
        : { ok: false, json: async () => ({}) };
    })
  );
}

async function renderPage(params: { left?: string; right?: string }) {
  render(await ComparePresidentsPage({ searchParams: Promise.resolve(params) }));
}

describe("ComparePresidentsPage", () => {
  beforeEach(() => serve({ "obama-44": obamaBreakdown }));

  it("preselects the president the scorecard linked from, and asks for a second", async () => {
    await renderPage({ left: "trump-47" });
    expect(screen.getByLabelText("First president")).toHaveValue("trump-47");
    expect(screen.getByLabelText("Second president")).toHaveValue("");
    expect(screen.getByText("Choose a second president to compare.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sets the scores and the API's figures side by side, without computing any", async () => {
    await renderPage({ left: "trump-47", right: "obama-44" });
    const row = (name: string) =>
      within(screen.getByRole("rowheader", { name }).closest("tr")!)
        .getAllByRole("cell")
        .map((c) => c.textContent);

    expect(row("Presidential Score")).toEqual(["51", "48"]);
    expect(row("Historical Legacy")).toEqual(["Not scored", "72"]);
    expect(row("Average approval (vs all presidents)")).toEqual(["—", "48.0% vs 50.9%"]);
    expect(row("Jobs a year (vs presidencies since 1939)")).toEqual(["—", "2.26M vs 1.44M"]);
    expect(row("Historians' points (vs all presidents)")).toEqual(["—", "664 vs 549"]);
    expect(screen.getByRole("link", { name: "Barack Obama" })).toHaveAttribute(
      "href",
      "/politicians/obama-44"
    );
    expect(screen.getByText(/44th president/)).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    await renderPage({ left: "trump-47", right: "obama-44" });
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("throws when the president list can't be read, rather than offering nobody", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 502 }))
    );
    await expect(renderPage({})).rejects.toThrow("HTTP 502");
  });
});
