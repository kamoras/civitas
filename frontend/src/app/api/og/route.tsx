import { formatCurrency, restatesTitle } from "@/lib/formatting";
import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";
import { OG_FAMILY, ogFonts } from "@/lib/ogFonts";
import { OgLockup } from "@/lib/ogLockup";
import { OG_COLOR as C } from "@/lib/ogTheme";
import { SITE_DOMAIN } from "@/lib/site";
import { fetchPhoto, parsePhotoPath } from "@/lib/photos";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";
import { usableRecord } from "@/lib/ssrPayload";
import {
  ISO_DATE,
  ISO_MONTH,
  fetchBillRecord,
  fetchDay,
  fetchMonth,
  fetchWeek,
} from "@/lib/congressServer";
import { calendarDate, longDate, monthLabel } from "@/lib/congress";
import { fetchAgency } from "@/lib/agencyServer";
import { candidateName, raceSeat } from "@/lib/elections";
import { CITATION, KIND_LABEL, STATUS_LABEL, nominationHeading } from "@/lib/nominations";
import { type SectionCard, SECTION_CARDS } from "@/lib/ogSections";
import { agencyBudget } from "@/lib/seo";
import type { BillRecord, Chamber, ChamberStatus, DayReport, PeriodReport } from "@/types/congress";
import type { StateBallot } from "@/types/election";
import type { ArchiveRecord } from "@/types/electionArchive";
import type { NominationDetail } from "@/types/nomination";
import type { StateRedistricting } from "@/types/redistricting";

export const runtime = "nodejs";

// ImageResponse sends "public, max-age=0, must-revalidate" in production, and
// nginx honours that over its own proxy_cache_valid, so without this every
// link-preview fetch re-rendered the card and counted against the miss limit
// (civitas.conf's /api/og block). Five minutes matches the backend fetches'
// revalidate below: a day card that says "no record yet" catches up soon.
const OG_HEADERS = { "Cache-Control": "public, max-age=300" };

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Satori (next/og's renderer) takes inline style objects only, no Tailwind
// classes or CSS custom properties, so the cards read the light theme's
// tokens from lib/ogTheme.ts (kept equal to globals.css by its test). A
// shared link lands in feeds of every colour; the cards use the parchment
// theme, the site's default.
const INK_HI = C.inkHi;
const INK = C.ink;
const INK_LO = C.inkLo;
const INK_MIN = C.inkMin;
const SURFACE_BASE = C.parchment;
// Box borders and the foot rule: rule-strong, 3:1 on parchment.
const HAIRLINE = C.rule;
// The ledger-green band across the top of every card.
const TOP_BAND = `10px solid ${C.accent}`;
const DOMAIN = SITE_DOMAIN;

const PARTY_ACCENT: Record<string, string> = {
  D: C.dem,
  R: C.rep,
  I: C.ind,
};

async function fetchIssue(id: string) {
  try {
    const res = await fetch(`${BACKEND}/api/action/issues/${id}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// Inlines the photo as a data URI, fetched from its source by the same code
// the /photo route uses: the API gives a same-origin path, which satori
// can't fetch from inside the frontend container, and satori's own fetch
// wouldn't get past bioguide's bot-mitigation (see lib/remoteImage.ts).
async function fetchPhotoAsDataUri(path: string): Promise<string | null> {
  const ref = parsePhotoPath(path);
  const photo = ref ? await fetchPhoto(ref) : null;
  return photo?.status === "ok"
    ? `data:${photo.contentType};base64,${photo.bytes.toString("base64")}`
    : null;
}

async function fetchPolitician(id: string) {
  try {
    const res = await fetch(`${BACKEND}/api/politicians/${id}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function fetchStateBallot(state: string): Promise<StateBallot | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/states/${encodeURIComponent(state)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    // Same guard the actual ballot page's own fetchStateBallot applies
    // (lib/ssrPayload.ts) — a 200 with a `{}`-shaped body is truthy but
    // has no real data, and must not be trusted as a real StateBallot.
    return usableRecord<StateBallot>(await res.json(), "state", "senateRaces");
  } catch {
    return null;
  }
}

function Header({ section, date }: { section: string; date?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 28, marginBottom: 44 }}>
      <OgLockup size={24} />
      <span style={{ color: INK_LO, fontSize: 22, fontWeight: 600 }}>{section}</span>
      {date && <span style={{ marginLeft: "auto", color: INK_LO, fontSize: 22 }}>{date}</span>}
    </div>
  );
}

function Footer({ label }: { label: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        borderTop: `2px solid ${HAIRLINE}`,
        paddingTop: 28,
      }}
    >
      <span style={{ color: INK_LO, fontSize: 22, fontFamily: OG_FAMILY.mono }}>{DOMAIN}</span>
      <span style={{ color: INK_LO, fontSize: 20, fontWeight: 600 }}>{label}</span>
    </div>
  );
}

export type OgIssue = {
  publicId?: string;
  title?: string;
  summary?: string;
  summarySource?: string | null;
  date?: string;
  status?: string;
  sourceNames?: string[];
  imageUrl?: string | null;
  imageCredit?: string | null;
};

const MAX_NAMED_SOURCES = 3;

/** Every string the issue card renders besides its title. A link preview
 *  travels without the page around it, so the card carries what the page
 *  says beside the story: whose words the summary is, who took the photo,
 *  the day, which outlets (or record) it comes from and where it lives.
 *  It used to carry none of these, and labelled every story, news ones
 *  included, "PUBLIC FEDERAL DATA". */
export function issueCardTexts(issue: OgIssue | null) {
  const title = issue?.title ?? "Civitas Research Action Center";
  const summary = !issue?.summary
    ? "Track what Congress is doing, and what you can do about it."
    : restatesTitle(title, issue.summary)
      ? ""
      : issue.summary.length > 140
        ? issue.summary.slice(0, 137) + "…"
        : issue.summary;
  const names = (issue?.sourceNames ?? []).filter(Boolean);
  const named = names.slice(0, MAX_NAMED_SOURCES).join(", ");
  const more = names.length - MAX_NAMED_SOURCES;
  return {
    title,
    summary,
    // The summary is a source's own words (post_composer), so it is quoted
    // as theirs, as the issue page does.
    summaryCredit: summary && issue?.summarySource ? `Quoted from ${issue.summarySource}` : "",
    section: issue?.status === "developing" ? "Action Center · Developing" : "Action Center",
    date: issue?.date ? calendarDate(issue.date) : "",
    photoCredit: issue?.imageCredit ? `Photo: ${issue.imageCredit}` : "",
    link: issue?.publicId ? `${DOMAIN}/issue/${issue.publicId}` : DOMAIN,
    sources: named
      ? `${names.length === 1 ? "Source" : "Sources"}: ${named}${more > 0 ? ` and ${more} more` : ""}`
      : "Public records",
  };
}

