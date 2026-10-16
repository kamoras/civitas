/**
 * Search-facing text and schema.org data for the server-rendered detail
 * routes. Kept out of the `page.tsx` files because Next rejects non-route
 * exports there, and so the wording can be unit-tested.
 */
import type { ActionIssue } from "@/types/action";
import type { BillDetail } from "@/types/bill";
import type { PoliticianProfile } from "@/types/politicians";
import type { VoteDetail } from "@/types/congress";
import type { AgencyDetail } from "@/types/agency";
import { formatCurrency } from "@/lib/formatting";
import type { NominationDetail } from "@/types/nomination";
import {
  BSKY_PROFILE_URL,
  GITHUB_REPO_URL,
  HOME_DESCRIPTION,
  SITE_ALTERNATE_NAMES,
  SITE_NAME,
  SITE_URL,
  absoluteUrl,
} from "@/lib/site";
import { billCanonicalPath, calendarDate } from "./congress";

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
  // Justices: the profile's role is Oyez's full title ("Associate Justice of
  // the Supreme Court of the United States"), which would say "Supreme
  // Court" twice and push the name out of a search result's title.
  const seat = role.includes("Chief") ? "Chief Justice" : "Associate Justice";
  return {
    title: `${seat} ${name}: Supreme Court Voting Record`,
    description: `${seat} ${name}: Supreme Court voting record, opinions, and votes under the appointing president, from public case records. Not scored.`,
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
    ...(identity.thumbnailUrl ? { image: absoluteUrl(identity.thumbnailUrl) } : {}),
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
export function describeBill(
  bill: BillDetail,
  // The Record's spelling ("H.R. 7008", the backend's billLabel), which is
  // what people type; the site's id ("HR.7008") when none is known.
  label: string = bill.billId
): { title: string; description: string } {
  const sponsor = `${bill.sponsorName} (${bill.sponsorParty}-${bill.sponsorState})`;
  const status = bill.isLaw
    ? "Became law."
    : bill.latestAction
      ? `Latest action: ${bill.latestAction}`
      : "";
  return {
    // The short title when Congress.gov has one the listing lacks: it is the
    // name people search for ("One Big Beautiful Bill Act").
    title: `${label}: ${bill.shortTitle || bill.title}`,
    description:
      `${label} (${ordinal(bill.congress)} Congress, ${CHAMBER_NAMES[bill.chamber] ?? bill.chamber}), sponsored by ${sponsor}. ${status}`.trim(),
  };
}

/** "<nominee>, of <state>, to be <office>: Senate roll call 256" —
 * what the vote was about leads (a nominee's name, a bill's number), since
 * that is what people search for; the question and result follow. */
export function describeRollCall(v: VoteDetail): { title: string; description: string } {
  const chamber = CHAMBER_NAMES[v.chamber] ?? v.chamber;
  const subject = v.billLabel && v.title ? `${v.billLabel}: ${v.title}` : v.title || v.question;
  const members = v.chamber === "senate" ? "senator" : "representative";
  return {
    title: `${subject}: ${chamber} roll call ${v.number}`,
    description:
      `${v.question}: ${v.result || "recorded"}, ${v.yeas}–${v.nays}, ${calendarDate(v.date)}. ` +
      `How every ${members} voted, by party, from the ${chamber}'s own record.`,
  };
}

/** A nomination's page: who, for what, and where it stands. */
export function describeNomination(
  n: NominationDetail,
  heading: string
): { title: string; description: string } {
  const where =
    n.status === "confirmed" && n.statusDate
      ? `confirmed ${calendarDate(n.statusDate)}${n.daysToConfirm != null ? `, ${n.daysToConfirm} days after nomination` : ""}`
      : n.status === "pending"
        ? `pending since ${calendarDate(n.receivedDate)}`
        : `${n.status} ${n.statusDate ? calendarDate(n.statusDate) : ""}`.trim();
  return {
    title: `${heading}: Nomination ${n.citation}`,
    description: `${n.description} ${where[0].toUpperCase()}${where.slice(1)}. Committee, hearings, every action and how each senator voted.`,
  };
}

export function legislationJsonLd(bill: BillDetail) {
  return {
    "@context": "https://schema.org",
    "@type": "Legislation",
    name: bill.shortTitle || bill.title,
    ...(bill.shortTitle ? { alternateName: bill.officialTitle || bill.title } : {}),
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

// Node ids, so every other page's data can name the publisher by
// reference rather than repeat it (schema.org @id; Google joins the nodes).
export const ORGANIZATION_ID = `${SITE_URL}/#organization`;
export const WEBSITE_ID = `${SITE_URL}/#website`;

/**
 * The site as an entity, on the homepage: the WebSite node is what Google's
 * site-name system reads first (name, alternateName, url — "Provide a site
 * name to Google Search"), and the Organization node is the publisher with
 * its logo and its profiles elsewhere (sameAs). Both carry the full name,
 * since "Civitas" alone names many other organizations.
 *
 * The SearchAction is schema.org's description of /explore's search. Google
 * retired the sitelinks search box it once drew from this (2024); it is
 * kept because it states a true fact about the site to any reader of it.
 */
export function siteJsonLd() {
  return [
    {
      "@context": "https://schema.org",
      "@type": "WebSite",
      "@id": WEBSITE_ID,
      name: SITE_NAME,
      alternateName: SITE_ALTERNATE_NAMES,
      url: `${SITE_URL}/`,
      description: HOME_DESCRIPTION,
      inLanguage: "en-US",
      publisher: { "@id": ORGANIZATION_ID },
      potentialAction: {
        "@type": "SearchAction",
        target: {
          "@type": "EntryPoint",
          urlTemplate: `${SITE_URL}/explore?q={search_term_string}`,
        },
        "query-input": "required name=search_term_string",
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "@id": ORGANIZATION_ID,
      name: SITE_NAME,
      alternateName: SITE_ALTERNATE_NAMES,
      url: `${SITE_URL}/`,
      // A raster logo at least 112px square (Google's Organization logo
      // guidelines): the 192px app icon, not the 32-unit SVG favicon.
      logo: absoluteUrl("/apple-icon.png"),
      description:
        "An independent, open-source political transparency project that scores U.S. members of Congress and presidents from public records.",
      sameAs: [BSKY_PROFILE_URL, GITHUB_REPO_URL],
    },
  ];
}

/** /about, as the page describing the organization (schema.org AboutPage). */
export function aboutPageJsonLd(description: string) {
  return {
    "@context": "https://schema.org",
    "@type": "AboutPage",
    name: `About ${SITE_NAME}`,
    url: absoluteUrl("/about"),
    description,
    isPartOf: { "@id": WEBSITE_ID },
    about: { "@id": ORGANIZATION_ID },
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
    // Named in full as well as by @id: the Organization node itself is
    // only on the homepage, and Google reads each page on its own.
    publisher: { "@type": "Organization", "@id": ORGANIZATION_ID, name: SITE_NAME, url: SITE_URL },
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

/** The redistricting index: what people search when a map changes. */
export function describeRedistrictingIndex(
  cycle: string | null,
  states: number,
  upcoming = true
): {
  title: string;
  description: string;
} {
  const year = cycle ?? "";
  return {
    title: `New ${year} congressional maps: what changed in each district`.replace("  ", " "),
    description: `${states} states ${upcoming ? "vote" : "voted"} on new U.S. House maps in ${year}. For each district: where its residents lived before, how its partisan lean moved, and the act or court order behind the map. Census counts, downloadable.`,
  };
}

/** /elections/calendar: special elections and every federal date. Counts
 * are the backend's; null when it couldn't be read. */
export function describeElectionCalendar(
  upcomingSpecials: number | null,
  vacancies: number | null
): { title: string; description: string } {
  const lead =
    upcomingSpecials == null
      ? ""
      : upcomingSpecials === 0
        ? "No federal special election is scheduled. "
        : `${upcomingSpecials} federal special election date${upcomingSpecials === 1 ? "" : "s"} ahead. `;
  const empty =
    vacancies == null
      ? ""
      : vacancies === 0
        ? "Every seat in Congress is filled. "
        : `${vacancies} seat${vacancies === 1 ? "" : "s"} in Congress empty today. `;
  return {
    title: "Special elections and election calendar",
    description: `${lead}${empty}Every federal primary, runoff and special election date from the FEC, each state's voter deadlines, and campaign finance reporting deadlines, with special elections' official counts and their swing against each seat's lean.`,
  };
}

/** One state's redistricting page: "Texas new congressional map 2026". */
export function describeStateRedistricting(
  stateName: string,
  cycle: string,
  seats: number,
  movedPct: number
): { title: string; description: string } {
  return {
    title: `${stateName} new congressional map ${cycle}: what changed in your district`,
    description: `${stateName}'s ${seats} U.S. House districts on the ${cycle} map: where each district's residents lived before (${movedPct}% are in a district built mostly from another), how each lean moved, and the official source. Find yours by town or county.`,
  };
}

/** /elections/map: the Senate and House map of the cycle the backend
 * names (usual vote, poll ranges); no year when the map isn't up. Never
 * "forecast", "prediction" or "projection" — the page is none of those. */
export function describeElectionMap(cycle?: number): {
  title: string;
  description: string;
} {
  const year = cycle ? `${cycle} ` : "";
  return {
    title: `${year}Senate and House Map: Usual Vote and Poll Ranges`,
    description: `How each ${year}Senate and House seat usually votes, a range for the margin (from this year's polls where a seat has any), and where the two disagree. Ranges, not results; not a race call. Method and held-out calibration shown.`,
  };
}

/** /elections/archive: every general election Civitas has kept a record of. */
export function describeElectionArchiveIndex(): { title: string; description: string } {
  return {
    title: "Past U.S. general elections: results, ballots and the map before the vote",
    description:
      "Each general election Civitas covered, kept after the site moves on: every Senate and House race's count as its state reported it, the candidates and statewide ballot measures on the ballot, and the seat map as it stood the day before.",
  };
}

/** /elections/{cycle}: "2026 general election results, ballot and pre-election map". */
export function describeElectionArchive(
  cycle: number,
  races: number,
  measures: number
): { title: string; description: string } {
  return {
    title: `${cycle} general election: results, ballot and pre-election map`,
    description: `The ${cycle} U.S. general election as Civitas recorded it: the count in ${races} Senate and House races as each state reported it, the candidates on the ballot and ${measures} statewide ballot measure${measures === 1 ? "" : "s"}, quoted from their sources, and the seat map as it stood before election day. No race is called.`,
  };
}

/** The agency directory: "Federal agencies: budgets, rules and comment periods". */
export function describeAgencyIndex(): { title: string; description: string } {
  return {
    title: "Federal agencies: budgets, rules and open comment periods",
    description:
      "Every federal agency the Federal Register lists: what each one spends (USAspending.gov), the rules it proposes and issues, the executive orders that name it, and when you can comment.",
  };
}

/** One agency: "Environmental Protection Agency (EPA): budget, rules and comment periods". */
export function describeAgency(a: AgencyDetail): { title: string; description: string } {
  const name = a.shortName && a.shortName !== a.name ? `${a.name} (${a.shortName})` : a.name;
  const parts = [
    a.budget?.reported && a.budget.budgetAuthority != null
      ? `${formatCurrency(a.budget.budgetAuthority)} in budgetary resources in FY${a.budget.fiscalYear}`
      : a.budget?.latest?.budgetaryResources != null
        ? `${formatCurrency(a.budget.latest.budgetaryResources)} in budgetary resources in FY${a.budget.latest.fiscalYear}`
        : null,
    a.documents.openCommentsCount > 0
      ? `${a.documents.openCommentsCount} open comment period${a.documents.openCommentsCount === 1 ? "" : "s"}`
      : null,
    a.documents.total > 0
      ? `${a.documents.total.toLocaleString("en-US")} Federal Register documents`
      : null,
  ].filter(Boolean);
  return {
    title: `${name}: budget, rules and comment periods`,
    description: `${name}${a.parent ? `, part of the ${a.parent.name}` : ""}: ${
      parts.length ? parts.join(", ") : "its Federal Register profile"
    }. What it does, what it spends and what it is proposing, from official sources.`,
  };
}

export function agencyJsonLd(a: AgencyDetail) {
  return {
    "@context": "https://schema.org",
    "@type": "GovernmentOrganization",
    name: a.name,
    ...(a.shortName ? { alternateName: a.shortName } : {}),
    url: absoluteUrl(`/agencies/${a.slug}`),
    ...(a.website || a.frUrl ? { sameAs: [a.website, a.frUrl].filter(Boolean) } : {}),
    ...(a.parent
      ? {
          parentOrganization: {
            "@type": "GovernmentOrganization",
            name: a.parent.name,
            url: absoluteUrl(`/agencies/${a.parent.slug}`),
          },
        }
      : {}),
  };
}
