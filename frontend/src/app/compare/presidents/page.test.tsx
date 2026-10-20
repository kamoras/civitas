import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import ComparePresidentsPage from "./page";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));

// The API still sends a deprecated 0-100 `score`; the page must not show it.
const score = { publicMandate: 50, effectiveness: 44, historicalLegacy: 72, overall: 61 };

const presidents = [
  {
    id: "doe-47",
    name: "Jane Doe",
    party: "R",
    number: 47,
    termStart: "2025-01-20",
    termEnd: null,
    score: { ...score, historicalLegacy: null },
  },
  {
    id: "roe-44",
    name: "John Roe",
    party: "D",
    number: 44,
    termStart: "2009-01-20",
    termEnd: "2017-01-20",
    score,
  },
];

const formerBreakdown = {
  publicMandate: {
    score: 50,
    components: [],
    facts: { approval: 47.97, approvalMean: 50.887, approvalVsEra: 1.04, approvalVsEraMean: 0.02 },
  },
  effectiveness: {
    score: 44,
    components: [],
    facts: {
      jobsPerYear: 2.257,
      jobsMean: 1.4414,
      gdpGrowth: 2.3016,
      gdpMean: 3.0832,
      gdpSince: true,
      gdpRelative: 1.8609,
      gdpRelativeMean: 2.2333,
    },
  },
  historicalLegacy: {
    score: 72,
    components: [],
    facts: {
      points: 664,
      pointsMean: 549.4,
      historiansRank: 12,
      historiansRated: 45,
      surveyYear: 2021,
    },
  },
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
  beforeEach(() => serve({ "roe-44": formerBreakdown }));

  it("preselects the president the profile linked from, and asks for a second", async () => {
    await renderPage({ left: "doe-47" });
    expect(screen.getByLabelText("First president")).toHaveValue("doe-47");
    expect(screen.getByLabelText("Second president")).toHaveValue("");
    expect(screen.getByText("Choose a second president to compare.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sets the API's figures side by side, with no score and computing none", async () => {
    await renderPage({ left: "doe-47", right: "roe-44" });
    const row = (name: string) =>
      within(screen.getByRole("rowheader", { name }).closest("tr")!)
        .getAllByRole("cell")
        .map((c) => c.textContent);

    expect(row("Historians' rank (C-SPAN survey, 2021; their ranking)")).toEqual([
      "Not rated",
      "#12 of 45",
    ]);
    // None of Civitas's own 0-100 scores.
    for (const name of ["Public Mandate", "Effectiveness", "Historical Legacy"]) {
      expect(screen.queryByRole("rowheader", { name })).not.toBeInTheDocument();
    }
    expect(screen.queryByText("61")).not.toBeInTheDocument();
    expect(row("Average approval (vs all presidents)")).toEqual(["—", "48.0% vs 50.9%"]);
    expect(row("Approval against its era, points (vs the typical president)")).toEqual([
      "—",
      "+1.0 vs +0.0",
    ]);
    expect(row("Jobs a year (vs presidencies since 1939)")).toEqual(["—", "2.26M vs 1.44M"]);
    expect(
      row("Growth per person vs 13 peer economies, points (vs presidencies since 1947)")
    ).toEqual(["—", "+1.9 vs +2.2"]);
    // A postwar term has no prewar figure.
    expect(row("Real GDP growth a year, before 1947 (vs presidencies before 1947)")).toEqual([
      "—",
      "—",
    ]);
    expect(row("Historians' points (vs all presidents)")).toEqual(["—", "664 vs 549"]);
    expect(screen.getByRole("link", { name: "John Roe" })).toHaveAttribute(
      "href",
      "/politicians/roe-44"
    );
    expect(screen.getByText(/44th president/)).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    await renderPage({ left: "doe-47", right: "roe-44" });
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