async function issueImage(issue: OgIssue | null) {
  const t = issueCardTexts(issue);

  // Only ever set from a source article whose feed explicitly granted
  // redistribution rights (see backend news_feeds._rights_cleared_image)
  // — same fetch-and-inline treatment as a politician's headshot, since
  // there's no guarantee this URL is still live months after the source
  // article ran.
  const photoDataUri = issue?.imageUrl ? await fetchPhotoAsDataUri(issue.imageUrl) : null;

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <Header section={t.section} date={t.date} />
      <div style={{ display: "flex", alignItems: "center", gap: 40, flex: 1, marginBottom: 32 }}>
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div
            style={{
              color: INK_HI,
              fontSize: t.title.length > 60 ? 46 : 56,
              fontFamily: OG_FAMILY.serif,
              fontWeight: 600,
              lineHeight: 1.15,
              marginBottom: 32,
            }}
          >
            {t.title}
          </div>
          <div style={{ color: INK, fontSize: 26, lineHeight: 1.45 }}>{t.summary}</div>
          {t.summaryCredit && (
            <div style={{ color: INK_LO, fontSize: 20, marginTop: 10 }}>{t.summaryCredit}</div>
          )}
        </div>
        {photoDataUri && (
          <div style={{ display: "flex", flexDirection: "column", width: 320, gap: 8 }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={photoDataUri}
              alt=""
              width={320}
              height={320}
              style={{ objectFit: "cover", border: `1px solid ${HAIRLINE}` }}
            />
            {t.photoCredit && <span style={{ color: INK_MIN, fontSize: 18 }}>{t.photoCredit}</span>}
          </div>
        )}
      </div>
      <IssueFooter link={t.link} sources={t.sources} />
    </div>,
    {
      width: 1200,
      height: 630,
      headers: OG_HEADERS,
      fonts: await ogFonts(),
    }
  );
}

function IssueFooter({ link, sources }: { link: string; sources: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 24,
        borderTop: `2px solid ${HAIRLINE}`,
        paddingTop: 28,
      }}
    >
      <span style={{ color: INK_LO, fontSize: 20, fontFamily: OG_FAMILY.mono }}>{link}</span>
      <span style={{ color: INK_LO, fontSize: 20 }}>{sources}</span>
    </div>
  );
}

export type OgPoliticianIdentity = {
  name?: string;
  party?: string;
  state?: string;
  district?: number | null;
  role?: string;
  thumbnailUrl?: string | null;
};

// A truthy check on district treats an at-large seat (0, FEC's own "00"
// convention — see lib/elections.ts's districtToken) the same as "no
// district" (a Senate identity), silently rendering it in the Senate's
// format — this exact bug class already has a named regression test in
// lib/elections.test.ts.
export function formatStanding(identity: OgPoliticianIdentity | undefined): string {
  if (identity?.district != null) {
    const district = identity.district === 0 ? "AL" : identity.district;
    return `${identity.party}-${identity.state}-${district}`;
  }
  if (identity?.state) return `${identity.party}-${identity.state}`;
  return "";
}

export type OgProfile = {
  id?: string;
  branch?: string;
  identity?: OgPoliticianIdentity & {
    number?: number;
    termStart?: string;
    termEnd?: string | null;
    appointingPresident?: string;
    dateStart?: string;
  };
  scorecard?: {
    funding?: {
      totalContributions?: number | null;
      pacSharePct?: number | null;
      smallDonorPercentage?: number | null;
    } | null;
    avgApproval?: number | null;
    eoCount?: number | null;
    casesDecided?: number | null;
    majorityPct?: number | null;
    authoredMajority?: number | null;
  } | null;
};

/** The historians' rank of one presidency, from /api/presidents. */
export type OgHistoriansRank = { rank: number | null; rated: number | null };

// A tile's figure and what it counts, or (value null) a sentence in its
// place: an absence a bare 0 would misstate ("Measures not yet covered").
export type Tile = { value: string | null; label: string };

const pct = (v: number) => `${Math.round(v)}%`;

const BRANCH_EYEBROW: Record<string, string> = {
  senate: "U.S. Senate",
  house: "U.S. House",
  president: "President",
  scotus: "Supreme Court",
};

function ordinalNumber(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

/** Everything the profile card says besides the photo: the eyebrow, the
 *  line under the name, the figures and the sources they come from. Never
 *  a 0-100 score (retired 2026-10): the figures are the record's own, as
 *  the profile page shows them. A figure the record doesn't hold is left
 *  off, never drawn as zero. */
export function profileCardTexts(profile: OgProfile | null, historians?: OgHistoriansRank | null) {
  const branch = profile?.branch ?? "";
  const identity = profile?.identity;
  const sc = profile?.scorecard;
  const tiles: Tile[] = [];
  let line = formatStanding(identity);
  let sources = "The public record";
  if (branch === "senate" || branch === "house") {
    const f = sc?.funding;
    if (f?.totalContributions != null)
      tiles.push({
        value: formatCurrency(f.totalContributions),
        label: "Contributions, latest election",
      });
    if (f?.pacSharePct != null) tiles.push({ value: pct(f.pacSharePct), label: "From PACs" });
    if (f?.smallDonorPercentage != null)
      tiles.push({ value: pct(f.smallDonorPercentage), label: "In gifts of $200 or less" });
    if (tiles.length) sources = "FEC filings";
  } else if (branch === "president") {
    const start = identity?.termStart?.slice(0, 4);
    const end = identity?.termEnd ? identity.termEnd.slice(0, 4) : "present";
    line = [
      identity?.number ? `${ordinalNumber(identity.number)} president` : "",
      start ? `${start} to ${end}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    if (historians?.rank != null && historians.rated)
      tiles.push({
        value: `#${historians.rank} of ${historians.rated}`,
        label: "C-SPAN historians' rank",
      });
    if (sc?.avgApproval != null)
      tiles.push({ value: pct(sc.avgApproval), label: "Average approval" });
    if (sc?.eoCount != null) tiles.push({ value: String(sc.eoCount), label: "Executive orders" });
    // The survey rates only completed terms: said last, not led with.
    if (historians?.rank == null && historians?.rated)
      tiles.push({ value: "Not rated", label: "C-SPAN historians' rank" });
    sources = "C-SPAN · American Presidency Project · Federal Register";
  } else if (branch === "scotus") {
    line = [
      identity?.appointingPresident ? `Appointed by ${identity.appointingPresident}` : "",
      identity?.dateStart ? `since ${identity.dateStart.slice(0, 4)}` : "",
    ]
      .filter(Boolean)
      .join(", ");
    if (sc?.casesDecided != null)
      tiles.push({ value: String(sc.casesDecided), label: "Cases decided" });
    if (sc?.majorityPct != null)
      tiles.push({ value: pct(sc.majorityPct), label: "In the majority" });
    if (sc?.authoredMajority != null)
      tiles.push({ value: String(sc.authoredMajority), label: "Majority opinions written" });
    sources = "Supreme Court Database · Oyez";
  }
  return {
    eyebrow: BRANCH_EYEBROW[branch] ?? "Public record",
    name: identity?.name ?? "Civitas Research",
    line,
    tiles,
    sources,
  };
}

