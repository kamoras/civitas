import { describe, expect, it } from "vitest";
import { getPartyMeta } from "./CandidateCard";

describe("getPartyMeta", () => {
  it("labels a code by itself where it has a label", () => {
    expect(getPartyMeta({ party: "DFL", partyGroup: "DEM" }).label).toBe("DEMOCRAT (DFL)");
    expect(getPartyMeta({ party: "NOP", partyGroup: "IND" }).label).toBe("NO PARTY PREFERENCE");
  });

  it("falls back to the backend's party group for any other code", () => {
    // FEC's U.S. Taxpayers code is the Constitution party (FEC_PARTY_ALIASES).
    expect(getPartyMeta({ party: "UST", partyGroup: "CON" }).label).toBe("CONSTITUTION");
    const newAlias = getPartyMeta({ party: "XYZ", partyGroup: "DEM" });
    expect(newAlias.color).toBe("text-dem-blue");
  });

  it("shows the bare code when nothing names it", () => {
    expect(getPartyMeta({ party: "IAP" }).label).toBe("IAP");
    // What the FEC's own table says a code names, when the page has no
    // label of its own: Michigan's U.S. Taxpayers nominees file as "TX".
    expect(getPartyMeta({ party: "TX", partyLabel: "Taxpayers" }).label).toBe("TAXPAYERS");
  });
});
