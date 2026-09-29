import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { OpsAlerts } from "./OverviewDashboard";

const open = {
  subject: "Justice loyalty not measured",
  body: "The justice step refreshed the voting record but not the score: the Supreme Court Database could not be read.",
  at: "2026-09-29T11:49:54",
  condition: "justice-loyalty-unmeasured",
  resolvedAt: null,
  open: true,
};
const resolved = {
  subject: "House pipeline overrun",
  body: "The House pipeline has been running for 8:10:00.",
  at: "2026-09-28T13:00:00",
  condition: "overrun-house",
  resolvedAt: "2026-09-28T14:05:00",
  open: false,
};
const event = {
  subject: "Nightly pipeline crashed",
  body: "RuntimeError: boom",
  at: "2026-09-27T06:00:00",
  condition: null,
  resolvedAt: null,
  open: false,
};

describe("OpsAlerts", () => {
  it("puts open alerts under Active and the rest under Earlier, as the API marks them", async () => {
    // Inside <main>, as on the admin page, so axe's landmark rule applies.
    render(
      <main>
        <OpsAlerts alerts={[open, resolved, event]} />
      </main>
    );
    const active = screen.getByRole("region", { name: "Active alerts" });
    const earlier = screen.getByRole("region", { name: "Earlier alerts" });
    expect(within(active).getAllByRole("listitem")).toHaveLength(1);
    expect(active).toHaveTextContent("Justice loyalty not measured");
    expect(active).toHaveTextContent("the Supreme Court Database could not be read");
    expect(within(earlier).getAllByRole("listitem")).toHaveLength(2);
    expect(earlier).toHaveTextContent("House pipeline overrun");
    expect(earlier).toHaveTextContent("Resolved");
    expect(within(earlier).getAllByRole("listitem")[1]).not.toHaveTextContent("Resolved");
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("says nothing is wrong when nothing is open", () => {
    render(<OpsAlerts alerts={[resolved]} />);
    expect(screen.getByText("Nothing is wrong right now.")).toBeInTheDocument();
  });

  it("says when there are none, and when they haven't loaded", () => {
    const { rerender } = render(<OpsAlerts alerts={[]} />);
    expect(screen.getByText("No alerts recorded.")).toBeInTheDocument();
    rerender(<OpsAlerts alerts={undefined} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
