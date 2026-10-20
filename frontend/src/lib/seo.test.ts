import { describe, expect, it } from "vitest";
import { serializeJsonLd } from "@/components/seo/JsonLd";
import {
  agencyJsonLd,
  describeAgency,
  describeElectionMap,
  describeBill,
  describeElections,
  describeProfile,
  describeStateBallot,
  aboutPageJsonLd,
  personJsonLd,
  siteJsonLd,
  describeRedistrictingIndex,
} from "./seo";
import type { PoliticianProfile } from "@/types/politicians";
import type { BillDetail } from "@/types/bill";
import type { AgencyDetail } from "@/types/agency";

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

  it("shortens Oyez's full title and describes the record, with no score framing", () => {
    const { title, description } = describeProfile(
      profile("scotus", { role: "Associate Justice of the Supreme Court of the United States" })
    );
    expect(title).toBe("Associate Justice Jane Doe: Supreme Court Voting Record");
    expect(description).toContain("beside every sitting justice");
    expect(description).not.toMatch(/scorecard|scored/i);
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

  it("uses the Record's spelling of the number when one is given", () => {
    const { title, description } = describeBill(bill, "S. 4967");
    expect(title).toBe("S. 4967: The Example Act");
    expect(description.startsWith("S. 4967 (121st Congress")).toBe(true);
  });
});

