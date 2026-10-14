import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Notable } from "@/types/notable";

vi.mock("next/server", () => ({ connection: async () => undefined }));
import { OnTheRecordList } from "./OnTheRecord";

afterEach(cleanup);

const vote = {
  chamber: "senate" as const,
  congress: 119,
  session: 2,
  number: 252,
  date: "2026-09-29",
  question: "On the Motion to Discharge S.Res. 852",
  title: "A resolution.",
  result: "Motion to Discharge Rejected",
  yeas: 47,
  nays: 51,
  billId: "SRES.852",
  billLabel: "S. Res. 852",
};

const data: Notable & { weekStart: string } = {
  weekStart: "2026-09-28",
  weekEnd: "2026-10-04",
  entries: [
    { kind: "closest", sentence: "Decided by 4 votes in the Senate, 47–51.", vote },
    {
      kind: "loneBreak",
      sentence:
        "The only Republican to vote Yea: Pat Lone (R-AA), against 50 Republicans voting Nay.",
      vote,
      memberId: "pat-lone",
      memberName: "Pat Lone",
      party: "R",
      state: "AA",
      compareWithId: "sam-other",
      compareWithName: "Sam Other",
    },
  ],
};

describe("OnTheRecordList", () => {
  it("prints the backend's sentences and links each to its roll call", () => {
    render(<OnTheRecordList data={data} />);
    expect(screen.getByText(data.entries[0].sentence)).toBeTruthy();
    const rollCalls = screen.getAllByRole("link", { name: "[ ROLL CALL ]" });
    expect(rollCalls.map((a) => a.getAttribute("href"))).toEqual([
      "/congress/bills/SRES.852?congress=119#vote-senate-2-252",
      "/congress/bills/SRES.852?congress=119#vote-senate-2-252",
    ]);
  });

  it("offers the member and the state's other senator side by side", () => {
    render(<OnTheRecordList data={data} />);
    expect(screen.getByRole("link", { name: "[ PAT LONE ]" }).getAttribute("href")).toBe(
      "/politicians/pat-lone"
    );
    expect(screen.getByRole("link", { name: "[ VS. SAM OTHER ]" }).getAttribute("href")).toBe(
      "/compare?leftId=pat-lone&leftChamber=senate&rightId=sam-other&rightChamber=senate"
    );
  });

  it("falls back to the day's page for a vote on no bill", () => {
    const nomination = {
      ...data,
      entries: [{ ...data.entries[0], vote: { ...vote, billId: null } }],
    };
    render(<OnTheRecordList data={nomination} />);
    expect(screen.getByRole("link", { name: "[ ROLL CALL ]" }).getAttribute("href")).toBe(
      "/congress/2026-09-29"
    );
  });
});
