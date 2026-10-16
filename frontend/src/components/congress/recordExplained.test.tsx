import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import AmendmentPurpose from "./AmendmentPurpose";
import SeatChart from "./SeatChart";
import BillJourney from "./BillJourney";
import VotePanel from "./VotePanel";
import type { BillJourney as Journey, BillPassage, VoteMember } from "@/types/congress";

async function violations() {
  const result = await axe.run(document.body, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}

describe("AmendmentPurpose", () => {
  it("quotes the record's purpose, labelled as the official one, with its source", () => {
    render(
      <AmendmentPurpose
        vote={{
          amendment: "S.Amdt. 6805",
          amendmentPurpose: "To limit price increases for college athletics tickets.",
        }}
        chamber="senate"
      />
    );
    expect(screen.getByText("Official purpose of S.Amdt. 6805")).toBeTruthy();
    expect(
      screen.getByText("To limit price increases for college athletics tickets.").tagName
    ).toBe("BLOCKQUOTE");
    expect(screen.getByText("Quoted from the Senate roll-call record")).toBeTruthy();
  });

  it("says when the record states no purpose, and says nothing before it is read", () => {
    const { rerender, container } = render(
      <AmendmentPurpose vote={{ amendment: "H.Amdt. 9", amendmentPurpose: "" }} chamber="house" />
    );
    expect(screen.getByText(/No statement of purpose on file for H\.Amdt\. 9/)).toBeTruthy();
    rerender(
      <AmendmentPurpose vote={{ amendment: null, amendmentPurpose: null }} chamber="house" />
    );
    expect(container.textContent).toBe("");
    rerender(<AmendmentPurpose vote={{}} chamber="house" />);
    expect(container.textContent).toBe("");
  });
});

function member(party: string, bucket: VoteMember["bucket"], i: number): VoteMember {
  return {
    lastName: `M${party}${i}`,
    firstName: "",
    party,
    state: "OH",
    position: bucket,
    bucket,
    page: null,
  };
}

describe("SeatChart", () => {
  it("draws one dot per member and gives the parties' counts as a table", async () => {
    const members = [
      ...Array.from({ length: 3 }, (_, i) => member("R", "nay", i)),
      member("R", "yea", 9),
      ...Array.from({ length: 2 }, (_, i) => member("D", "yea", i)),
      member("D", "notVoting", 5),
    ];
    const { container } = render(
      <SeatChart
        members={members}
        parties={[
          { party: "R", yea: 1, nay: 3, present: 0, notVoting: 0 },
          { party: "D", yea: 2, nay: 0, present: 0, notVoting: 1 },
        ]}
        chamberName="Senate"
      />
    );
    // The dots are hidden from a screen reader; the table carries the figures.
    const dots = container.querySelectorAll("[data-seats] > span");
    expect(dots.length).toBe(7);
    const table = screen.getByRole("table", { name: "How each party voted" });
    const rows = within(table).getAllByRole("row");
    expect(rows[0].textContent).toBe("PartyYeaNayNot voting");
    expect(rows[1].textContent).toBe("Republicans130");
    expect(rows[2].textContent).toBe("Democrats201");
    expect(await violations()).toEqual([]);
  });
});

const journey: Journey = {
  stops: [
    {
      key: "introduced",
      label: "Introduced in the Senate",
      status: "done",
      date: "2025-01-24",
      detail: null,
      after: null,
    },
    {
      key: "committee",
      label: "Committee",
      status: "done",
      date: "2025-11-04",
      detail: "Reported by the committee",
      after: "9 months later",
    },
    {
      key: "floor-senate",
      label: "Senate floor",
      status: "current",
      date: "2025-12-11",
      detail: "Passed",
      after: "37 days later",
    },
    {
      key: "floor-house",
      label: "House floor",
      status: "ahead",
      date: null,
      detail: null,
      after: null,
    },
  ],
};

const passage: BillPassage[] = [
  {
    chamber: "senate",
    kind: "passed",
    date: "2025-12-11",
    method: "unanimousConsent",
    twoThirds: false,
    methodTerm: "Unanimous consent",
    methodMeaning: "The chamber agrees to something unless any member objects.",
    sentence:
      "Passed the Senate by unanimous consent on December 11, 2025; no roll call is taken for that.",
    text: "Passed Senate without amendment by Unanimous Consent.",
    rollCall: null,
  },
];

describe("BillJourney", () => {
  it("lists every stop in order, dated, with where the bill is now", async () => {
    render(<BillJourney journey={journey} passage={passage} />);
    const items = screen.getAllByRole("listitem").slice(0, 4);
    expect(items.map((li) => li.querySelector(".font-bold")?.textContent)).toEqual([
      "Introduced in the Senate",
      "Committee",
      "Senate floor (where it is now)",
      "House floor (not reached)",
    ]);
    expect(items[2]).toHaveAttribute("aria-current", "step");
    expect(within(items[1]).getByText("November 4, 2025")).toBeTruthy();
    expect(within(items[1]).getByText("9 months later")).toBeTruthy();
    expect(within(items[3]).getByText("Not yet")).toBeTruthy();
    expect(screen.getByText(passage[0].sentence)).toBeTruthy();
    expect(screen.getByText("Unanimous consent:")).toBeTruthy();
    expect(await violations()).toEqual([]);
  });
});

describe("VotePanel without a roll call", () => {
  it("says how each chamber passed the bill instead of 'no recorded votes'", () => {
    render(<VotePanel votes={[]} congress={119} passage={passage} />);
    expect(screen.queryByText("No recorded votes on this bill in either chamber.")).toBeNull();
    expect(screen.getByText(/No roll call was taken on this bill/)).toBeTruthy();
    expect(screen.getByText(passage[0].sentence)).toBeTruthy();
  });
});