describe("JSON-LD", () => {
  it("escapes '<' so external text cannot close the script tag", () => {
    const out = serializeJsonLd({ name: "</script><script>alert(1)</script>" });
    expect(out).not.toContain("<");
    expect(JSON.parse(out).name).toBe("</script><script>alert(1)</script>");
  });

  it("gives members of Congress a party affiliation but not justices", () => {
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
  it("drops the lean map from /elections' description from election day", () => {
    const campaign = describeElections(2026, "campaign");
    expect(campaign.description).toMatch(/partisan lean/);
    const results = describeElections(2026, "results");
    expect(results.title).toBe("2026 Election Results by State: Senate & House Count");
    expect(results.description).not.toMatch(/partisan lean/);
    expect(results.description).toMatch(/"leads" even once its count is official/);
    expect(results.description).toMatch(/Civitas calls no race\.$/);
    // Election morning: no count yet, and no lean either — the page draws
    // none from election day.
    const morning = describeElections(2026, "election_day");
    expect(morning.title).not.toMatch(/Results/);
    expect(morning.description).not.toMatch(/partisan lean|lean/);
    for (const d of [campaign, results, morning]) {
      expect(d.description).not.toMatch(/\bwins?\b|\bwon\b|until (?:the state|official)/);
      // Short enough that metaDescription never cuts the qualifier.
      expect(d.description.length).toBeLessThanOrEqual(160);
    }
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
    expect(live.description).toMatch(/"leads" even once official/);
    // A state not read live: no count promised, in the title either.
    const notLive = describeStateBallot(ballot, true, false);
    expect(notLive.description).toMatch(/Civitas doesn't read it live/);
    expect(notLive.title).not.toMatch(/Count/);
    // The longest state name still fits.
    const long = describeStateBallot({ ...ballot, stateName: "North Carolina" }, true, true);
    expect(long.description.length).toBeLessThanOrEqual(160);
    // Couldn't check: promises neither.
    const unknown = describeStateBallot(ballot, true, null);
    expect(unknown.description).not.toMatch(/doesn't read|as its election office publishes it/);
    for (const d of [live, notLive, unknown]) {
      expect(d.description.length).toBeLessThanOrEqual(160);
      expect(d.description).not.toMatch(/\bwins?\b|\bwon\b|until official/);
    }
  });
});

describe("describeRedistrictingIndex", () => {
  it("speaks of a redraw as coming until its election, and as past after", () => {
    expect(describeRedistrictingIndex("2026", 9).description).toMatch(/^9 states vote on/);
    expect(describeRedistrictingIndex("2026", 9, false).description).toMatch(/^9 states voted on/);
  });
});

describe("siteJsonLd", () => {
  // The names Google's site-name system reads. "Civitas" alone is shared by
  // many organizations, so the full name leads and the short one is an
  // alternate, never the other way round.
  const [website, organization] = siteJsonLd();

  it("names the WebSite in full, with its alternates and the canonical origin", () => {
    expect(website).toMatchObject({
      "@type": "WebSite",
      "@id": "https://civitas-research.org/#website",
      name: "Civitas Research",
      alternateName: ["Civitas", "civitas-research.org"],
      url: "https://civitas-research.org/",
      publisher: { "@id": "https://civitas-research.org/#organization" },
    });
  });

  it("describes /explore's search as a SearchAction whose template /explore reads", () => {
    expect(website.potentialAction).toEqual({
      "@type": "SearchAction",
      target: {
        "@type": "EntryPoint",
        urlTemplate: "https://civitas-research.org/explore?q={search_term_string}",
      },
      "query-input": "required name=search_term_string",
    });
  });

  it("names the Organization with a raster logo and its own profiles", () => {
    expect(organization).toMatchObject({
      "@type": "Organization",
      "@id": "https://civitas-research.org/#organization",
      name: "Civitas Research",
      alternateName: ["Civitas", "civitas-research.org"],
      url: "https://civitas-research.org/",
      logo: "https://civitas-research.org/apple-icon.png",
      sameAs: [
        "https://bsky.app/profile/civitas-research.org",
        "https://github.com/kamoras/civitas",
      ],
    });
  });

  it("points the About page at the Organization and the WebSite", () => {
    expect(aboutPageJsonLd("d")).toMatchObject({
      "@type": "AboutPage",
      url: "https://civitas-research.org/about",
      about: { "@id": organization["@id"] },
      isPartOf: { "@id": website["@id"] },
    });
  });
});

describe("agency metadata", () => {
  const fda = {
    slug: "food-and-drug-administration",
    name: "Food and Drug Administration",
    shortName: "FDA",
    parent: {
      slug: "health-and-human-services-department",
      name: "Health and Human Services Department",
    },
    budget: null,
    documents: { total: 120, openCommentsCount: 3 },
    website: "https://www.fda.gov",
    frUrl: "https://www.federalregister.gov/agencies/food-and-drug-administration",
  } as unknown as AgencyDetail;

  it("leads with the name people search for and states what the page holds", () => {
    const { title, description } = describeAgency(fda);
    expect(title).toBe("Food and Drug Administration (FDA): budget, rules and comment periods");
    expect(description).toContain("part of the Health and Human Services Department");
    expect(description).toContain("3 open comment periods, 120 Federal Register documents");
  });

  it("states the latest reported year, never $0 for a year not yet reported", () => {
    const rrb = {
      ...fda,
      parent: null,
      shortName: "RRB",
      name: "Railroad Retirement Board",
      budget: {
        reported: false,
        budgetAuthority: null,
        fiscalYear: 2026,
        latest: { fiscalYear: 2025, budgetaryResources: 17_820_000_000 },
      },
    } as unknown as AgencyDetail;
    const { description } = describeAgency(rrb);
    expect(description).toContain("$17.8B in budgetary resources in FY2025");
    expect(description).not.toContain("$0");
  });

  it("names its department as the parent organization", () => {
    const ld = agencyJsonLd(fda);
    expect(ld["@type"]).toBe("GovernmentOrganization");
    expect(ld.parentOrganization?.url).toMatch(/\/agencies\/health-and-human-services-department$/);
  });
});

describe("describeElectionMap", () => {
  it("names the cycle the backend reports, and no year without one", () => {
    expect(describeElectionMap(2028).title).toBe(
      "2028 Senate and House Map: Usual Vote and Poll Ranges"
    );
    expect(describeElectionMap(2028).description).toContain("each 2028 Senate and House seat");
    expect(describeElectionMap().title).toBe("Senate and House Map: Usual Vote and Poll Ranges");
    expect(describeElectionMap().description).not.toMatch(/\d{4}/);
  });
});
