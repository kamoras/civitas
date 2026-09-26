/**
 * Search-facing text and schema.org data for the server-rendered detail
 * routes. Kept out of the `page.tsx` files because Next rejects non-route
 * exports there, and so the wording can be unit-tested.
 */
import type { ActionIssue } from "@/types/action";
import type { BillDetail } from "@/types/bill";
import type { PoliticianProfile } from "@/types/politicians";
import { SITE_NAME, SITE_URL, absoluteUrl } from "@/lib/site";

const PARTY_ADJECTIVES: Record<string, string> = { D: "Democratic", R: "Republican", I: "independent" };
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
export function describeProfile(profile: PoliticianProfile): { title: string; description: string } {
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
      description: `${name}, ${partyWord} ${former ? "former " : ""}${role.toLowerCase()} for ${seat}: voting record, top donors and PAC money, sponsored bills, and representation score.`,
    };
  }
  if (branch === "president") {
    return {
      title: `${name}: Presidential Record & Scorecard`,
      description: `${name}, ${role}: executive orders, public record, and scorecard — built from Federal Register and other public federal records.`,
    };
  }
  // Justices: role is "Chief Justice" / "Associate Justice".
  return {
    title: `${role} ${name}: Supreme Court Voting Record`,
    description: `${role} ${name}: Supreme Court voting record, opinions, and impartiality scorecard — from public case records.`,
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
  const status = bill.isLaw ? "Became law." : bill.latestAction ? `Latest action: ${bill.latestAction}` : "";
  return {
    title: `${bill.billId}: ${bill.title}`,
    description: `${bill.billId} (${ordinal(bill.congress)} Congress, ${CHAMBER_NAMES[bill.chamber] ?? bill.chamber}), sponsored by ${sponsor}. ${status}`.trim(),
  };
}

export function legislationJsonLd(bill: BillDetail) {
  return {
    "@context": "https://schema.org",
    "@type": "Legislation",
    name: bill.title,
    legislationIdentifier: bill.billId,
    url: absoluteUrl(`/bills/${encodeURIComponent(bill.billId)}`),
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
