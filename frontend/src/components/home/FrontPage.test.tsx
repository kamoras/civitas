import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import type { FrontPage, FrontPageVote } from "@/types/frontPage";

vi.mock("next/server", () => ({ connection: async () => undefined }));
import { FrontPageView } from "./FrontPage";

afterEach(cleanup);

const vote: FrontPageVote = {
  chamber: "senate",
  congress: 119,
  session: 2,
  number: 248,
  date: "2026-10-14",
  question: "On the Amendment S.Amdt. 1 to S. 1",
  title: "An Amdt. No. 1",
  result: "Amendment Rejected",
  yeas: 49,
  nays: 50,
  billId: "S.1",
  billLabel: "S. 1",
  tieBreaker: null,
  amendment: "S.Amdt. 1",
  amendmentPurpose: "To do a thing.",
};

const data: FrontPage = {
  asOf: "2026-10-20",
  dateline: ["Tuesday, October 20", "In session today", "14 days to Election Day"],
  edition: {
    sentence: "From the House and Senate's own records, each in its own words",
    week: "2026-10-19",
    weekLabel: "Everything this week",
  },
  voteWeek: "2026-10-12",
  lead: {
    kind: "closest",
    column: "congress",
    date: "2026-10-14",
    headline: "Decided by 1 vote in the Senate, 49–50: Amendment Rejected.",
    vote,
    figure: { type: "split", yeas: 49, nays: 50, yeaShare: 0.4949, caption: "Decided by 1 vote" },
  },
  tier: [
    {
      kind: "loneBreak",
      column: "congress",
      date: "2026-10-14",
      headline:
        "The only Republican to vote Yea: Pat Lone (R-AA), against 40 Republicans voting Nay.",
      vote,
      memberId: "pat-lone",
      memberName: "Pat Lone",
      party: "R",
      state: "AA",
      compareWithId: null,
      compareWithName: null,
      figure: { type: "dots", party: "R", voting: 41, caption: "1 of 41 Republicans voted Yea" },
    },
    {
      kind: "comment",
      column: "agencies",
      date: "2026-10-20",
      headline: "A proposed rule",
      agencyName: "An Agency",
      agencySlug: "an-agency",
      docType: "Proposed Rule",
      id: 7,
      key: "fr-2026-00007",
      commentUrl: "https://www.regulations.gov/commenton/X-7",
      figure: {
        type: "countdown",
        opened: "2026-08-21",
        closes: "2026-10-20",
        totalDays: 60,
        daysLeft: 0,
        elapsedPercent: 100,
      },
    },
  ],
  columns: {
    congress: [],
    agencies: [],
    news: [
      {
        kind: "issue",
        column: "news",
        date: "2026-10-20",
        headline: "An outlet's own headline",
        publicId: "i0000001",
        outlet: "Outlet B",
        outlets: ["Outlet B"],
        alsoDates: ["2026-10-19"],
        monitor: { slug: "a-story", title: "A story" },
        figure: { type: "stat", value: 1, caption: "newsroom reporting" },
      },
    ],
  },
};

describe("FrontPageView", () => {
  it("draws the nameplate, the lead with its figure and the evidence behind it", () => {
    render(<FrontPageView data={data} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Entered into the record");
    expect(screen.getByText(data.dateline.join(" · "))).toBeTruthy();
    expect(screen.getByRole("link", { name: /Everything this week/ })).toHaveAttribute(
      "href",
      "/congress/week/2026-10-19"
    );
    const lead = screen.getByRole("article", { name: "Lead story" });
    expect(
      within(lead).getByRole("img", { name: "49 yea to 50 nay. Decided by 1 vote." })
    ).toBeTruthy();
    expect(within(lead).getByText("To do a thing.")).toBeTruthy();
    expect(within(lead).getByRole("link", { name: "How your senators voted" })).toHaveAttribute(
      "href",
      "/congress/votes/senate/119/2/248#members"
    );
    expect(within(lead).getByRole("link", { name: "S. 1" })).toBeTruthy();
  });

  it("says each figure in words and never shows an internal id", () => {
    const { container } = render(<FrontPageView data={data} />);
    expect(screen.getByRole("img", { name: "1 of 41 Republicans voted Yea." })).toBeTruthy();
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Comment on regulations.gov/ })).toHaveAttribute(
      "href",
      "https://www.regulations.gov/commenton/X-7"
    );
    expect(container.textContent).not.toMatch(/ISSUE-|i0000001/i);
  });

  it("tags an ongoing story, folds its other days and says an empty column in words", () => {
    render(<FrontPageView data={data} />);
    expect(screen.getByRole("link", { name: "Ongoing story: A story" })).toHaveAttribute(
      "href",
      "/action?tab=monitors&monitor=a-story"
    );
    expect(screen.getByText(/Outlet B · Tue, Oct 20 · also Mon, Oct 19/)).toBeTruthy();
    expect(screen.getByText("No other record votes, laws or confirmations to show.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /The week in Congress/ })).toHaveAttribute(
      "href",
      "/congress/week/2026-10-12"
    );
  });

  it("is share-ready and passes axe", async () => {
    const { container } = render(<FrontPageView data={data} />);
    const section = container.querySelector("[data-share-section='record']");
    expect(section?.getAttribute("data-share-as-of")).toBe("2026-10-20");
    expect(section?.getAttribute("data-share-source")).toBeTruthy();
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

  it("renders nothing without a lead", () => {
    const { container } = render(<FrontPageView data={{ ...data, lead: null }} />);
    expect(container.innerHTML).toBe("");
  });
});
