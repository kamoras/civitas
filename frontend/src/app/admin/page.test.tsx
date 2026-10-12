import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import payloads from "./__fixtures__/admin-payloads.json";
import AdminPage from "./page";

// The page loads three requests together: dashboard, run history, pipeline
// status. They used to be one Promise.all whose catch swallowed everything
// but a 401, so one failed request discarded the other two: System showed
// blank services, Pipelines "No pipeline runs recorded." and Data & ML
// "Loading…" for good, and nothing said a request had failed.

function stubBackend(failing: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split("?")[0];
      const json = (status: number, body: unknown) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      if (path.endsWith(failing)) return json(500, { detail: "boom" });
      if (path.endsWith("/admin/dashboard")) return json(200, payloads.dashboard);
      if (path.endsWith("/admin/pipeline/history")) return json(200, payloads.history);
      if (path.endsWith("/admin/pipeline/status")) return json(200, payloads.status);
      return json(404, { detail: "not in this test" });
    })
  );
}

function openTab(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
  return screen.getByRole("tabpanel");
}

beforeEach(() => {
  window.sessionStorage.setItem("civitas_admin_token", "local-test-token");
  window.history.replaceState(null, "", "/admin");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("admin dashboard load", () => {
  it("keeps the dashboard when the run history fails, and says so", async () => {
    stubBackend("/admin/pipeline/history");
    render(<AdminPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The run history didn't load (History failed: 500)");

    const data = openTab("Data & ML");
    expect(within(data).getByText("TOTAL RECORDS")).toBeInTheDocument();
    expect(within(data).queryByText("Not loaded.")).not.toBeInTheDocument();

    const system = openTab("System");
    expect(within(system).getByText("LiquidAI/lfm2.5-1.2b-instruct")).toBeInTheDocument();

    const pipelines = openTab("Pipelines");
    expect(within(pipelines).queryByText("No pipeline runs recorded.")).not.toBeInTheDocument();
    expect(within(pipelines).getByText("Not loaded.")).toBeInTheDocument();
  });

  it("keeps the run history when the dashboard fails, and never shows a zero for it", async () => {
    stubBackend("/admin/dashboard");
    render(<AdminPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The dashboard summary didn't load (Dashboard failed: 500)");

    const pipelines = openTab("Pipelines");
    expect(
      within(pipelines).getByRole("table", { name: "Pipeline run history" })
    ).toBeInTheDocument();

    const data = openTab("Data & ML");
    expect(within(data).queryByText("Loading…")).not.toBeInTheDocument();
    expect(within(data).getByText("Not loaded.")).toBeInTheDocument();

    const system = openTab("System");
    expect(within(system).queryByText("UNAVAILABLE")).not.toBeInTheDocument();
    expect(within(system).queryByText("0 B")).not.toBeInTheDocument();
  });

  // The same shape in the tabs' own requests: a failure became an empty
  // list, which read as "no visits" and "0 runs, 0 failed".
  it("says a tab's own request failed rather than showing nothing found", async () => {
    stubBackend("/admin/pipeline/trend");
    render(<AdminPage />);
    await screen.findByRole("tab", { name: "Pipelines" });

    const pipelines = openTab("Pipelines");
    expect(
      (await within(pipelines).findAllByText("Couldn't load this: the request failed.")).length
    ).toBeGreaterThan(0);
    expect(within(pipelines).queryByText("No runs in this range.")).not.toBeInTheDocument();
    expect(within(pipelines).getAllByText("couldn't load").length).toBeGreaterThan(0);
  });
});