async function fetchHistoriansRank(id: string): Promise<OgHistoriansRank | null> {
  try {
    const res = await fetch(`${BACKEND}/api/presidents`, { next: { revalidate: 3600 } });
    if (!res.ok) return null;
    const list = (await res.json()) as {
      id: string;
      historiansRank: number | null;
      historiansRated: number | null;
    }[];
    const p = list.find((x) => x.id === id);
    return p ? { rank: p.historiansRank, rated: p.historiansRated } : null;
  } catch {
    return null;
  }
}

/** "Joseph R. Biden, Jr." -> "JB": first and last name's initials, suffixes
 *  and middle initials passed over. */
export function initialsOf(name: string): string {
  const words = name
    .replace(/,.*$/, "")
    .split(/\s+/)
    .filter((w) => w && !/^[A-Z]\.$/.test(w));
  if (!words.length) return "";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return `${first}${last}`;
}

async function politicianImage(profile: OgProfile | null) {
  const identity = profile?.identity;
  const historians =
    profile?.branch === "president" && profile.id ? await fetchHistoriansRank(profile.id) : null;
  const t = profileCardTexts(profile, historians);
  const accent = PARTY_ACCENT[identity?.party ?? ""] ?? INK_HI;

  const photoDataUri = identity?.thumbnailUrl
    ? await fetchPhotoAsDataUri(identity.thumbnailUrl)
    : null;

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <Header section={t.eyebrow} />
      <div style={{ display: "flex", alignItems: "center", gap: 48, flex: 1, marginBottom: 32 }}>
        {photoDataUri ? (
          // satori renders this tree standalone at request time; next/image's
          // client-side optimization runtime doesn't apply here.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={photoDataUri}
            alt=""
            width={250}
            height={300}
            style={{
              objectFit: "cover",
              objectPosition: "center 25%",
              border: `2px solid ${accent}`,
            }}
          />
        ) : (
          // No portrait (none on record, or it couldn't be fetched): the
          // initials box the profile header shows in its place.
          <div
            style={{
              display: "flex",
              width: 250,
              height: 300,
              alignItems: "center",
              justifyContent: "center",
              border: `2px solid ${accent}`,
              background: C.paper,
              color: accent,
              fontSize: 84,
              fontWeight: 600,
            }}
          >
            {initialsOf(t.name)}
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div
            style={{
              color: INK_HI,
              fontSize: t.name.length > 24 ? 46 : 56,
              fontFamily: OG_FAMILY.serif,
              fontWeight: 600,
              lineHeight: 1.1,
            }}
          >
            {t.name}
          </div>
          {t.line && (
            <div style={{ color: accent, fontSize: 28, fontWeight: 600, marginTop: 10 }}>
              {t.line}
            </div>
          )}
          {t.tiles.length > 0 && (
            <div style={{ display: "flex", gap: 16, marginTop: 32 }}>
              {t.tiles.map((tile) => (
                <StatTile key={tile.label} value={tile.value} label={tile.label} />
              ))}
            </div>
          )}
        </div>
      </div>
      <Footer label={t.sources} />
    </div>,
    {
      width: 1200,
      height: 630,
      headers: OG_HEADERS,
      fonts: await ogFonts(),
    }
  );
}

// A plain title/description card with no stat tiles — used when there is
// no real per-state data to show (fetch failed, or the code doesn't map
// to a real ballot jurisdiction), so nothing that LOOKS like a specific
// fact ("0 U.S. Senate races") gets asserted about a state we couldn't
// actually confirm anything for.
async function genericCard({
  section,
  title,
  description,
  footerLabel,
}: {
  section: string;
  title: string;
  description: string;
  footerLabel: string;
}) {
  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <Header section={section} />
      <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
        <div
          style={{
            color: INK_HI,
            fontSize: 52,
            fontFamily: OG_FAMILY.serif,
            fontWeight: 600,
            lineHeight: 1.15,
          }}
        >
          {title}
        </div>
        <div style={{ color: INK, fontSize: 28, marginTop: 18 }}>{description}</div>
      </div>
      <Footer label={footerLabel} />
    </div>,
    {
      width: 1200,
      height: 630,
      headers: OG_HEADERS,
      fonts: await ogFonts(),
    }
  );
}

function StatTile({ value, label }: Tile) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        justifyContent: value === null ? "center" : "flex-start",
        flex: 1,
        padding: "20px 24px",
        background: C.paper,
        border: `2px solid ${HAIRLINE}`,
      }}
    >
      {/* No fragment: satori lays a fragment's children out in a row. */}
      {value !== null && (
        <span style={{ color: INK_HI, fontSize: value.length > 7 ? 34 : 44, fontWeight: 600 }}>
          {value}
        </span>
      )}
      <span
        style={
          value === null
            ? { color: INK_HI, fontSize: 28, fontWeight: 600, lineHeight: 1.25 }
            : { color: INK_LO, fontSize: 20, fontWeight: 600, marginTop: 8, lineHeight: 1.3 }
        }
      >
        {label}
      </span>
    </div>
  );
}

