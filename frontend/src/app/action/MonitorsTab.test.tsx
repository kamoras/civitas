import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import MonitorsTab from "./MonitorsTab";
import type { NationalMonitor } from "@/lib/api";
import * as api from "@/lib/api";
import { closestLabel, closestVote, position } from "@/components/action/OnTheRecord";
import type { IssueVote } from "@/types/action";

// jsdom has no scrollIntoView; a row opened from a "Tracked in" link
// scrolls itself into view on a timer that can fire in a later test.
Element.prototype.scrollIntoView = vi.fn();

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

function vote(over: Partial<IssueVote>): IssueVote {
  return {
    chamber: "house",
    congress: 119,
    session: 2,
    number: 1,
    date: "2026-05-14",
    question: "On Agreeing to the Resolution",
    result: "Failed",
    title: "Directing the President to remove Armed Forces from hostilities with Iran",
    yeas: 212,
    nays: 212,
    rejected: true,
    margin: 0,
    ...over,
  };
}

describe("A monitor opened", () => {
  const updates = Array.from({ length: 25 }, (_, i) => ({
    id: i + 1,
    date: "2026-09-24",
    summary: `Update ${i + 1}`,
    sourceUrl: "https://example.org/a",
    sourceName: "Outlet",
    createdAt: "",
    articleTitle: "",
  }));

  it("shows its votes on the record and ten updates, then ten more at a time", async () => {
    vi.mocked(api.fetchMonitorDetail).mockResolvedValue({
      ...monitor({ updateCount: 25 }),
      updates,
      onTheRecord: [
        vote({}),
        vote({
          chamber: "senate",
          number: 244,
          date: "2026-09-24",
          yeas: 49,
          nays: 50,
          margin: -1,
        }),
      ],
    });
    render(<MonitorsTab initialSlug="a-concern" />);
    expect(await screen.findByRole("heading", { name: "On the record" })).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /House: 1 vote, closest Tied 212–212, May 14/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Senate: 1 vote, closest 49–50, Sep 24/ })
    ).toBeInTheDocument();
    expect(screen.getAllByText(/^Update \d+$/)).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: "[ SHOW 10 MORE OF 15 ]" }));
    expect(screen.getAllByText(/^Update \d+$/)).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: "[ SHOW 5 MORE OF 5 ]" }));
    expect(screen.getAllByText(/^Update \d+$/)).toHaveLength(25);
    expect(screen.queryByRole("button", { name: /SHOW/ })).not.toBeInTheDocument();
  });
});

describe("On the record", () => {
  it("labels the vote nearest a tie, the earliest among equals", () => {
    const votes = [
      vote({ margin: 5, yeas: 215, nays: 210 }),
      vote({ number: 2, margin: -1, yeas: 213, nays: 214, date: "2026-04-16" }),
    ];
    expect(closestVote(votes)?.number).toBe(2);
    expect(closestLabel(vote({}))).toBe("Tied 212–212, May 14");
    expect(closestLabel(votes[1])).toBe("213–214, Apr 16");
  });

  it("puts a tie on the middle line and the widest margin at the edge of the band", () => {
    expect(position(vote({}), "2026-05-14", "2026-05-14", 10)).toEqual({ left: 42, top: 50 });
    expect(
      position(vote({ margin: 10, date: "2026-09-24" }), "2026-05-14", "2026-09-24", 10)
    ).toEqual({ left: 81, top: 10 });
    expect(position(vote({ margin: -10 }), "2026-05-14", "2026-09-24", 10)).toEqual({
      left: 3,
      top: 90,
    });
  });
});
