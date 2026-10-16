import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { CompactStrip, FullStrip } from "./ThisWeekStrip";
import type { ThisWeek } from "@/types/thisWeek";

const data: ThisWeek = {
  election: { date: "2026-11-03", phase: "campaign", daysUntil: 23, states: ["AK", "AL", "DC"] },
  congress: {
    today: "2026-10-15",
    weekStart: "2026-10-12",
    sentence: "Congress is out until Tuesday, October 20.",
    chambers: {
      senate: {
        chamber: "senate",
        inSessionToday: false,
        daysInSessionThisWeek: 0,
        recordVotesThisWeek: 0,
        nextMeeting: "2026-10-20",
      },
      house: {
        chamber: "house",
        inSessionToday: false,
        daysInSessionThisWeek: 0,
        recordVotesThisWeek: 1,
        nextMeeting: "2026-10-20",
      },
    },
    recentVotes: [
      {
        chamber: "house",
        congress: 119,
        session: 2,
        number: 500,
        date: "2026-10-12",
        question: "On Passage",
        title: "A bill to do a thing",
        result: "Passed",
        billId: "HR.1",
        billLabel: "H.R. 1",
      },
    ],
  },
  commentDeadlines: [
    {
      id: 7,
      title: "A rule in the news",
      docType: "Proposed Rule",
      agencyName: "An Agency",
      commentsCloseOn: "2026-10-20",
      commentUrl: "https://www.regulations.gov/commenton/7",
      daysLeft: 9,
      issueId: "i0000002a",
      citedBy: 0,
    },
    {
      id: 8,
      title: "A cited rule",
      docType: "Final Rule",
      agencyName: null,
      commentsCloseOn: "2026-11-01",
      commentUrl: "https://www.regulations.gov/commenton/8",
      daysLeft: 21,
      issueId: null,
      citedBy: 2,
    },
  ],
};

async function violations() {
  const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
  return r.violations.map((v) => v.id);
}

describe("ThisWeekStrip", () => {
  it("full: countdown, every state's ballot as a link behind a disclosure, Congress and deadlines", async () => {
    render(
      <main>
        <FullStrip data={data} />
      </main>
    );
    expect(screen.getByText("23")).toBeInTheDocument();
    expect(screen.getByText(/days until Election Day · Tue, Nov 3/)).toBeInTheDocument();
    // Navigation only: links to each ballot page, behind a disclosure, no
    // field to type into.
    const ballots = screen.getByText("See who's on your state's ballot").closest("details")!;
    expect(ballots.open).toBe(false);
    expect(
      within(ballots).getByRole("link", { name: "Alaska ballot", hidden: true })
    ).toHaveAttribute("href", "/elections/states/AK");
    expect(
      within(ballots).getByRole("link", { name: "District of Columbia ballot", hidden: true })
    ).toBeInTheDocument();
    expect(document.querySelector("input, select, textarea")).toBeNull();
    expect(screen.getByText("Congress is out until Tuesday, October 20.")).toBeInTheDocument();
    expect(screen.getByText(/SENATE · no record votes this week/)).toBeInTheDocument();
    expect(screen.getByText(/HOUSE · 1 record vote this week/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /H\.R\. 1: A bill to do a thing/ })).toHaveAttribute(
      "href",
      "/congress/votes/house/119/2/500"
    );
    expect(screen.getByRole("link", { name: /What Congress did this week/ })).toHaveAttribute(
      "href",
      "/congress/week/2026-10-12"
    );
    const news = screen.getByRole("link", { name: "A rule in the news" }).closest("li")!;
    expect(within(news).getByRole("link", { name: "In the news" })).toHaveAttribute(
      "href",
      "/issue/i0000002a"
    );
    expect(within(news).getByText(/9 days left/)).toBeInTheDocument();
    expect(screen.getByText(/Cited by 2 other documents/)).toBeInTheDocument();
    expect(await violations()).toEqual([]);
  });

  it("full: each state's next voter deadline, nearest five first, every state behind a disclosure", async () => {
    const codes = ["AK", "AL", "AZ", "AR", "CA", "CO"];
    const deadlines = codes.map((state, i) => ({
      state,
      sentence: `Online registration closes Oct ${13 + i}`,
      countdown: i === 0 ? "today" : i === 1 ? "tomorrow" : `${i} days`,
      status: "open" as const,
      daysLeft: i,
      kind: "registration",
      closesOn: `2026-10-${13 + i}`,
    }));
    render(
      <main>
        <FullStrip data={{ ...data, election: { ...data.election, deadlines } }} />
      </main>
    );
    const first = screen.getByRole("link", { name: "Alaska" });
    expect(first).toHaveAttribute("href", "/elections/states/AK#voter-deadlines");
    expect(first.closest("li")).toHaveTextContent(
      "Alaska: Online registration closes Oct 13last day"
    );
    expect(screen.getByRole("link", { name: "Alabama" }).closest("li")).toHaveTextContent(
      "1 day left"
    );
    const more = screen.getByText("Every state's next deadline").closest("details")!;
    expect(more.open).toBe(false);
    expect(within(more).getByRole("link", { name: "Colorado", hidden: true })).toBeInTheDocument();
    expect(await violations()).toEqual([]);
  });

  it("full: says so when nothing is open for comment", () => {
    render(<FullStrip data={{ ...data, commentDeadlines: [] }} />);
    expect(screen.getByText("No federal rule is open for comment right now.")).toBeInTheDocument();
  });

  it("compact: one line, the state list behind a disclosure", async () => {
    render(
      <main>
        <CompactStrip data={data} />
      </main>
    );
    const strip = screen.getByRole("complementary", { name: "This week" });
    expect(
      within(strip).getByText(/Congress is out until Tuesday, October 20\./)
    ).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: "A rule in the news" })).toHaveAttribute(
      "href",
      "/explore/7"
    );
    const details = within(strip).getByText("See who's on your state's ballot").closest("details")!;
    expect(details.open).toBe(false);
    expect(
      within(details).getByRole("link", { name: "Alabama ballot", hidden: true })
    ).toHaveAttribute("href", "/elections/states/AL");
    expect(await violations()).toEqual([]);
  });

  it("compact, on a page about another day: labelled with today's date", () => {
    // 2026-09-16's page read "This week … meets today" on 2026-10-15.
    const { rerender } = render(<CompactStrip data={data} pageDate="2026-09-16" />);
    expect(screen.getByRole("complementary", { name: "Now · Thu, Oct 15" })).toBeInTheDocument();
    expect(screen.queryByText("This week")).toBeNull();
    rerender(<CompactStrip data={data} pageDate="2026-10-15" />);
    expect(screen.getByRole("complementary", { name: "This week" })).toBeInTheDocument();
  });

  it("names the count after the election, not a countdown", () => {
    render(
      <CompactStrip
        data={{ ...data, election: { ...data.election, phase: "results", daysUntil: -2 } }}
      />
    );
    expect(screen.getByText("Results")).toBeInTheDocument();
    expect(screen.getByText("See your state's count")).toBeInTheDocument();
  });
});