// A title, one line under it, and a row of stat tiles: the state ballot
// card and the Congress day and week cards.
async function tileCard({
  section,
  title,
  description,
  tiles,
  footerLabel,
}: {
  section: string;
  title: string;
  description: string;
  tiles: Tile[];
  footerLabel: string;
}) {
  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <Header section={section} />
      <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
        <div
          style={{
            color: INK_HI,
            fontSize: title.length > 30 ? 44 : 56,
            fontFamily: OG_FAMILY.serif,
            fontWeight: 600,
            lineHeight: 1.15,
          }}
        >
          {title}
        </div>
        <div style={{ color: INK, fontSize: 28, marginTop: 16, marginBottom: 44 }}>
          {description}
        </div>
        <div style={{ display: "flex", gap: 20 }}>
          {tiles.map((t) => (
            <StatTile key={t.label} value={t.value} label={t.label} />
          ))}
        </div>
      </div>
      <Footer label={footerLabel} />
    </div>,
    {
      width: 1200,
      height: 630,
      headers: OG_HEADERS,
      fonts: await ogFonts(),
    }
  );
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

async function electionImage(ballot: StateBallot | null, code: string) {
  const section = "Elections";
  const footerLabel = "Federal and statewide ballot";

  // A failed/unavailable fetch must never render as "this state has zero
  // races and zero measures" — that's a specific, false claim, not an
  // honest "we don't know" (see AGENTS.md's "Ballot content is quoted,
  // never generated": absence must be able to say WHICH absence it is,
  // the same discipline the actual ballot page already applies via
  // MeasureCoverage's confirmed_none/not_yet_covered/ingest_failed split).
  // A guessed `${code} Ballot ${currentYear}` title made the same mistake
  // for the cycle year, so the whole card falls back to generic branding
  // instead of asserting anything state-specific it can't back up.
  if (!ballot) {
    return genericCard({
      section,
      title: `${STATE_NAMES[code] ?? code}: the ballot`,
      description: "Federal contests and statewide ballot measures.",
      footerLabel,
    });
  }

  const title = `${ballot.stateName ?? STATE_NAMES[code] ?? code} ballot, ${ballot.cycleYear}`;
  const description = ISO_DATE.test(ballot.electionDate)
    ? `Election day: ${calendarDate(ballot.electionDate)}`
    : `Election day: ${ballot.electionDate}`;
  return tileCard({ section, title, description, tiles: ballotTiles(ballot), footerLabel });
}

// The state ballot card's tiles. A count is shown only where it is one; an
// empty list is worded as the absence it is (AGENTS.md §7: null is not
// zero), never "0": a state with no Senate seat up has no Senate race, and
// a state whose measures haven't been read (MeasureCoverage not_yet_covered
// or ingest_failed) has measures nobody has counted. Wisconsin's card read
// "0 Ballot measures" that way (2026-10-21).
export function ballotTiles(ballot: StateBallot): Tile[] {
  const senate = ballot.senateRaces.length;
  const house = ballot.houseRaces.length;
  // On the ballot: a removed or withdrawn measure is shown on the page for
  // a while, marked, but is not something a voter marks (ballotContests).
  const listed = ballot.measures.length;
  const measures = ballot.measures.filter(
    (m) => m.status !== "removed" && m.status !== "withdrawn"
  ).length;
  const confirmedNone = ballot.measureCoverage.status === "confirmed_none";
  return [
    senate > 0
      ? { value: String(senate), label: plural(senate, "U.S. Senate race", "U.S. Senate races") }
      : {
          value: null,
          // nextSenateElection is null only where there is no Senate seat (DC).
          label:
            ballot.nextSenateElection === null
              ? "No U.S. Senate seat"
              : `No Senate race in ${ballot.cycleYear}`,
        },
    house > 0
      ? { value: String(house), label: plural(house, "U.S. House race", "U.S. House races") }
      : { value: null, label: "House races not listed yet" },
    measures > 0
      ? {
          value: String(measures),
          label: plural(measures, "Statewide ballot measure", "Statewide ballot measures"),
        }
      : {
          value: null,
          label: confirmedNone
            ? "No statewide measures"
            : listed > 0 && ballot.measureCoverage.status === "covered"
              ? "No statewide measures remaining"
              : "Measures not yet covered",
        },
  ];
}

const CHAMBER_NAME: Record<Chamber, string> = { senate: "Senate", house: "House" };

// What a chamber's tile says when its day has no counts to show: the day
// page's own distinction, so a day without a record never reads as zero.
const NO_COUNT_LABEL: Partial<Record<ChamberStatus, string>> = {
  not_in_session: "not in session",
  no_record: "no record yet",
  no_record_published: "no record published",
};

export function chamberTile(report: DayReport, chamber: Chamber): { value: string; label: string } {
  const day = report.chambers[chamber];
  const none = NO_COUNT_LABEL[day.status];
  return none
    ? { value: "—", label: `${CHAMBER_NAME[chamber]}: ${none}` }
    : { value: String(day.counts.recordVotes), label: `${CHAMBER_NAME[chamber]} record votes` };
}

function summaryLine(sentence: string): string {
  return sentence.length > 140 ? sentence.slice(0, 137) + "…" : sentence;
}

// Fetch failures throw (lib/congressServer), and a date the backend has no
// record of is null: either way the card says only what is certain.
async function tryFetch<T>(fetcher: () => Promise<T | null>): Promise<T | null> {
  try {
    return await fetcher();
  } catch {
    return null;
  }
}

async function congressDayImage(date: string) {
  const section = "Congress";
  const footerLabel = "Congressional Record";
  const report = await tryFetch(() => fetchDay(date));
  if (!report) {
    return genericCard({
      section,
      title: `Congress on ${longDate(date)}`,
      description: "What the Senate and the House did, from the official record.",
      footerLabel,
    });
  }
  return tileCard({
    section,
    title: `Congress on ${longDate(report.date)}`,
    description: summaryLine(report.sentence),
    tiles: [chamberTile(report, "senate"), chamberTile(report, "house")],
    footerLabel,
  });
}

