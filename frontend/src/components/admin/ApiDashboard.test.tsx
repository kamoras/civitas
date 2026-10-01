import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import type { ApiUsage } from "@/lib/api";

const usage: ApiUsage = {
  days: [
    { date: "2026-09-29", http: 3, mcp: 0, rateLimited: 0, errors: 0, mcpConnections: 0 },
    { date: "2026-09-30", http: 40, mcp: 12, rateLimited: 2, errors: 5, mcpConnections: 3 },
  ],
  totals: { http: 43, mcp: 12, rateLimited: 2, errors: 5, mcpConnections: 3 },
  byEndpoint: [
    { endpoint: "list_senators", http: 30, mcp: 10, rateLimited: 2, errors: 0 },
    { endpoint: "get_senator", http: 13, mcp: 2, rateLimited: 0, errors: 5 },
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
      "ENDPOINTHTTPMCP429ERRORS",
      "list_senators301020",
      "get_senator13205",
    ]);
    expect(screen.getByText("API requests today").parentElement).toHaveTextContent("40");
    expect(screen.getByText("MCP tool calls today").parentElement).toHaveTextContent("12");
    expect(screen.getByText("Rate-limited today").parentElement).toHaveTextContent(
      "5 other errors"
    );
    const results = await axe.run(document.body);
    expect(results.violations).toEqual([]);
  });
});
