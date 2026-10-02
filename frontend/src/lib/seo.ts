/**
 * Search-facing text and schema.org data for the server-rendered detail
 * routes. Kept out of the `page.tsx` files because Next rejects non-route
 * exports there, and so the wording can be unit-tested.
 */
import type { ActionIssue } from "@/types/action";
import type { BillDetail } from "@/types/bill";
import type { PoliticianProfile } from "@/types/politicians";
import { SITE_NAME, SITE_URL, absoluteUrl } from "@/lib/site";
import { billCanonicalPath } from "./congress";

const PARTY_ADJECTIVES: Record<string, string> = {
  D: "Democratic",
  R: "Republican",
  I: "independent",
};
// No entry for "I": an independent has no party to be affiliated with.
const PARTY_ORGS: Record<string, string> = { D: "Democratic Party", R: "Republican Party" };

const BODY_NAMES: Record<string, string> = {
  senate: "United States Senate",
  house: "United States House of Representatives",
  president: "Executive Office of the President of the United States",
  scotus: "Supreme Court of the United States",
};

/**
 * Title and description in the words people search with. The query this
 * page answers is "<name> voting record" / "<name> donors"; the old title
 * was just "<name> — Civitas", and the description never said what the
 * page contains.
 *
 * The (party-state) tag is for members of Congress only: a justice's
 * `party` is the APPOINTING president's party, not the justice's own, and
 * historical presidents carry Whig/Federalist codes a reader wouldn't parse.
 */
export function describeProfile(profile: PoliticianProfile): {
  title: string;
  description: string;
} {
  const { identity, branch } = profile;
  const { name, party, state, stateName, role, district } = identity;
  const former = identity.isCurrent === false || identity.isActive === false;

  if (branch === "senate" || branch === "house") {
    const prefix = `${former ? "Former " : ""}${branch === "senate" ? "Sen." : "Rep."}`;
    const seat =
      branch === "house" && typeof district === "number"
        ? district > 0
          ? `${stateName ?? state}'s ${ordinal(district)} district`
          : `${stateName ?? state} (at-large)`
        : (stateName ?? state);
    const partyWord = PARTY_ADJECTIVES[party] ?? party;
    return {
      title: `${prefix} ${name} (${party}-${state}): Voting Record, Donors & Scorecard`,
      description: `${name}, ${former ? "former " : ""}${partyWord} ${role.toLowerCase()} for ${seat}: voting record, top donors and PAC money, sponsored bills, and representation score.`,
    };
  }
  if (branch === "president") {
    return {
      title: `${name}: Presidential Record & Scorecard`,
      description: `${name}, ${role}: executive orders, public record, and scorecard, built from Federal Register and other public federal records.`,
    };
  }
  // Justices: role is "Chief Justice" / "Associate Justice".
  return {
    title: `${role} ${name}: Supreme Court Voting Record`,
    description: `${role} ${name}: Supreme Court voting record, opinions, and impartiality scorecard, from public case records.`,
  };
}

function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  return `${n}${({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th"}`;
}

export function personJsonLd(id: string, profile: PoliticianProfile) {
  const { identity, branch } = profile;
  return {
    "@context": "https://schema.org",
    "@type": "Person",
    name: identity.name,
    url: absoluteUrl(`/politicians/${encodeURIComponent(id)}`),
    jobTitle: identity.role,
    ...(identity.thumbnailUrl ? { image: identity.thumbnailUrl } : {}),
    ...(branch in BODY_NAMES
      ? { memberOf: { "@type": "GovernmentOrganization", name: BODY_NAMES[branch] } }
      : {}),
    ...((branch === "senate" || branch === "house") && PARTY_ORGS[identity.party]
      ? { affiliation: { "@type": "PoliticalParty", name: PARTY_ORGS[identity.party] } }
      : {}),
    ...(identity.websiteUrl ? { sameAs: [identity.websiteUrl] } : {}),
  };
}

const CHAMBER_NAMES: Record<string, string> = { senate: "Senate", house: "House" };

/** "S.4967, the Foo Act: sponsor, status, and votes" — the bill number is
 * what people paste into a search box, so it leads. */
export function describeBill(bill: BillDetail): { title: string; description: string } {
  const sponsor = `${bill.sponsorName} (${bill.sponsorParty}-${bill.sponsorState})`;
  const status = bill.isLaw
    ? "Became law."
    : bill.latestAction
      ? `Latest action: ${bill.latestAction}`
      : "";
  return {
    title: `${bill.billId}: ${bill.title}`,
    description:
      `${bill.billId} (${ordinal(bill.congress)} Congress, ${CHAMBER_NAMES[bill.chamber] ?? bill.chamber}), sponsored by ${sponsor}. ${status}`.trim(),
  };
}

