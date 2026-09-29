import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { OpsAlerts } from "./OverviewDashboard";

describe("OpsAlerts", () => {
  it("shows each alert's subject, time and body, newest first as served", async () => {
    // Inside <main>, as on the admin page, so axe's landmark rule applies.
    render(
      <main>
        <OpsAlerts
          alerts={[
            {
              subject: "Justice loyalty not measured",
              body: "The justice step refreshed the voting record but not the score: the Supreme Court Database could not be read.",
              at: "2026-09-29T11:49:54",
            },
            {
              subject: "SupplementaryPipelineRun: step justice_scorecards failed",
              body: "Supplementary step 'justice_scorecards' failed and the run continued without it.",
              at: "2026-09-27T06:25:34",
            },
          ]}
        />
      </main>
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Justice loyalty not measured");
    expect(items[0]).toHaveTextContent("the Supreme Court Database could not be read");
    expect(items[0].querySelector("time")).toHaveAttribute("dateTime", "2026-09-29T11:49:54");
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("says when there are none, and when they haven't loaded", () => {
    const { rerender } = render(<OpsAlerts alerts={[]} />);
    expect(screen.getByText("No alerts recorded.")).toBeInTheDocument();
    rerender(<OpsAlerts alerts={undefined} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
