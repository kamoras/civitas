import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import MonitorsTab from "./MonitorsTab";
import type { NationalMonitor } from "@/lib/api";

function monitor(over: Partial<NationalMonitor>): NationalMonitor {
  return {
    id: 1,
    slug: "a-concern",
    title: "A concern",
    description: "",
    category: "general",
    status: "watching",
    policyAreas: [],
    createdAt: "2026-05-01",
    updatedAt: "2026-10-08",
    lastArticleDate: "2026-09-24",
    updateCount: 12,
    closedOn: null,
    ...over,
  };
}

vi.mock("@/lib/api", () => ({
  fetchMonitorDetail: vi.fn(() => new Promise(() => {})),
  fetchMonitors: vi.fn(async () => ({
    monitors: [monitor({})],
    recentlyClosed: [
      monitor({
        id: 2,
        slug: "an-older-concern",
        title: "An older concern",
        status: "closed",
        createdAt: "2026-06-02",
        lastArticleDate: "2026-07-30",
        closedOn: "2026-08-31",
      }),
    ],
    recentlyClosedDays: 60,
  })),
}));

describe("Ongoing tab", () => {
  it("lists monitors closed recently, with when each was tracked and closed", async () => {
    render(<MonitorsTab />);
    const section = (
      await screen.findByRole("heading", { name: /Closed in the last 60 days/ })
    ).closest("section") as HTMLElement;
    expect(within(section).getByText("An older concern")).toBeInTheDocument();
    expect(within(section).getByText(/Tracked Jun 2 – Jul 30 · closed Aug 31/)).toBeInTheDocument();
    expect(screen.getAllByText("A concern")).toHaveLength(1);
  });

  it("offers an open monitor's own feed, to follow without an account", async () => {
    render(<MonitorsTab />);
    fireEvent.click(await screen.findByRole("button", { name: /A concern/ }));
    const link = screen.getByRole("link", { name: "Follow this monitor without an account" });
    expect(link.getAttribute("href")).toBe("/feed/monitors/a-concern.xml");
    expect(link.getAttribute("type")).toBe("application/atom+xml");
  });
});