export function legislationJsonLd(bill: BillDetail) {
  return {
    "@context": "https://schema.org",
    "@type": "Legislation",
    name: bill.title,
    legislationIdentifier: bill.billId,
    url: absoluteUrl(billCanonicalPath(bill.billId, bill.congress)),
    ...(bill.introducedDate ? { legislationDate: bill.introducedDate } : {}),
    legislationJurisdiction: "US",
    sponsor: {
      "@type": "Person",
      name: bill.sponsorName,
      url: absoluteUrl(`/politicians/${encodeURIComponent(bill.sponsorId)}`),
    },
  };
}

/** An Action Center issue as a news-style Article. */
export function articleJsonLd(issue: ActionIssue) {
  const url = absoluteUrl(`/issue/${issue.publicId}`);
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: issue.title.slice(0, 110),
    description: issue.summary,
    url,
    mainEntityOfPage: url,
    image: absoluteUrl(`/api/og?issue=${issue.publicId}`),
    datePublished: issue.firstSurfaced || issue.date,
    dateModified: issue.date,
    publisher: { "@type": "Organization", name: SITE_NAME, url: SITE_URL },
  };
}

/**
 * /elections' title and description, by what the page is showing
 * (`mode`): the live count from the first state's last polls closing
 * until the results window closes (backend election_phase); on election
 * day before then, no count yet and — as all day — no partisan lean; the
 * rest of the time a ballot-research index with its lean map. The results
 * wording is kept under metaDescription's 160 characters, so the part that
 * says Civitas calls no race is never the part cut off.
 */
export function describeElections(
  cycleYear: number | null,
  mode: "campaign" | "election_day" | "results"
): { title: string; description: string } {
  const year = cycleYear ? `${cycleYear} ` : "";
  if (mode === "results") {
    return {
      title: `${year}Election Results by State: Senate & House Count`,
      description: `The ${year}Senate and House count by state, as each state's election office publishes it. A race "leads" even once its count is official; Civitas calls no race.`,
    };
  }
  if (mode === "election_day") {
    return {
      title: `${year}Election Day: Senate & House Races by State`,
      description: `Election day: each state's count appears here once its last polls close, as its election office publishes it. Senate and House candidates, and ballot measures.`,
    };
  }
  return {
    title: `${year}Elections by State: Senate, House & Ballot Measures`,
    description: `Every ${year}U.S. Senate and House race by state: candidates, FEC fundraising, partisan lean, and statewide ballot measures quoted from official sources.`,
  };
}

/**
 * A state page's title and description. In the results window the page
 * leads with the state's count where Civitas reads one live (`live`), and
 * otherwise says where the state publishes it; the ballot research stays
 * below either. `live` is null when that couldn't be checked, and the
 * wording then promises neither. Results wording fits in 160 characters.
 */
export function describeStateBallot(
  ballot: {
    stateName?: string | null;
    state: string;
    cycleYear: number;
    electionDate: string;
    measures: unknown[];
  },
  resultsMode: boolean,
  live: boolean | null
): { title: string; description: string } {
  const name = ballot.stateName ?? ballot.state;
  const year = ballot.cycleYear;
  if (resultsMode) {
    return {
      // "Count" only where the page shows one: a state not read live says
      // where its count is published, not what it is.
      title:
        live === true
          ? `${name} Election Results ${year}: Senate & House Count and Ballot`
          : `${name} Election Results ${year}: Where to Find Them, and Ballot`,
      description:
        live === true
          ? `${name}'s ${year} Senate & House count as its election office publishes it ("leads" even once official; Civitas calls no race), and who was on the ballot.`
          : live === false
            ? `Where ${name}'s election office publishes its ${year} count (Civitas doesn't read it live), and who was on the ballot, with their FEC fundraising.`
            : `${name}'s ${year} election: where the state publishes its count, and who was on the ballot, with their FEC fundraising.`,
    };
  }
  const n = ballot.measures.length;
  const measures =
    n > 0
      ? `, and ${n} statewide ballot ${n === 1 ? "measure" : "measures"} quoted from official sources`
      : "";
  return {
    title: `${name} Ballot ${year}: Senate, House Races & Ballot Measures`,
    description: `What's on the ${year} ${name} ballot (${ballot.electionDate}): U.S. Senate and House candidates with FEC fundraising${measures}.`,
  };
}