// A week's or a month's report: the same three tiles.
async function congressPeriodImage(
  title: string,
  fetcher: () => Promise<PeriodReport | null>,
  fallbackSpan: string
) {
  const section = "Congress";
  const footerLabel = "Congressional Record";
  const report = await tryFetch(fetcher);
  if (!report) {
    return genericCard({
      section,
      title,
      description: `What the Senate and the House did ${fallbackSpan}, from the official record.`,
      footerLabel,
    });
  }
  return tileCard({
    section,
    title,
    description: summaryLine(report.sentence),
    tiles: [
      { value: String(report.totals.senate.recordVotes), label: "Senate record votes" },
      { value: String(report.totals.house.recordVotes), label: "House record votes" },
      { value: String(report.becameLaw.length), label: "Became law" },
    ],
    footerLabel,
  });
}

function congressWeekImage(date: string) {
  return congressPeriodImage(
    `Congress, week of ${longDate(date).replace(/^\w+, /, "")}`,
    () => fetchWeek(date),
    "that week"
  );
}

function congressMonthImage(month: string) {
  return congressPeriodImage(
    `Congress in ${monthLabel(month)}`,
    () => fetchMonth(month),
    "that month"
  );
}

// One official record (a roll call, a bill, a congressional-record event, a
// Federal Register document) as the backend's record card describes it
// (backend app/services/record_cards.py). The card's `alt` text is built
// from exactly these fields, so the image shows every one of them and
// nothing else: no wordmark, no label of our own.
export type RecordCardParty = { party: string; name: string; count: number };
export type RecordCardTally = {
  position: string;
  label: string;
  total: number;
  parties: RecordCardParty[];
};
export type RecordCard = {
  kind: string;
  key: string;
  eyebrow: string;
  headline: string;
  date: string | null;
  path: string;
  source_name: string;
  as_of: string;
  lines: string[];
  tally: RecordCardTally[];
  source: string;
  alt: string;
};

