import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import CongressGlance from "./CongressGlance";
import YourMembersRecord from "./YourMembersRecord";
import { YOUR_STATE_KEY } from "@/hooks/useYourState";
import type { CongressGlance as Glance, StateMemberRecord } from "@/types/congress";

const GLANCE: Glance = {
  calendar: {
    congress: 119,
    start: "2025-01-03",
    end: "2027-01-02",
    today: "2025-02-11",
    recordFrom: "2025-01-03",
    days: { "2025-01-03": { senate: 0, house: 2 }, "2025-02-11": { senate: 1 } },
    months: [
      { month: "2025-01", senate: { days: 1, votes: 0 }, house: { days: 1, votes: 2 } },
      { month: "2025-02", senate: { days: 1, votes: 1 }, house: { days: 0, votes: 0 } },
    ],
    totals: { senate: { days: 2, votes: 1 }, house: { days: 1, votes: 2 } },
    maxVotes: 2,
  },
  pipeline: {
    total: 19195,
    milestones: [
      { label: "Introduced", stage: "INTRODUCED", bills: 19195, share: 1, shareText: "100%" },
      { label: "Became law", stage: "ENACTED", bills: 122, share: 0.0064, shareText: "0.6%" },
    ],
  },
};

const MEMBER: StateMemberRecord = {
  id: "pat-quill",
  name: "Pat Quill",
  party: "D",
  chamber: "house",
  state: "PA",
  district: 7,
  photo: null,
  rollCalls: 10,
  chamberRollCalls: 12,
  voted: 9,
  missed: 1,
  votes: [
    {
      date: "2025-02-11",
      vote: "Nay",
      billId: "HR.12",
      billName: "A Long Act",
      votedWithParty: false,
      rollCall: {
        chamber: "house",
        congress: 119,
        session: 1,
        number: 3,
        question: "On Passage",
        result: "Passed",
      },
    },
  ],
  billsSponsored: 1,
  bills: [
    {
      billId: "HR.12",
      billLabel: "H.R. 12",
      congress: 119,
      title: "A Long Act",
      introduced: "2025-02-01",
      stage: "REFERRED",
      latestAction: "",
      latestActionDate: "",
    },
  ],
};

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("CongressGlance", () => {
  it("draws the sitting calendar and the pipeline, each with a table", async () => {
    const { container } = render(
      <main>
        <CongressGlance glance={GLANCE} />
      </main>
    );
    const cal = screen.getByRole("img", { name: /Senate 2 days with 1 record votes/ });
    // Lit cells: two Senate days and one House day; every other day dim.
    expect(cal.querySelectorAll("rect.fill-phos")).toHaveLength(2);
    expect(cal.querySelectorAll("rect.fill-signal-cyan")).toHaveLength(1);
    const months = screen.getByRole("table", { name: /by month/ });
    expect(within(months).getByRole("link", { name: "January 2025" })).toHaveAttribute(
      "href",
      "/congress/month/2025-01"
    );
    const pipe = screen.getByRole("table", { name: /log scale/ });
    // Each step opens the very bills it counts (?reached=, not ?stage=).
    expect(within(pipe).getByRole("rowheader", { name: /^Became law/ })).toBeInTheDocument();
    expect(within(pipe).getByRole("link", { name: "Became law: 122 bills" })).toHaveAttribute(
      "href",
      "/congress/bills?reached=ENACTED"
    );
    expect(within(pipe).getByRole("link", { name: "Introduced: 19,195 bills" })).toHaveAttribute(
      "href",
      "/congress/bills?reached=INTRODUCED"
    );
    expect(within(pipe).getByText("122")).toBeInTheDocument();
    expect(container.querySelectorAll("[data-share-section]")).toHaveLength(2);
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("YourMembersRecord", () => {
  it("shows nothing without a state picked", () => {
    const { container } = render(<YourMembersRecord />);
    expect(container).toBeEmptyDOMElement();
  });

  it("loads the picked state's members, sending only the state", async () => {
    sessionStorage.setItem(YOUR_STATE_KEY, "PA");
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ state: "PA", members: [MEMBER] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <main>
        <YourMembersRecord />
      </main>
    );
    expect(await screen.findByRole("link", { name: "Pat Quill's full record" })).toHaveAttribute(
      "href",
      "/politicians/pat-quill"
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/congress/members?state=PA");
    expect(screen.getByText(/against party/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "On Passage" })).toHaveAttribute(
      "href",
      "/congress/votes/house/119/1/3"
    );
    expect(screen.getByRole("link", { name: "H.R. 12" })).toHaveAttribute(
      "href",
      "/congress/bills/HR.12?congress=119"
    );
    await waitFor(async () => {
      const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
      expect(r.violations.map((v) => v.id)).toEqual([]);
    });
  });
});
