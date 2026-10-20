import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import CommitteeEntries from "./CommitteeEntries";
import type { CommitteeEntry } from "@/types/congress";

afterEach(cleanup);

const meeting: CommitteeEntry = {
  id: 1,
  kind: "meeting",
  chamber: "senate",
  date: "2026-09-30",
  congress: 119,
  committee: "Committee on Energy and Natural Resources",
  text: "Committee ordered favorably reported S. 465, to require ...; and S. 1254, to amend ...",
  pages: "",
  report: null,
  bills: [
    { billId: "S.465", billLabel: "S. 465" },
    { billId: "S.1254", billLabel: "S. 1254" },
  ],
};

describe("CommitteeEntries", () => {
  it("prints the Digest's sentence and links the day and every bill it names", () => {
    render(<CommitteeEntries entries={[meeting]} />);
    expect(screen.getByText(meeting.text)).toBeTruthy();
    expect(screen.getByText(meeting.committee!)).toBeTruthy();
    expect(screen.getByRole("link", { name: "S. 1254" })).toHaveAttribute(
      "href",
      "/congress/bills/S.1254?congress=119"
    );
    expect(screen.getByRole("link", { name: /Sep 30/ })).toHaveAttribute(
      "href",
      "/congress/2026-09-30"
    );
  });

  it("names a report by its number", () => {
    render(
      <CommitteeEntries
        entries={[{ ...meeting, kind: "report", committee: null, report: "H. Rept. 119-851" }]}
      />
    );
    expect(screen.getByText("H. Rept. 119-851")).toBeTruthy();
  });
});