async function fetchRecordCard(key: string, title: boolean): Promise<RecordCard | null> {
  try {
    const res = await fetch(`${BACKEND}/api/cards/${key}?title=${title ? 1 : 0}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return usableRecord<RecordCard>(await res.json(), "key", "source");
  } catch {
    return null;
  }
}

// The record's date as the alt text writes it ("October 9, 2026"), or
// nothing when it has none (backend record_cards.long_date).
function recordDate(card: RecordCard): string {
  const iso = (card.date ?? "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) ? calendarDate(iso) : "";
}

// The alt text names a party with a count of zero nowhere, so neither does
// the image.
function countedParties(row: RecordCardTally): RecordCardParty[] {
  return row.parties.filter((p) => p.count > 0);
}

/** Every string the record card image renders, in order. The tests check
 *  each is one of the card's own fields, so the image says nothing its alt
 *  text doesn't. The date is the record's, written as the alt text writes it. */
export function recordCardTexts(card: RecordCard): string[] {
  return [
    card.eyebrow,
    recordDate(card),
    card.headline,
    ...card.lines,
    ...card.tally.flatMap((row) => [
      row.label,
      String(row.total),
      ...countedParties(row).map((p) => `${p.name} ${p.count}`),
    ]),
    card.source,
  ].filter(Boolean);
}

function headlineSize(card: RecordCard): number {
  const length = card.headline.length + card.lines.join("").length;
  if (length > 300) return 28;
  if (length > 140) return 34;
  return card.headline.length > 60 ? 40 : 48;
}

async function recordCardImage(card: RecordCard) {
  const date = recordDate(card);
  const widest = Math.max(1, ...card.tally.map((row) => row.total));

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "56px 72px 48px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 24,
          color: INK_LO,
          fontSize: 22,
          fontWeight: 600,
          marginBottom: 28,
        }}
      >
        <span>{card.eyebrow}</span>
        {date && <span style={{ color: INK_LO, fontWeight: 400 }}>{date}</span>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
        {card.headline && (
          <div
            style={{
              color: INK_HI,
              fontSize: headlineSize(card),
              fontFamily: OG_FAMILY.serif,
              fontWeight: 600,
              lineHeight: 1.15,
            }}
          >
            {card.headline}
          </div>
        )}
        {card.lines.map((line) => (
          <div key={line} style={{ color: INK, fontSize: 24, lineHeight: 1.4, marginTop: 16 }}>
            {line}
          </div>
        ))}
        {card.tally.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 28 }}>
            {card.tally.map((row) => (
              <div key={row.position} style={{ display: "flex", alignItems: "center", gap: 20 }}>
                <span style={{ display: "flex", width: 170, color: INK_LO, fontSize: 22 }}>
                  {row.label}
                </span>
                <span
                  style={{
                    display: "flex",
                    width: 70,
                    justifyContent: "flex-end",
                    color: INK_HI,
                    fontSize: 26,
                    fontWeight: 600,
                  }}
                >
                  {String(row.total)}
                </span>
                <div style={{ display: "flex", flexDirection: "column", flex: 1, gap: 6 }}>
                  <div style={{ display: "flex", width: `${(row.total / widest) * 100}%` }}>
                    {countedParties(row).map((p) => (
                      <div
                        key={p.party}
                        style={{
                          display: "flex",
                          height: 14,
                          flex: p.count,
                          background: PARTY_ACCENT[p.party] ?? INK_MIN,
                        }}
                      />
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: 18, fontSize: 19, fontWeight: 600 }}>
                    {countedParties(row).map((p) => (
                      <span key={p.party} style={{ color: PARTY_ACCENT[p.party] ?? INK_MIN }}>
                        {`${p.name} ${p.count}`}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div
        style={{
          display: "flex",
          borderTop: `2px solid ${HAIRLINE}`,
          paddingTop: 22,
          marginTop: 24,
          color: INK_LO,
          fontSize: 20,
        }}
      >
        {card.source}
      </div>
    </div>,
    {
      width: 1200,
      height: 630,
      headers: OG_HEADERS,
      fonts: await ogFonts(),
    }
  );
}

// A section page's card (lib/ogSections.ts): its own words, from the list
// only, never from the query string.
function sectionImage(card: SectionCard) {
  return genericCard({
    section: card.eyebrow,
    title: card.title,
    description: card.subtitle,
    footerLabel: "An independent public record",
  });
}

async function fetchJson<T>(path: string, revalidate: number): Promise<T | null> {
  try {
    const res = await fetch(`${BACKEND}${path}`, { next: { revalidate } });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export type OgRaceCandidate = {
  name: string;
  ballotName?: string | null;
  displayName?: string | null;
  partyGroup?: string | null;
  partyLabel?: string | null;
  confirmed?: boolean;
  incumbentChallenge?: string | null;
  contributions?: number | null;
};

export type OgRace = {
  id: string;
  cycleYear: number;
  office: string;
  state: string;
  district: number | null;
  isSpecial?: boolean;
  usualVote?: { label?: string | null } | null;
  candidateSource?: string;
  candidates: OgRaceCandidate[];
};

const PARTY_GROUP: Record<string, { word: string; accent: string }> = {
  DEM: { word: "Democrat", accent: C.dem },
  REP: { word: "Republican", accent: C.rep },
};
const MAX_RACE_ROWS = 4;

/** What the race card says: the seat, its usual vote, and up to four
 *  candidates (the confirmed ones when the state has named them), each with
 *  the contributions the FEC reports. A candidate with no FEC total shows
 *  none, never $0. */
export function raceCardTexts(race: OgRace) {
  const state = STATE_NAMES[race.state] ?? race.state;
  const seat = raceSeat(race, state);
  const confirmed = race.candidates.filter((c) => c.confirmed);
  const pool = confirmed.length ? confirmed : race.candidates;
  const rows = [...pool]
    .sort((a, b) => (b.contributions ?? -1) - (a.contributions ?? -1))
    .slice(0, MAX_RACE_ROWS)
    .map((c) => {
      const party = PARTY_GROUP[c.partyGroup ?? ""];
      return {
        name: candidateName(c),
        party: party?.word ?? c.partyLabel ?? "",
        accent: party?.accent ?? INK_LO,
        incumbent: c.incumbentChallenge === "I",
        money: c.contributions != null ? `${formatCurrency(c.contributions)} raised` : "",
      };
    });
  const more = pool.length - rows.length;
  return {
    eyebrow: `Elections · ${race.cycleYear}${race.isSpecial ? " · Special election" : ""}`,
    title: seat,
    lean: race.usualVote?.label ? `Usually votes ${race.usualVote.label}` : "",
    rows,
    more: more > 0 ? `and ${more} more` : "",
    // Who is listed comes from the state until it names no one; before
    // that the race lists the FEC's filers (candidateSource "filers").
    footer:
      race.candidateSource === "filers"
        ? "Candidates and money: FEC filings"
        : rows.some((r) => r.money)
          ? "Candidates: state sources · Money: FEC"
          : "Candidates: state sources",
  };
}

async function raceImage(race: OgRace) {
  const t = raceCardTexts(race);
  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <Header section={t.eyebrow} />
      <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
        <div
          style={{
            color: INK_HI,
            fontSize: t.title.length > 36 ? 44 : 52,
            fontFamily: OG_FAMILY.serif,
            fontWeight: 600,
            lineHeight: 1.15,
          }}
        >
          {t.title}
        </div>
        {t.lean && <div style={{ color: INK, fontSize: 26, marginTop: 10 }}>{t.lean}</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 28 }}>
          {t.rows.map((r) => (
            <div
              key={r.name}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 18,
                fontSize: 28,
                borderLeft: `6px solid ${r.accent}`,
                paddingLeft: 16,
              }}
            >
              <span style={{ color: INK_HI, fontWeight: 600 }}>{r.name}</span>
              <span style={{ color: r.accent, fontSize: 22, fontWeight: 600 }}>
                {r.party}
                {r.incumbent ? " · Incumbent" : ""}
              </span>
              {r.money && (
                <span style={{ marginLeft: "auto", color: INK_LO, fontSize: 22 }}>{r.money}</span>
              )}
            </div>
          ))}
          {t.more && <div style={{ color: INK_LO, fontSize: 20 }}>{t.more}</div>}
        </div>
      </div>
      <Footer label={t.footer} />
    </div>,
    { width: 1200, height: 630, headers: OG_HEADERS, fonts: await ogFonts() }
  );
}

async function agencyImage(slug: string) {
  const a = await tryFetch(() => fetchAgency(slug));
  if (!a) {
    return genericCard({
      section: "Agencies",
      title: "Federal agencies",
      description: "What each agency spends, the rules it proposes and when you can comment.",
      footerLabel: "Federal Register · USAspending.gov",
    });
  }
  const budget = agencyBudget(a);
  const tiles: Tile[] = [];
  if (budget)
    tiles.push({
      value: formatCurrency(budget.amount),
      label: budget.fiscalYear
        ? `Budgetary resources, FY${budget.fiscalYear}`
        : "Budgetary resources",
    });
  tiles.push({
    value: a.documents.total.toLocaleString("en-US"),
    label: "Federal Register documents",
  });
  tiles.push({
    value: String(a.documents.openCommentsCount),
    label: plural(a.documents.openCommentsCount, "Comment period open", "Comment periods open"),
  });
  return tileCard({
    section: a.parent ? `Agencies · Part of the ${a.parent.name}` : "Agencies",
    title: a.shortName && a.shortName !== a.name ? `${a.name} (${a.shortName})` : a.name,
    description: "Its budget, its rules and when you can comment.",
    tiles,
    footerLabel: budget ? "Federal Register · USAspending.gov" : "Federal Register",
  });
}

async function nominationImage(ref: string) {
  const n = await fetchJson<NominationDetail>(`/api/nominations/${ref}`, 600);
  if (!n?.citation) {
    return genericCard({
      section: "Nominations",
      title: "Presidential nominations",
      description: "Every nomination before the Senate, and how long each took.",
      footerLabel: "Congress.gov",
    });
  }
  const final = n.finalVote
    ? n.rollCalls.find(
        (rc) => rc.session === n.finalVote!.session && rc.number === n.finalVote!.number
      )
    : undefined;
  const tiles: Tile[] = [
    {
      value: STATUS_LABEL[n.status],
      label: n.statusDate ? calendarDate(n.statusDate) : `Received ${calendarDate(n.receivedDate)}`,
    },
  ];
  if (n.status === "confirmed" && n.daysToConfirm != null)
    tiles.push({ value: String(n.daysToConfirm), label: "Days from nomination" });
  if (final) tiles.push({ value: `${final.yeas} to ${final.nays}`, label: final.label });
  return tileCard({
    section: `Nominations · ${n.citation} · ${KIND_LABEL[n.kind]}`,
    title: nominationHeading(n),
    description: `Received ${calendarDate(n.receivedDate)}`,
    tiles,
    footerLabel: "Congress.gov",
  });
}

async function redistrictingImage(code: string) {
  const d = await fetchJson<StateRedistricting>(`/api/elections/redistricting/${code}`, 300);
  const name = STATE_NAMES[code] ?? code;
  if (!d?.summary) {
    return genericCard({
      section: "Elections · New maps",
      title: `${name}'s congressional map`,
      description: "What changed in each district, from the enacted map and Census blocks.",
      footerLabel: "Census blocks · Enacted maps",
    });
  }
  return tileCard({
    section: "Elections · New maps",
    title: `${d.stateName}'s new congressional map, ${d.cycle}`,
    description: "Where each district's residents lived before, and how each lean moved.",
    tiles: [
      { value: String(d.summary.seats), label: "U.S. House districts" },
      { value: String(d.summary.unchangedSeats), label: "Kept the same lines" },
      {
        value: `${d.summary.residentsMovedPct}%`,
        label: "Of residents in a district built mostly from another",
      },
    ],
    footerLabel: "Census blocks · Enacted maps",
  });
}

