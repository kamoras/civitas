import { describe, expect, it } from "vitest";
import { countLobbiedBills } from "./lobbying";
import type { LobbyingMatch } from "@/types/senator";

const bill = (billId: string) => ({
  billId,
  label: billId,
  billName: "",
  vote: "Yea",
  filingYear: 2025,
  filingUrl: null,
  registrant: null,
  filingCount: 1,
});
const match = (ids: string[]): LobbyingMatch => ({
  lobbyistOrg: "x",
  industry: "FINANCE",
  lobbyingSpend: 0,
  donationToSenator: 0,
  billsInfluenced: [],
  senatorVoteAligned: null,
  description: "",
  lobbiedBills: ids.map(bill),
});

describe("countLobbiedBills", () => {
  it("counts a bill two organizations' filings name once", () => {
    expect(countLobbiedBills([match(["HR.1", "S.5"]), match(["HR.1"])])).toBe(2);
  });
  it("treats responses without the field as none", () => {
    expect(countLobbiedBills([{ ...match([]), lobbiedBills: undefined }])).toBe(0);
  });
});
