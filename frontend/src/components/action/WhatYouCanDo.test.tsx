import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { IssueTags, WhatYouCanDo } from "./IssueEnrichment";
import { callScript } from "./ContactYourMembers";
import type { ActionIssue, IssueMembers } from "@/types/action";
import * as api from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchIssueMembers: vi.fn(),
  fetchHouseDistrictsByCounty: vi.fn(),
  sendActionTap: vi.fn(),
}));

const VT: IssueMembers = {
  state: "VT",
  votes: [
    {
      chamber: "house",
      congress: 119,
      session: 2,
      number: 314,
      date: "2026-09-30",
      question: "On Passage",
      result: "Passed",
      billId: "S.2403",
      billLabel: "S. 2403",
    },
  ],
  members: [
    {
      id: "ann-ash",
      name: "Ann Ash",
      party: "D",
      chamber: "senate",
      officePhone: "(202) 224-0001",
      contactFormUrl: "https://ash.senate.gov/contact",
      websiteUrl: "",
      positions: {},
    },
    {
      id: "bo-birch",
      name: "Bo Birch",
      party: "I",
      chamber: "house",
      district: 0,
      officePhone: "(202) 225-0002",
      contactFormUrl: "",
      websiteUrl: "",
      positions: { "house-119-2-314": "Yea" },
    },
  ],
};

beforeEach(() => {
  vi.mocked(api.fetchIssueMembers).mockReset().mockResolvedValue(VT);
  vi.mocked(api.fetchHouseDistrictsByCounty)
    .mockReset()
    .mockResolvedValue({ state: "VT", congress: 119, available: true, districts: [] });
  vi.mocked(api.sendActionTap).mockReset();
});

function issue(overrides: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 1,
    publicId: "abc123",
    date: "2026-09-29",
    firstSurfaced: "2026-09-29",
    rank: 1,
    title: "A story",
    summary: "A summary.",
    facts: [],
    factSources: [],
    newFacts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: [],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    isTrending: false,
    status: "confirmed",
    ...overrides,
  };
}

const rows = () => screen.getAllByRole("listitem");

