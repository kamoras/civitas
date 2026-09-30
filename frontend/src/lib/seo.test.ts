import { describe, expect, it } from "vitest";
import { serializeJsonLd } from "@/components/seo/JsonLd";
import {
  describeBill,
  describeElections,
  describeProfile,
  describeStateBallot,
  personJsonLd,
} from "./seo";
import type { PoliticianProfile } from "@/types/politicians";
import type { BillDetail } from "@/types/bill";

function profile(
  branch: string,
  identity: Partial<PoliticianProfile["identity"]>
): PoliticianProfile {
  return {
    id: "x",
    branch,
    identity: { name: "Jane Doe", party: "D", role: "Senator", ...identity },
  } as unknown as PoliticianProfile;
}

describe("describeProfile", () => {
  it("leads a senator's title with the name and party-state tag", () => {
    const { title, description } = describeProfile(
      profile("senate", { state: "CA", stateName: "California", isCurrent: true })
    );
    expect(title).toBe("Sen. Jane Doe (D-CA): Voting Record, Donors & Scorecard");
    expect(description).toContain("Democratic senator for California");
    expect(description.length).toBeLessThanOrEqual(160);
  });

  it("names a House district with a correct ordinal, and at-large seats", () => {
    const rep = (district: number) =>
      describeProfile(
        profile("house", {
          role: "Representative",
          party: "R",
          state: "TX",
          stateName: "Texas",
          district,
        })
      );
    expect(rep(12).description).toContain("Texas's 12th district");
    expect(rep(22).description).toContain("Texas's 22nd district");
    expect(rep(0).description).toContain("Texas (at-large)");
  });

  it("marks former members", () => {
    const { title, description } = describeProfile(
      profile("senate", { state: "OH", stateName: "Ohio", isCurrent: false })
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
    expect(
      describeProfile(profile("president", { role: "President (16th)", party: "R" })).title
    ).toBe("Jane Doe: Presidential Record & Scorecard");
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
    expect(personJsonLd("b", profile("scotus", { role: "Associate Justice" }))).not.toHaveProperty(
      "affiliation"
    );
    expect(personJsonLd("c", profile("senate", { state: "VT", party: "I" }))).not.toHaveProperty(
      "affiliation"
    );
  });
});

describe("elections metadata", () => {
  it("drops the lean map from /elections' description in the results window", () => {
    const campaign = describeElections(2026, false);
    expect(campaign.description).toMatch(/partisan lean/);
    const results = describeElections(2026, true);
    expect(results.title).toBe("2026 Election Results by State: Senate & House Count");
    expect(results.description).not.toMatch(/partisan lean/);
    expect(results.description).toMatch(/Civitas calls none\.$/);
    expect(results.description).not.toMatch(/\bwins?\b|\bwon\b/);
    // Short enough that metaDescription never cuts the "calls none".
    expect(results.description.length).toBeLessThanOrEqual(160);
  });

  const ballot = {
    state: "GA",
    stateName: "Georgia",
    cycleYear: 2026,
    electionDate: "2026-11-03",
    measures: [{}, {}],
  };

  it("keeps the ballot-research wording for a state page outside the results window", () => {
    const d = describeStateBallot(ballot, false, null);
    expect(d.title).toBe("Georgia Ballot 2026: Senate, House Races & Ballot Measures");
    expect(d.description).toMatch(/2 statewide ballot measures/);
  });

  it("says a state page leads with the count in the results window — or where it is", () => {
    const live = describeStateBallot(ballot, true, true);
    expect(live.title).toBe("Georgia Election Results 2026: Senate & House Count and Ballot");
    expect(live.description).toMatch(/as its election office publishes it/);
    expect(live.description).toMatch(/Civitas calls no race/);
    // A state not read live: no count promised, the office pointed to.
    const notLive = describeStateBallot(ballot, true, false);
    expect(notLive.description).toMatch(/Civitas doesn't read it live/);
    // Couldn't check: promises neither.
    const unknown = describeStateBallot(ballot, true, null);
    expect(unknown.description).not.toMatch(/doesn't read|as its election office publishes it/);
    for (const d of [live, notLive, unknown]) {
      expect(d.description.length).toBeLessThanOrEqual(160);
      expect(d.description).not.toMatch(/\bwins?\b|\bwon\b/);
    }
  });
});