async function electionArchiveImage(cycle: number) {
  const r = await fetchJson<ArchiveRecord>(`/api/elections/archive/${cycle}`, 300);
  if (!r?.totals) {
    return genericCard({
      section: "Elections · Archive",
      title: `The ${cycle} general election`,
      description: "Each race's count as its state reported it, and the ballot it was on.",
      footerLabel: "State election offices",
    });
  }
  return tileCard({
    section: "Elections · Archive",
    title: `The ${cycle} general election`,
    description: "The count as each state reported it, and the ballot it was on. No race called.",
    tiles: [
      { value: String(r.totals.races), label: "Senate and House races" },
      r.totals.measures > 0
        ? { value: String(r.totals.measures), label: "Statewide ballot measures" }
        : { value: null, label: "No statewide measures archived" },
    ],
    footerLabel: "State election offices",
  });
}

const STOP_MARK: Record<string, string> = {
  done: C.accent,
  current: C.amber,
  failed: C.red,
};

/** Where the bill stands: its furthest stop that happened (done, current or
 *  failed), worded as the bill page's ladder words it. */
export function billStage(record: BillRecord): string {
  const stops = record.journey?.stops ?? [];
  const reached = stops.filter((s) => s.status === "current" || s.status === "failed");
  const stop = reached.at(-1) ?? stops.filter((s) => s.status === "done").at(-1);
  if (!stop) return "";
  // "Reported by the committee" says where it is; "Passed" needs "House floor".
  if (!stop.detail) return stop.label;
  // The ladder dates a bill still in committee in its detail ("In committee
  // since", BillJourney.tsx).
  const detail =
    stop.key === "committee" && stop.status === "current" && stop.date
      ? `${stop.detail} ${calendarDate(stop.date)}`
      : stop.detail;
  return detail.toLowerCase().includes(stop.label.toLowerCase())
    ? detail
    : `${stop.label}: ${detail}`;
}