describe("WhatYouCanDo", () => {
  it("gives a state's offices' phones on the issue itself, from a state picked from a list", async () => {
    // Nothing asks or remembers where the reader lives: a list, kept nowhere.
    render(<WhatYouCanDo issue={issue({ voteCount: 1 })} today="2026-09-29" />);
    expect(screen.queryByRole("link", { name: "Directory →" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Your state"), { target: { value: "VT" } });
    expect(api.fetchIssueMembers).toHaveBeenCalledWith("abc123", "VT");
    const call = await screen.findByRole("link", {
      name: "Call Sen. Ann Ash's office, (202) 224-0001",
    });
    expect(call).toHaveAttribute("href", "tel:2022240001");
    // The record on the issue's roll call, linked to the vote on the bill page.
    expect(screen.getByText("Yea")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "S. 2403 · On Passage" })).toHaveAttribute(
      "href",
      expect.stringContaining("#vote-house-2-314")
    );
    fireEvent.click(call);
    // Counted per issue and kind only: no state, member or number goes with it.
    expect(api.sendActionTap).toHaveBeenNthCalledWith(1, "abc123", "pick");
    expect(api.sendActionTap).toHaveBeenNthCalledWith(2, "abc123", "call");
  });

  it("states who decides next, from the record", () => {
    render(
      <WhatYouCanDo
        issue={issue({ deciders: [{ body: "The House", basis: "S. 2403 · In Other Chamber" }] })}
        today="2026-09-29"
      />
    );
    const [row] = rows();
    expect(within(row).getByText("Decides")).toBeInTheDocument();
    expect(within(row).getByText("The House")).toBeInTheDocument();
    expect(within(row).getByText("S. 2403 · In Other Chamber")).toBeInTheDocument();
  });

  it("contacts each named member through their own form, by chamber", () => {
    render(
      <WhatYouCanDo
        issue={issue({
          relatedSenators: [
            {
              id: "S1",
              name: "Maria Alvarez",
              state: "NM",
              party: "D",
              overallScore: 74.4,
              leadershipScore: null,
              chamber: "senate",
              contactFormUrl: "https://alvarez.senate.gov/contact",
            },
            {
              id: "H1",
              name: "Tom Reed",
              state: "KS",
              party: "R",
              overallScore: 61,
              leadershipScore: null,
              chamber: "house",
            },
          ],
        })}
        today="2026-09-29"
      />
    );
    const [sen, rep] = rows();
    expect(within(sen).getByText(/Sen\. Maria Alvarez/)).toBeInTheDocument();
    expect(within(sen).getByRole("link", { name: "Contact ↗" })).toHaveAttribute(
      "href",
      "https://alvarez.senate.gov/contact"
    );
    // No contact URL on file: the scorecard, not a generic senate.gov page
    // (wrong for a House member).
    expect(within(rep).getByText(/Rep\. Tom Reed/)).toBeInTheDocument();
    expect(within(rep).getByRole("link", { name: "Scorecard →" })).toHaveAttribute(
      "href",
      "/politicians/H1"
    );
    // A named member's office answers its own constituents, so every other
    // reader still gets a contact they can complete: their own members.
    expect(screen.getByText("Or contact your own senators and representative")).toBeInTheDocument();
    expect(screen.getByLabelText("Your state")).toBeInTheDocument();
  });

  it("says why each member is listed once, in the reason's own words", () => {
    const member = {
      state: "NM",
      party: "D" as const,
      overallScore: 50,
      leadershipScore: null,
      chamber: "senate" as const,
    };
    render(
      <WhatYouCanDo
        issue={issue({
          relatedSenators: [
            { ...member, id: "S1", name: "Maria Alvarez", matchReason: "named in coverage" },
            { ...member, id: "S2", name: "Ann Cole", matchReason: "referenced in coverage" },
          ],
        })}
        today="2026-09-29"
      />
    );
    const [named, referenced] = rows();
    expect(within(named).getByText("Named in coverage")).toBeInTheDocument();
    expect(within(named).queryByText(/named in coverage.*named in coverage/i)).toBeNull();
    expect(within(referenced).getByText("Referenced in coverage")).toBeInTheDocument();
  });

  it("lists a bill once, even when a track action points at it too", () => {
    const url = "https://www.congress.gov/bill/119th-congress/house-bill/5371";
    render(
      <WhatYouCanDo
        issue={issue({
          relatedBills: [
            {
              id: "H.R.5371",
              name: "Continuing Appropriations Act",
              url,
              internalUrl: "/congress/bills/HR.5371",
            },
          ],
          actions: [{ type: "track_legislation", text: "Track H.R.5371 on Congress.gov", url }],
        })}
        today="2026-09-29"
      />
    );
    const follows = rows().filter((r) => within(r).queryByText("Follow"));
    expect(follows).toHaveLength(1);
    expect(within(follows[0]).getByRole("link", { name: "Bill page →" })).toHaveAttribute(
      "href",
      "/congress/bills/HR.5371"
    );
  });

  it("offers a comment only while the period is open", () => {
    const doc = {
      title: "Proposed rule",
      docType: "proposed_rule",
      date: "2026-09-01",
      url: null,
      commentUrl: "https://www.regulations.gov/x",
    };
    render(
      <WhatYouCanDo
        issue={issue({
          relatedExploreDocs: [
            { ...doc, id: 7, commentsCloseOn: "2026-10-11" },
            { ...doc, id: 8, commentsCloseOn: "2026-09-28" },
          ],
        })}
        today="2026-09-29"
      />
    );
    expect(screen.getByRole("link", { name: "Comment →" })).toHaveAttribute(
      "href",
      "/explore/7#comment"
    );
    // Closed yesterday: still worth reading, no longer commentable.
    expect(screen.getByRole("link", { name: "Document →" })).toHaveAttribute("href", "/explore/8");
  });

  it("says when a comment action closes and how long is left, once per document", () => {
    render(
      <WhatYouCanDo
        issue={issue({
          actions: [
            {
              text: "Comment by October 11",
              type: "comment_period",
              url: "/explore/7#comment",
              docId: 7,
              closesOn: "2026-10-11",
            },
          ],
          relatedExploreDocs: [
            {
              id: 7,
              title: "A proposed rule",
              docType: "Proposed Rule",
              date: "2026-09-01",
              url: null,
              commentUrl: "https://www.regulations.gov/x",
              commentsCloseOn: "2026-10-11",
            },
          ],
        })}
        today="2026-09-29"
      />
    );
    const links = screen.getAllByRole("link", { name: "Comment →" });
    expect(links).toHaveLength(1);
    const row = links[0].closest("li")!;
    expect(row).toHaveTextContent("A proposed rule");
    expect(row).toHaveTextContent("Proposed Rule · Comment by October 11 · 12 days left");
  });

  it("drops a comment action whose period has closed", () => {
    render(
      <WhatYouCanDo
        issue={issue({
          actions: [
            {
              text: "Comment by September 28",
              type: "comment_period",
              url: "/explore/7#comment",
              docId: 7,
              closesOn: "2026-09-28",
            },
          ],
        })}
        today="2026-09-29"
      />
    );
    expect(screen.queryByRole("link", { name: "Comment →" })).toBeNull();
  });

  it("names the committee that handles the issue, with its chair and ranking member", () => {
    render(
      <WhatYouCanDo
        issue={issue({
          actions: [
            {
              text: "Contact the Senate Committee on Finance",
              type: "contact_committee",
              url: "https://www.finance.senate.gov/",
              committee: {
                code: "SSFI",
                name: "Senate Committee on Finance",
                chamber: "senate",
                url: "https://www.finance.senate.gov/",
                chair: { id: "S9", name: "Pat Doe", party: "R", state: "ID", chamber: "senate" },
                rankingMember: {
                  id: "S8",
                  name: "Lee Roe",
                  party: "D",
                  state: "OR",
                  chamber: "senate",
                },
              },
            },
          ],
        })}
        today="2026-09-29"
      />
    );
    const link = screen.getByRole("link", { name: "Committee ↗" });
    expect(link).toHaveAttribute("href", "https://www.finance.senate.gov/");
    const row = link.closest("li")!;
    expect(row).toHaveTextContent("Senate Committee on Finance");
    expect(row).toHaveTextContent("Likely the committee for this, matched by its jurisdiction");
    expect(within(row).getByRole("link", { name: "Sen. Pat Doe" })).toHaveAttribute(
      "href",
      "/politicians/S9"
    );
    expect(within(row).getByRole("link", { name: "Sen. Lee Roe" })).toHaveAttribute(
      "href",
      "/politicians/S8"
    );
  });
});

describe("IssueTags", () => {
  it("links a monitor to its row on the monitors tab", () => {
    render(
      <IssueTags
        issue={issue({
          relatedMonitors: [{ slug: "government-funding", title: "Government Funding" }],
        })}
      />
    );
    expect(screen.getByRole("link", { name: /Government Funding/ })).toHaveAttribute(
      "href",
      "/action?tab=monitors&monitor=government-funding"
    );
  });

  it("names a monitor by its stored title, apostrophes and capitals intact", () => {
    render(
      <IssueTags
        issue={issue({
          relatedMonitorSlugs: ["senator-talks-trumps-deal"],
          relatedMonitors: [
            { slug: "senator-talks-trumps-deal", title: "Senator talks Trump's deal" },
          ],
        })}
      />
    );
    expect(screen.getByRole("link", { name: "Senator talks Trump's deal →" })).toBeInTheDocument();
    expect(screen.queryByText(/trumps/)).toBeNull();
  });

  it("opens the monitor in place when the page supplies a handler", () => {
    // On /action itself a same-route <Link> is a soft navigation the page
    // never re-reads, so it must be a button that sets the state directly.
    const onMonitor = vi.fn();
    render(
      <IssueTags
        issue={issue({
          relatedMonitors: [{ slug: "government-funding", title: "Government Funding" }],
        })}
        onMonitor={onMonitor}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Government Funding/ }));
    expect(onMonitor).toHaveBeenCalledWith("government-funding");
  });
});

