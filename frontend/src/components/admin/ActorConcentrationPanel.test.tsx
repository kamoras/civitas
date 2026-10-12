import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ActorConcentration } from "@/lib/api";

const data: ActorConcentration = {
  weeks: [
    { weekStart: "2026-09-28", issues: 0, hhi: null, top: null, president: 0, noGovernment: 0 },
    {
      weekStart: "2026-10-05",
      issues: 4,
      hhi: 0.375,
      top: { label: "president", name: "President Pat Example", issues: 2 },
      president: 2,
      noGovernment: 1,
    },
  ],
};

vi.mock("@/lib/api", () => ({ fetchAdminActorConcentration: vi.fn(async () => data) }));

const { ActorConcentrationPanel } = await import("./ActionCenterDashboard");

describe("ActorConcentrationPanel", () => {
  it("shows this week's concentration, and its leading actor", async () => {
    render(<ActorConcentrationPanel token="t" />);
    expect(await screen.findByText("President Pat Example (2)")).toBeTruthy();
    expect(screen.getAllByText("0.38").length).toBeGreaterThan(0);
    expect(screen.getByText("4 issues")).toBeTruthy();
  });
});
