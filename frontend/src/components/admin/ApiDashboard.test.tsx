import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import type { ApiUsage } from "@/lib/api";

const usage: ApiUsage = {
  days: [
    {
      date: "2026-09-29",
      http: 3,
      mcp: 0,
      rateLimited: 0,
      rejected: 0,
      serverErrors: 0,
      mcpConnections: 0,
    },
    {
      date: "2026-09-30",
      http: 40,
      mcp: 12,
      rateLimited: 2,
      rejected: 4,
      serverErrors: 1,
      mcpConnections: 3,
    },
  ],
  totals: { http: 43, mcp: 12, rateLimited: 2, rejected: 4, serverErrors: 1, mcpConnections: 3 },
  byEndpoint: [
    { endpoint: "list_senators", http: 30, mcp: 10, rateLimited: 2, rejected: 0, serverErrors: 0 },
    { endpoint: "get_senator", http: 13, mcp: 2, rateLimited: 0, rejected: 4, serverErrors: 1 },
  ],
  rejections: [
    { endpoint: "search_documents", parameter: "doc_type", reason: "literal_error", count: 4 },
  ],
};

vi.mock("@/lib/api", () => ({ fetchAdminApiUsage: vi.fn(async () => usage) }));

const { ApiDashboard } = await import("./ApiDashboard");

describe("ApiDashboard", () => {
  it("shows today's figures and the window per endpoint, as the API reports them", async () => {
    render(
      <main>
        <ApiDashboard token="t" />
      </main>
    );
    const table = await screen.findByRole("table", { name: /requests by endpoint/i });
    const rows = within(table).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "ENDPOINTHTTPMCP429REJECTED5XX",
      "list_senators3010200",
      "get_senator132041",
    ]);
    expect(screen.getByText("API requests today").parentElement).toHaveTextContent("40");
    expect(screen.getByText("MCP tool calls today").parentElement).toHaveTextContent("12");
    expect(screen.getByText("Rejected today").parentElement).toHaveTextContent("4");
    expect(screen.getByText("Server errors today").parentElement).toHaveTextContent("1");
    const reasons = await screen.findByRole("table", { name: /invalid public api requests/i });
    expect(
      within(reasons)
        .getAllByRole("row")
        .map((r) => r.textContent)
    ).toEqual(["ENDPOINTPARAMETERRULE BROKENCOUNT", "search_documentsdoc_typeliteral_error4"]);
    const results = await axe.run(document.body);
    expect(results.violations).toEqual([]);
  });
});