async function billImage(id: string, congress: number) {
  const { record } = await fetchBillRecord(id, congress);
  if (!record) {
    return genericCard({
      section: "Congress · Bills",
      title: id.replace(".", ". "),
      description: "Where the bill stands, who sponsors it and every action, from Congress.gov.",
      footerLabel: "Congress.gov",
    });
  }
  const label = record.billLabel ?? record.billId;
  const named = record.shortTitle || record.title || "";
  const stage = billStage(record);
  const stops = record.journey?.stops ?? [];
  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: OG_FAMILY.sans,
        borderTop: TOP_BAND,
      }}
    >
      <Header section={`${label} · ${ordinalNumber(record.congress)} Congress`} />
      <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
        <div
          style={{
            color: INK_HI,
            fontSize: named.length > 110 ? 36 : named.length > 60 ? 44 : 54,
            fontFamily: OG_FAMILY.serif,
            fontWeight: 600,
            lineHeight: 1.15,
          }}
        >
          {named.length > 180 ? `${named.slice(0, 177)}…` : named || label}
        </div>
        {stage && (
          <div
            style={{ color: INK, fontSize: 28, marginTop: 20 }}
          >{`Where it stands: ${stage}`}</div>
        )}
        {stops.length > 0 && (
          <div style={{ display: "flex", gap: 8, marginTop: 28 }}>
            {stops.map((s) => (
              <div
                key={s.key}
                style={{ display: "flex", flexDirection: "column", flex: 1, gap: 8 }}
              >
                <div
                  style={{
                    display: "flex",
                    height: 12,
                    background: STOP_MARK[s.status] ?? C.hairline,
                  }}
                />
                <span
                  style={{
                    color: s.status === "ahead" || s.status === "skipped" ? INK_MIN : INK_HI,
                    fontSize: 17,
                    lineHeight: 1.2,
                  }}
                >
                  {s.label}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <Footer label="Congress.gov" />
    </div>,
    { width: 1200, height: 630, headers: OG_HEADERS, fonts: await ogFonts() }
  );
}

// `race=<cycle>-<office>-<ST>[-<district>]`, as backend race ids are
// written ("2026-SEN-GA", "2026-HOUSE-TX-12").
export function parseRaceId(raw: string | null): string | null {
  return raw && /^\d{4}-[A-Z]{3,5}-[A-Z]{2}(-[A-Z0-9]{1,8}){0,2}$/.test(raw) ? raw : null;
}

// `agency=<slug>`: a Federal Register agency slug.
export function parseAgencySlug(raw: string | null): string | null {
  return raw && /^[a-z0-9-]{1,120}$/.test(raw) ? raw : null;
}

// `nomination=<congress>/<citation>` ("119/PN123", "119/PN123-1").
export function parseNominationRef(raw: string | null): string | null {
  const [congress, citation, ...rest] = (raw ?? "").split("/");
  return !rest.length && /^[1-9]\d{1,2}$/.test(congress) && CITATION.test(citation ?? "")
    ? `${congress}/${citation}`
    : null;
}

// `billPage=<congress>/<BILLID>`, the bill page's own card.
export function parseBillPageRef(raw: string | null): { congress: number; id: string } | null {
  const m = raw?.match(/^(\d{2,3})\/([A-Za-z]{1,8}\.\d{1,5})$/);
  return m ? { congress: Number(m[1]), id: m[2] } : null;
}

// `vote=<chamber>/<congress>/<session>/<number>` -> "senate/119/2/123".
export function parseVoteRef(raw: string | null): string | null {
  return raw && /^(senate|house)\/\d{1,3}\/\d\/\d{1,5}$/.test(raw) ? raw : null;
}

// `bill=<congress>/<BILLID>`: a bill type's letters, one dot, its number
// ("119/S.4668", "119/HJRES.5").
export function parseBillRef(raw: string | null): string | null {
  return raw && /^\d{1,3}\/[A-Za-z]{1,8}\.\d{1,5}$/.test(raw) ? raw : null;
}

// A congressional-record event's or an Explore document's numeric id.
export function parseRecordId(raw: string | null): string | null {
  return raw && /^\d{1,12}$/.test(raw) ? raw : null;
}

// The backend card key for the record a request names, or null.
export function recordCardKey(params: URLSearchParams): string | null {
  const vote = parseVoteRef(params.get("vote"));
  if (vote) return `vote:${vote}`;
  const bill = parseBillRef(params.get("bill"));
  if (bill) return `bill:${bill}`;
  const event = parseRecordId(params.get("congressEvent"));
  if (event) return `event:${event}`;
  const document = parseRecordId(params.get("explore"));
  return document ? `document:${document}` : null;
}

// A day's or week's date, checked before it reaches the backend URL.
export function parseCongressDate(raw: string | null): string | null {
  return raw && ISO_DATE.test(raw) ? raw : null;
}

// Every real link on the site points at an issue's public id (backend
// issue_ids.py: "i" + 8 lowercase hex chars, e.g. "i9e3779b1") via
// issue.publicId — page.tsx's generateMetadata builds this OG URL from
// that same route param. A digits-only check here rejected every real
// request and silently fell through to the generic fallback card for
// every issue, photo or not; a bare numeric id is only ever a legacy
// pre-public-id share link. Backend's own get_action_issue accepts both
// formats (see its docstring) — mirrored here, not invented.
export function parseIssueId(rawIssueId: string | null): string | null {
  return rawIssueId && /^(i[0-9a-f]{8}|\d+)$/i.test(rawIssueId) ? rawIssueId : null;
}

// Checked against the real USPS-code list (lib/stateCodes.ts), not just
// the 2-letter shape — a shape-only check let a garbage code like "ZZ"
// through to render a fully-formed, plausible-looking "ZZ Ballot 2026"
// share card asserting ZZ is a real jurisdiction, rather than falling
// back to a generic one the way an unknown issue/politician id already
// does. Case-insensitive on input (the route always uppercases before
// using it), since a hand-typed or copy-pasted URL param shouldn't fail
// over letter case alone.
export function parseStateCode(rawState: string | null): string | null {
  if (!rawState || !/^[a-z]{2}$/i.test(rawState)) return null;
  const code = rawState.toUpperCase();
  return STATE_CODES.includes(code) ? code : null;
}

// Politician ids are slugs, validated here rather than passed unvalidated
// into the outgoing fetch URL. Members' are "last-first" ("grassley-chuck"),
// presidents' "name-number" ("bush-41"), and justices' are Oyez identifiers
// ("ketanji_brown_jackson") — the underscore was missing from this pattern,
// so every justice's share card fell back to the generic image.
export function parsePoliticianId(rawId: string | null): string | null {
  return rawId && /^[a-z0-9_-]+$/.test(rawId) ? rawId : null;
}

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams;
  const politicianId = parsePoliticianId(req.nextUrl.searchParams.get("politician"));
  if (politicianId) {
    const profile = await fetchPolitician(politicianId);
    return politicianImage(profile);
  }

  const stateCode = parseStateCode(req.nextUrl.searchParams.get("state"));
  if (stateCode) {
    const ballot = await fetchStateBallot(stateCode);
    return electionImage(ballot, stateCode);
  }

  const recordKey = recordCardKey(req.nextUrl.searchParams);
  if (recordKey) {
    const card = await fetchRecordCard(recordKey, req.nextUrl.searchParams.get("title") !== "0");
    if (card) return recordCardImage(card);
    return genericCard({
      section: "Public record",
      title: "Civitas Research",
      description: "The official record, in its own words.",
      footerLabel: "Public federal records",
    });
  }

  const congressDay = parseCongressDate(req.nextUrl.searchParams.get("congress"));
  if (congressDay) return congressDayImage(congressDay);

  const congressWeek = parseCongressDate(req.nextUrl.searchParams.get("congressWeek"));
  if (congressWeek) return congressWeekImage(congressWeek);

  const congressMonth = q.get("congressMonth");
  if (congressMonth && ISO_MONTH.test(congressMonth)) return congressMonthImage(congressMonth);

  const section = SECTION_CARDS[q.get("page") ?? ""];
  if (section) return sectionImage(section);

  const raceId = parseRaceId(q.get("race"));
  if (raceId) {
    const race = await fetchJson<OgRace>(`/api/elections/races/${raceId}`, 120);
    if (race?.candidates) return raceImage(race);
    const code = parseStateCode(raceId.split("-")[2]);
    if (code) return electionImage(await fetchStateBallot(code), code);
  }

  const agency = parseAgencySlug(q.get("agency"));
  if (agency) return agencyImage(agency);

  const nomination = parseNominationRef(q.get("nomination"));
  if (nomination) return nominationImage(nomination);

  const redrawn = parseStateCode(q.get("redistricting"));
  if (redrawn) return redistrictingImage(redrawn);

  const cycle = q.get("electionArchive");
  if (cycle && /^\d{4}$/.test(cycle)) return electionArchiveImage(Number(cycle));

  const bill = parseBillPageRef(q.get("billPage"));
  if (bill) return billImage(bill.id, bill.congress);

  const issueId = parseIssueId(req.nextUrl.searchParams.get("issue"));
  const issue = issueId ? await fetchIssue(issueId) : null;
  return issueImage(issue);
}
