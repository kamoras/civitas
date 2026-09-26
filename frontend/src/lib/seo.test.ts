import { describe, expect, it } from "vitest";
import { serializeJsonLd } from "@/components/seo/JsonLd";
import { describeBill, describeProfile, personJsonLd } from "./seo";
import type { PoliticianProfile } from "@/types/politicians";
import type { BillDetail } from "@/types/bill";

function profile(branch: string, identity: Partial<PoliticianProfile["identity"]>): PoliticianProfile {
  return {
    id: "x",
    branch,
    identity: { name: "Jane Doe", party: "D", role: "Senator", ...identity },
  } as unknown as PoliticianProfile;
}

describe("describeProfile", () => {
  it("leads a senator's title with the name and party-state tag", () => {
    const { title, description } = describeProfile(
      profile("senate", { state: "CA", stateName: "California", isCurrent: true }),
    );
    expect(title).toBe("Sen. Jane Doe (D-CA): Voting Record, Donors & Scorecard");
    expect(description).toContain("Democratic senator for California");
    expect(description.length).toBeLessThanOrEqual(160);
  });

  it("names a House district with a correct ordinal, and at-large seats", () => {
    const rep = (district: number) =>
      describeProfile(profile("house", { role: "Representative", party: "R", state: "TX", stateName: "Texas", district }));
    expect(rep(12).description).toContain("Texas's 12th district");
    expect(rep(22).description).toContain("Texas's 22nd district");
    expect(rep(0).description).toContain("Texas (at-large)");
  });

  it("marks former members", () => {
    const { title, description } = describeProfile(
      profile("senate", { state: "OH", stateName: "Ohio", isCurrent: false }),
    );
    expect(title).toMatch(/^Former Sen\. /);
    expect(description).toContain("Jane Doe, former Democratic senator for Ohio");
  });

  it("never tags a justice with the appointing president's party", () => {
    const { title } = describeProfile(profile("scotus", { role: "Chief Justice", party: "R" }));
    expect(title).toBe("Chief Justice Jane Doe: Supreme Court Voting Record");
    expect(title).not.toContain("(R");
  });

  it("gives presidents their own title", () => {
    expect(describeProfile(profile("president", { role: "President (16th)", party: "R" })).title).toBe(
      "Jane Doe: Presidential Record & Scorecard",
    );
  });
});

describe("describeBill", () => {
  const bill = {
    billId: "S.4967",
    title: "The Example Act",
    chamber: "senate",
    sponsorId: "s1",
    sponsorName: "Jane Doe",
    sponsorParty: "D",
    sponsorState: "CA",
    congress: 121,
    isLaw: false,
    latestAction: "Referred to committee.",
    introducedDate: "2026-01-01",
  } as unknown as BillDetail;

  it("leads with the bill number and uses a correct congress ordinal", () => {
    const { title, description } = describeBill(bill);
    expect(title).toBe("S.4967: The Example Act");
    expect(description).toContain("121st Congress");
    expect(description).toContain("Latest action: Referred to committee.");
  });
});

describe("JSON-LD", () => {
  it("escapes '<' so external text cannot close the script tag", () => {
    const out = serializeJsonLd({ name: "</script><script>alert(1)</script>" });
    expect(out).not.toContain("<");
    expect(JSON.parse(out).name).toBe("</script><script>alert(1)</script>");
  });

  it("gives members of Congress a party affiliation but not justices", () => {
    expect(personJsonLd("a", profile("senate", { state: "CA" }))).toHaveProperty("affiliation");
    expect(personJsonLd("a", profile("senate", { state: "CA" })).affiliation).toEqual({
      "@type": "PoliticalParty",
      name: "Democratic Party",
    });
    expect(personJsonLd("b", profile("scotus", { role: "Associate Justice" }))).not.toHaveProperty("affiliation");
    expect(personJsonLd("c", profile("senate", { state: "VT", party: "I" }))).not.toHaveProperty("affiliation");
  });
});