describe("callScript", () => {
  const sen = { name: "Ann Ash", chamber: "senate" };
  const withBill = issue({
    relatedBills: [{ name: "S. 2403", id: "S.2403", url: "https://www.congress.gov/x" }],
  });

  it("is a fixed template around the record, with support and oppose worded alike", () => {
    expect(callScript(withBill, sen, "Vermont", "support")).toBe(
      "Hello. My name is [your name], and I live in [your town], Vermont.\n" +
        "I am calling about S. 2403.\n" +
        "I support it, and I ask Sen. Ann Ash to vote yes.\n" +
        "Thank you."
    );
    expect(callScript(withBill, sen, "Vermont", "oppose")).toContain(
      "I oppose it, and I ask Sen. Ann Ash to vote no."
    );
    expect(callScript(withBill, sen, "Vermont", "ask")).toContain(
      "I would like to know where Sen. Ann Ash stands on it."
    );
  });

  it("quotes the report and its outlet when no bill is named, and never uses a pronoun for the member", () => {
    const text = callScript(
      issue({ summary: "Senators wrote a letter.", summarySource: "NPR" }),
      sen,
      "Vermont",
      "ask"
    );
    expect(text).toContain('I am calling about this report from NPR: "Senators wrote a letter".');
    expect(text).not.toMatch(/\b(he|she|his|her|him|they|their|them)\b/i);
  });

  it("offers support and oppose only when there is a bill to vote on", async () => {
    render(<WhatYouCanDo issue={issue()} today="2026-09-29" />);
    fireEvent.change(screen.getByLabelText("Your state"), { target: { value: "VT" } });
    fireEvent.click(await screen.findByRole("button", { name: "What to say to Sen. Ann Ash" }));
    expect(screen.queryByRole("button", { name: "Support" })).toBeNull();
    expect(
      screen.getByText(/I would like to know where Sen. Ann Ash stands on it/)
    ).toBeInTheDocument();
  });
});
