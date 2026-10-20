/**
 * The parts an Action Center issue is built from, shared by the Action
 * Center's cards and the standalone /issue/{id} page.
 *
 * Shared deliberately. The full-story page is a *cold entry point* (Bluesky
 * posts link straight to it), so a visitor who lands there must get the same
 * links a visitor who started at the Action Center gets, and the Action
 * Center's top issue and its expanded secondary issues render one body, not a
 * full-size copy and a hand-compacted one that drift apart.
 *
 * Set in the records style (see PageMasthead, RecordIndex): small mono caps
 * over a hairline for a section, hairline rows for items, the display face for
 * anything a person reads, underlined sentence-case links. No tinted boxes and
 * no per-block accent colour; colour is left for what is true about the data
 * (a party, a developing draft, a new fact).
 *
 * No hooks and no browser APIs, so the server-rendered issue page can render
 * these directly.
 */

import Link from "next/link";
import {
  describeDaysLeftOn,
  formatUtcDate,
  isNewFact,
  issueDateLabel,
  issueRef,
  policyAreaLabel,
  safeHref,
} from "@/lib/formatting";
import {
  countIsOfficial,
  developingSource,
  factsAreTheCount,
  factsHeading,
} from "@/lib/developing";
import { formatEasternTime } from "@/lib/results";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { monitorHref } from "@/lib/routes";
import type { ActionIssue, ActionItem, RelatedBill, RelatedExploreDoc } from "@/types/action";
import type { ActionTapKind } from "@/lib/api";
import Photo from "@/components/Photo";
import ContactYourMembers from "@/components/action/ContactYourMembers";
import TrackedLink from "@/components/action/TrackedLink";
import OnTheBallot from "@/components/action/OnTheBallot";
import AgencyLink from "@/components/agencies/AgencyLink";
import { exploreHref } from "@/lib/exploreHref";

/** Hairline section header: the one used by RecordIndex and /elections. */
export const SECTION_HEADING =
  "flex items-baseline justify-between gap-3 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min";

/** A standalone link's target on a phone: 44px tall (mobile spec P10), its
 *  own height from 640px up. Not for a link inside a sentence. */
export const TAP_TARGET = "inline-flex min-h-11 items-center sm:min-h-0";

/** An underlined sentence-case link, the records pages' only link treatment. */
export const TEXT_LINK =
  "font-sans text-sm text-ink-hi underline decoration-ink-min/60 underline-offset-4 transition-colors hover:decoration-ink-hi";

/** An issue's rights-cleared source photo, in the two sizes it renders at.
 *
 *  Accessibility: every image gets real alt text, always — no alt="".
 *  WCAG technically permits alt="" when an equivalent description is
 *  already in visible adjacent text, and an earlier version of this
 *  component relied on that: empty alt whenever the figcaption (or, for
 *  the thumbnail, the adjacent title/summary) already said the same
 *  thing. That conditional logic already shipped one real bug (a
 *  credit-only figcaption — attribution, not a description — still
 *  suppressed alt, silently dropping the image for screen-reader users
 *  entirely). Always-populate is the simpler rule and structurally
 *  cannot have that failure mode: `imageAlt` (the source's own caption)
 *  when present, else the issue's own title — never empty.
 *
 *  The source caption is also still shown as a VISIBLE figcaption
 *  alongside the credit on the full size, for sighted readers — that's
 *  a deliberate, accepted duplication with the alt text now, not an
 *  oversight. */
export function IssueImage({
  issue,
  size = "full",
  priority = false,
}: {
  issue: ActionIssue;
  size?: "full" | "thumbnail";
  /** The page's lead picture (its LCP element): fetched first. */
  priority?: boolean;
}) {
  if (!issue.imageUrl) return null;
  const alt = issue.imageAlt || issue.title;

  if (size === "thumbnail") {
    return (
      <Photo
        src={issue.imageUrl}
        alt={alt}
        sizes="(min-width: 640px) 80px, 64px"
        className="h-16 w-16 shrink-0 border border-white/[0.07] object-cover sm:h-20 sm:w-20"
      />
    );
  }

  return (
    <figure className="mb-5">
      <Photo
        src={issue.imageUrl}
        alt={alt}
        // The column's width: the full screen on a phone, at most ~830px
        // (the Action Center's lead card, the issue page's column).
        sizes="(min-width: 1024px) 832px, 100vw"
        priority={priority}
        className="max-h-96 w-full border border-white/[0.07] object-cover"
      />
      {(issue.imageAlt || issue.imageCredit) && (
        <figcaption className="mt-1.5 font-mono text-xs tracking-wide text-ink-min">
          {issue.imageAlt}
          {issue.imageAlt && issue.imageCredit && " · "}
          {issue.imageCredit && `Photo: ${issue.imageCredit}`}
        </figcaption>
      )}
    </figure>
  );
}

/**
 * The docket line above an issue's title: date, reference, and the two
 * computed flags. "Developing" is amber (lower confidence: a primary-source
 * draft not yet corroborated by press coverage — backend early_signal.py);
 * "Trending" is cyan, and only ever set when the backend's traction bar
 * (app/trending.py) was cleared. Words, not boxed badges.
 */
export function IssueMeta({
  issue,
  lead,
  withRef = true,
  as: Tag = "p",
}: {
  issue: ActionIssue;
  /** A label ahead of the date, e.g. "Top issue". */
  lead?: string;
  withRef?: boolean;
  /** "span" inside phrasing content, such as a disclosure button. */
  as?: "p" | "span";
}) {
  return (
    <Tag className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 font-mono text-xs tracking-[0.08em] text-ink-min">
      {lead && (
        <span className="border border-white/15 px-2 py-0.5 uppercase tracking-[0.14em] text-ink-hi">
          {lead}
        </span>
      )}
      <span>{issueDateLabel(issue)}</span>
      {withRef && (
        <>
          <span aria-hidden="true">·</span>
          <span>{issueRef(issue.publicId)}</span>
        </>
      )}
      {issue.status === "developing" &&
        (issue.isCurrent === false ? (
          <span className="text-ink-lo">
            {RETIRED_FLAG[issue.retiredReason ?? ""] ?? "Retired"}
          </span>
        ) : (
          <span className="text-signal-amber">Developing</span>
        ))}
      {issue.isTrending && <span className="text-signal-cyan">Trending</span>}
    </Tag>
  );
}

/** A retired draft's flag, by why it left (backend models.RetiredReason). */
const RETIRED_FLAG: Record<string, string> = {
  unconfirmed: "Not confirmed",
  covered: "Superseded",
};

/** One-line disclosure paired with the "Developing" flag — explains what it
 *  means, and names what the story was drafted from, rather than leaving
 *  readers to guess. A retired draft says why it left instead: "has not yet
 *  confirmed" on a month-old draft reads as a story still developing, and a
 *  draft that gave way to reporting must not read as never confirmed. */
export function DevelopingDisclosure({
  sourceType,
  countOfficial = false,
  retired = false,
  retiredReason,
  coveredBy,
}: {
  sourceType?: string | null;
  /** countIsOfficial(issue) — an official count must not read "not final". */
  countOfficial?: boolean;
  /** The draft is off the Action Center (isCurrent false). */
  retired?: boolean;
  /** Why: "unconfirmed" (no coverage in time) or "covered" (a reported
   *  issue covers it); null for one retired before reasons were kept. */
  retiredReason?: string | null;
  /** The reported issue on the same vote, when the record knows it. */
  coveredBy?: string | null;
}) {
  const source = developingSource(sourceType, { countOfficial });
  if (retired) {
    const subject = sourceType === "federal_register_significant_rule" ? "rule" : "vote";
    return (
      <p className="mb-4 font-mono text-xs leading-relaxed text-ink-min">
        {retiredReason === "covered"
          ? `Superseded by broader coverage. Based on ${source}; taken off the Action Center once a reported issue covered the same ${subject}.`
          : retiredReason === "unconfirmed"
            ? `Not confirmed. Based on ${source}; taken off the Action Center before broader news coverage confirmed it.`
            : `Based on ${source}; taken off the Action Center.`}
        {coveredBy && (
          <>
            {" "}
            <Link href={`/issue/${coveredBy}`} className="text-ink underline underline-offset-2">
              The reported issue on the same {subject} →
            </Link>
          </>
        )}
      </p>
    );
  }
  return (
    <p className="mb-4 font-mono text-xs leading-relaxed text-ink-min">
      Based on {source}; broader news coverage has not yet confirmed this story.
    </p>
  );
}

/** Marks a fact added since this issue's last genuine content change (see
 *  backend/app/fact_diff.py). Phosphor: a computed fact about the data. */
export function NewFactTag() {
  return <span className="ml-2 font-mono text-xs text-phos-mid">new</span>;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Policy areas and the national monitors tracking this issue, on one line.
 * Each monitor opens in place on the Action Center's monitors tab.
 */
export function IssueTags({
  issue,
  className = "mb-6",
  onMonitor,
}: {
  issue: ActionIssue;
  className?: string;
  /**
   * On /action itself, open the monitor in place. A <Link> to the same route
   * would be a soft navigation the page never re-reads (it latches ?monitor=
   * at mount), so the tab would switch and nothing would open.
   */
  onMonitor?: (slug: string) => void;
}) {
  const areas = issue.policyAreas ?? [];
  // By stored title, never the slug printed back as a name: that lost the
  // apostrophes and capitals ("talks trumps russian diesel deal").
  const monitors = issue.relatedMonitors ?? [];
  if (areas.length === 0 && monitors.length === 0) return null;
  return (
    <div
      className={`flex flex-wrap items-baseline gap-x-4 gap-y-1.5 font-mono text-xs tracking-[0.08em] text-ink-lo ${className}`}
    >
      {areas.map((area) => (
        <span key={area}>{policyAreaLabel(area)}</span>
      ))}
      {monitors.map(({ slug, title }) => (
        <span key={slug}>
          Tracked in{" "}
          {onMonitor ? (
            <button onClick={() => onMonitor(slug)} className={TEXT_LINK}>
              {title} →
            </button>
          ) : (
            <Link href={monitorHref(slug)} className={TEXT_LINK}>
              {title} →
            </Link>
          )}
        </span>
      ))}
    </div>
  );
}

/** Prefer our internal bill page over congress.gov when the API says we host it. */
export function billLink(bill: RelatedBill): { href: string; internal: boolean } {
  if (bill.internalUrl) return { href: bill.internalUrl, internal: true };
  return { href: safeHref(bill.url) || "#", internal: false };
}

/**
 * Track-legislation actions carry the same congress.gov URL as the related
 * bill they came from — reuse that bill's internal link when it has one.
 */
export function trackActionLink(
  issue: ActionIssue,
  action: ActionItem
): { href: string; internal: boolean } {
  const match = issue.relatedBills?.find((b) => b.url === action.url && b.internalUrl);
  if (match?.internalUrl) return { href: match.internalUrl, internal: true };
  return { href: safeHref(action.url) || "#", internal: false };
}

/** Older stored actions say "Track X on Congress.gov" — drop the suffix when we link internally. */
export function trackActionText(action: ActionItem, internal: boolean): string {
  return internal ? action.text.replace(/ on Congress\.gov$/i, "") : action.text;
}

export function trackableActions(issue: ActionIssue): ActionItem[] {
  return (issue.actions ?? []).filter((a) => a.type === "track_legislation" && a.url);
}

/** A seat-flip issue's link to the live count (backend
 * live_results/signals.py). Same-site paths only: the backend writes
 * these, and anything else is not one of them. */
export function followResultsActions(issue: ActionIssue): ActionItem[] {
  return (issue.actions ?? []).filter(
    (a) =>
      a.type === "follow_results" && typeof a.url === "string" && a.url.startsWith("/elections/")
  );
}

/** A document row's detail line, led by its agency (a link to the agency's
 *  page when the document names it). */
function withAgency(doc: RelatedExploreDoc | undefined, rest: string): React.ReactNode {
  if (!doc?.agencyName) return rest;
  return (
    <>
      <AgencyLink name={doc.agencyName} slug={doc.agencySlug} />
      {rest ? ` · ${rest}` : ""}
    </>
  );
}

interface ActionRow {
  key: string;
  verb: string;
  what: React.ReactNode;
  detail?: React.ReactNode;
  /** No link: a row that states something (who decides) rather than offers it. */
  href?: string;
  internal?: boolean;
  label?: string;
  /** Counted as this kind of tap when followed (backend ActionTap). */
  tap?: ActionTapKind;
}

/**
 * Every row a reader can act on, in one list: contact the members the coverage
 * names or the committee that handles the subject, follow the legislation,
 * comment on a rule while its period is open, read the federal documents.
 *
 * Bills and track-legislation actions describe the same legislation (an action
 * is generated from a related bill), so a track action whose URL is already a
 * related bill's is not listed twice.
 *
 * First, who decides next, where the record says (a linked bill's stage, an
 * open comment period's agency). Then the reader's own members, on the page:
 * a state picked from a list gives each office's phone and contact form, how
 * each voted on the issue's roll calls, and a call script (ContactYourMembers).
 * The reader is never asked, and nothing remembers, where they live
 * (AGENTS.md §8).
 *
 * `today` is passed in rather than read from the clock so a server-rendered page
 * and its hydration agree on whether a comment period is still open.
 */
function actionRows(issue: ActionIssue, today: string): ActionRow[] {
  const rows: ActionRow[] = [];

  // A roll-call issue's vote on its page here, every member's position by
  // party (backend api/action.py). Same-site paths only, as the backend
  // writes them; the chamber's own record stays among the sources.
  for (const action of issue.actions ?? []) {
    if (action.type !== "roll_call" || !action.url?.startsWith("/congress/votes/")) continue;
    rows.push({
      key: "roll-call",
      verb: "See",
      what: action.text,
      detail: "Every member's vote, by party, from the chamber's record",
      href: action.url,
      internal: true,
      label: "Roll call →",
    });
  }

  (issue.deciders ?? []).forEach((d, i) => {
    rows.push({ key: `decides-${i}`, verb: "Decides", what: d.body, detail: capitalize(d.basis) });
  });

  // A seat-flip issue's first row: the count itself, on its state's page.
  followResultsActions(issue).forEach((action, i) => {
    rows.push({
      key: `results-${i}`,
      verb: "Follow",
      what: action.text,
      detail: "The state's own count, as it comes in",
      href: action.url!,
      internal: true,
      label: "Live count →",
    });
  });

  const members = issue.relatedSenators ?? [];
  for (const m of members) {
    const url = safeHref(m.contactFormUrl || m.websiteUrl || null);
    const title = m.chamber === "house" ? "Rep." : "Sen.";
    const reason = m.matchReason ? capitalize(m.matchReason) : "Named in the coverage";
    rows.push({
      key: `member-${m.id}`,
      verb: "Contact",
      what: (
        <>
          {title} {m.name}{" "}
          <span className={`font-mono text-xs ${PARTY_COLORS[m.party] ?? "text-ink-lo"}`}>
            {m.party}-{m.state}
          </span>{" "}
          <Link
            href={`/politicians/${m.id}`}
            className="font-mono text-xs text-phos-mid hover:underline"
          >
            · record
          </Link>
        </>
      ),
      // The reason is the whole sentence ("named in coverage", "referenced
      // in coverage"); prefixing it repeated the first and contradicted the
      // second.
      detail: m.officePhone ? (
        <>
          {reason} ·{" "}
          <TrackedLink
            issuePublicId={issue.publicId}
            kind="call"
            href={`tel:${m.officePhone.replace(/[^0-9+]/g, "")}`}
            internal={false}
            className="underline decoration-ink-min/60 underline-offset-4"
            ariaLabel={`Call ${title} ${m.name}'s office, ${m.officePhone}`}
          >
            {m.officePhone}
          </TrackedLink>
        </>
      ) : (
        reason
      ),
      href: url ?? `/politicians/${m.id}`,
      internal: !url,
      label: url ? "Contact ↗" : "Record →",
      tap: url ? "write" : undefined,
    });
  }
  // The reader's own members, always — except on a count issue (a seat
  // changing party on election night), where there is no coverage to have
  // named anyone and the useful action is the count itself. A named
  // member's office takes messages from its own constituents (a House
  // contact form refuses an address outside the district), so when the
  // only contact row was a member from another state, a reader anywhere
  // else had nothing on the page they could complete.
  if (followResultsActions(issue).length === 0) {
    rows.push({
      key: "your-members",
      verb: "Contact",
      what: <ContactYourMembers issue={issue} afterNamed={members.length > 0} />,
    });
  }

  // The committee with jurisdiction, one per chamber at most (backend
  // app/committee_routing.py), and the two members who lead it. Worded as a
  // likely match, not a fact: held out, about four in five named committees
  // were right, and the rest are mostly stories no committee clearly handles.
  for (const action of issue.actions ?? []) {
    const c = action.type === "contact_committee" ? action.committee : null;
    const href = c ? safeHref(c.url ?? action.url ?? null) : null;
    if (!c || !href) continue;
    const title = c.chamber === "house" ? "Rep." : "Sen.";
    const leaders = [
      ["Chair", c.chair],
      ["Ranking member", c.rankingMember],
    ] as const;
    rows.push({
      key: `committee-${c.code}`,
      verb: "Contact",
      what: c.name,
      detail: (
        <>
          Likely the committee for this, matched by its jurisdiction
          {leaders.map(([role, m]) =>
            m ? (
              <span key={role}>
                {" · "}
                {role}{" "}
                <Link href={`/politicians/${m.id}`} className="underline underline-offset-2">
                  {title} {m.name}
                </Link>{" "}
                <span className={`font-mono text-xs ${PARTY_COLORS[m.party] ?? "text-ink-lo"}`}>
                  {m.party}-{m.state}
                </span>
              </span>
            ) : null
          )}
        </>
      ),
      href,
      internal: false,
      label: "Committee ↗",
      tap: "committee",
    });
  }

  const bills = issue.relatedBills ?? [];
  const billUrls = new Set(bills.map((b) => b.url));
  for (const bill of bills) {
    const { href, internal } = billLink(bill);
    rows.push({
      key: `bill-${bill.id}`,
      verb: "Follow",
      what: (
        <>
          <span className="font-mono text-xs text-ink-lo">{bill.id}</span> · {bill.name}
        </>
      ),
      detail: bill.stageName
        ? [
            bill.stageName,
            bill.latestAction &&
              `Latest action${bill.latestActionDate ? ` ${formatUtcDate(bill.latestActionDate, { month: "short", day: "numeric" })}` : ""}: ${bill.latestAction}`,
          ]
            .filter(Boolean)
            .join(" · ")
        : undefined,
      href,
      internal,
      label: internal ? "Bill page →" : "Congress.gov ↗",
      tap: "bill",
    });
  }
  trackableActions(issue)
    .filter((a) => !billUrls.has(a.url ?? ""))
    .forEach((action, i) => {
      const { href, internal } = trackActionLink(issue, action);
      rows.push({
        key: `track-${i}`,
        verb: "Follow",
        what: trackActionText(action, internal),
        href,
        internal,
        label: internal ? "Bill page →" : "Congress.gov ↗",
        tap: "bill",
      });
    });

  // "Comment by <date>": a rule about this story still open for comment.
  // The API drops the action once the period closes; `today` re-checks it
  // for a page cached across the deadline.
  const docs = issue.relatedExploreDocs ?? [];
  const commented = new Set<number>();
  for (const action of issue.actions ?? []) {
    if (action.type !== "comment_period" || !action.docId || !action.closesOn) continue;
    if (action.closesOn < today || commented.has(action.docId)) continue;
    commented.add(action.docId);
    const doc = docs.find((d) => d.id === action.docId);
    rows.push({
      key: `comment-${action.docId}`,
      verb: "Comment",
      what: doc?.title ?? action.text,
      detail: withAgency(
        doc,
        [doc?.docType.replace(/_/g, " "), action.text, describeDaysLeftOn(action.closesOn, today)]
          .filter(Boolean)
          .join(" · ")
      ),
      href: `${exploreHref({ id: action.docId, key: action.docKey })}#comment`,
      internal: true,
      label: "Comment →",
      tap: "comment",
    });
  }

  for (const doc of docs) {
    if (commented.has(doc.id)) continue;
    const commentOpen = !!(doc.commentUrl && doc.commentsCloseOn && doc.commentsCloseOn >= today);
    const kind = doc.docType.replace(/_/g, " ");
    rows.push({
      key: `doc-${doc.id}`,
      verb: commentOpen ? "Comment" : "Read",
      what: doc.title,
      detail: withAgency(
        doc,
        commentOpen
          ? `${kind} · comments close ${formatUtcDate(doc.commentsCloseOn!, { month: "short", day: "numeric" })} · ${describeDaysLeftOn(doc.commentsCloseOn!, today)}`
          : `${kind} · ${doc.date}`
      ),
      href: commentOpen ? `${exploreHref(doc)}#comment` : exploreHref(doc),
      internal: true,
      label: commentOpen ? "Comment →" : "Document →",
      tap: commentOpen ? "comment" : undefined,
    });
  }

  return rows;
}

export function WhatYouCanDo({
  issue,
  today,
  className = "mt-8",
  headingLevel = "h3",
}: {
  issue: ActionIssue;
  today: string;
  className?: string;
  headingLevel?: "h2" | "h3" | "h4";
}) {
  const rows = actionRows(issue, today);
  const Heading = headingLevel;
  return (
    <section className={className}>
      <Heading className={SECTION_HEADING}>What you can do</Heading>
      {/* An election story's next step is the voter's own ballot: first. */}
      <OnTheBallot ballot={issue.ballot} />
      <ul>
        {rows.map((row) => {
          const cls = `${TEXT_LINK} ${TAP_TARGET} whitespace-nowrap`;
          return (
            <li
              key={row.key}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1 border-b border-white/[0.07] py-3 sm:grid-cols-[5.5rem_minmax(0,1fr)_auto]"
            >
              <span className="col-span-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-min sm:col-span-1">
                {row.verb}
              </span>
              <div
                className={`min-w-0 font-sans text-[15px] leading-snug text-ink-hi ${row.href ? "" : "col-span-2 sm:col-span-2"}`}
              >
                {row.what}
                {row.detail && (
                  <span className="mt-0.5 block text-[13px] text-ink-lo">{row.detail}</span>
                )}
              </div>
              {!row.href ? null : row.tap ? (
                <TrackedLink
                  issuePublicId={issue.publicId}
                  kind={row.tap}
                  href={row.href}
                  internal={!!row.internal}
                  className={cls}
                >
                  {row.label}
                </TrackedLink>
              ) : row.internal ? (
                <Link href={row.href} className={cls}>
                  {row.label}
                </Link>
              ) : (
                <a href={row.href} target="_blank" rel="noopener noreferrer" className={cls}>
                  {row.label}
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** An outlet's name, linking the article a line was quoted from when the
 *  issue records it (issues before 2026-10 record only the name). */
function OutletLink({ name, url }: { name: string; url?: string | null }) {
  const href = safeHref(url);
  return href ? (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="underline decoration-white/20 underline-offset-4 hover:text-ink-hi"
    >
      {name}
    </a>
  ) : (
    <>{name}</>
  );
}

/** The outlet of the issue's summary, which is a line quoted from one
 *  article like the facts under it. */
export function SummarySource({ issue }: { issue: ActionIssue }) {
  if (!issue.summarySource) return null;
  return (
    <span className="ml-2 whitespace-nowrap font-mono text-xs text-ink-min">
      <OutletLink name={issue.summarySource} url={issue.summarySourceUrl} />
    </span>
  );
}

/** Lines quoted verbatim from the reporting, each with its outlet. */
export function Coverage({
  issue,
  className = "mt-8",
  headingLevel = "h3",
  heading,
}: {
  issue: ActionIssue;
  className?: string;
  headingLevel?: "h2" | "h3" | "h4";
  /** Replaces the plain heading, e.g. to add a share button beside it. */
  heading?: React.ReactNode;
}) {
  const facts = issue.facts ?? [];
  if (facts.length === 0) return null;
  const Heading = headingLevel;
  return (
    <section className={className}>
      {heading ?? <Heading className={SECTION_HEADING}>{factsHeading(issue)}</Heading>}
      <ol className="mt-1">
        {facts.map((fact, i) => (
          <li key={i} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-3 py-2">
            <span className="pt-0.5 font-mono text-xs text-ink-min" aria-hidden="true">
              {String(i + 1).padStart(2, "0")}
            </span>
            <p className="font-sans text-[15px] leading-relaxed text-ink">
              {fact}
              {issue.factSources?.[i] && (
                <span className="ml-2 whitespace-nowrap font-mono text-xs text-ink-min">
                  <OutletLink name={issue.factSources[i]} url={issue.factSourceUrls?.[i]} />
                </span>
              )}
              {isNewFact(issue.newFacts, fact) && <NewFactTag />}
            </p>
          </li>
        ))}
      </ol>
      <CountAsOf issue={issue} />
    </section>
  );
}

/** A count issue's facts are the count as Civitas read it: say when —
 *  the backend's own read time (countAsOf), the only time that describes
 *  the figures — and whether the state lists it as official, inside the facts
 *  section, so a shared image of it (card or page) says so too. With no
 *  countAsOf (an older backend) it names no time: the time a page happened
 *  to be rendered is not when the count was read (the issue is written at
 *  sync time and served from caches), and would overstate how fresh it is. */
function CountAsOf({ issue }: { issue: ActionIssue }) {
  if (!factsAreTheCount(issue)) return null;
  const when = issue.countAsOf ? formatEasternTime(issue.countAsOf) : "";
  return (
    <p className="mt-4 text-xs text-ink-min">
      {countIsOfficial(issue) ? (
        <span className="text-ink-hi">OFFICIAL COUNT</span>
      ) : (
        <span className="text-signal-amber">NOT FINAL</span>
      )}{" "}
      · {when ? `the count as of ${when}, when Civitas read it. ` : ""}
      The state&apos;s own results site has the current count.
    </p>
  );
}

/** "Sources · AP · NPR", each outlet linked to the article it came from. */
export function SourceList({ issue, className = "" }: { issue: ActionIssue; className?: string }) {
  const names = issue.sourceNames ?? [];
  if (names.length === 0) return null;
  return (
    <p
      className={`flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-xs tracking-[0.08em] text-ink-min ${className}`}
    >
      <span>Sources</span>
      {names.map((name, i) => {
        const href = safeHref(issue.sourceUrls?.[i]);
        return (
          <span key={`${i}-${name}`} className="flex items-baseline gap-2">
            <span aria-hidden="true">·</span>
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-ink-lo underline decoration-white/20 underline-offset-4 hover:text-ink-hi"
              >
                {name}
              </a>
            ) : (
              <span className="text-ink-lo">{name}</span>
            )}
          </span>
        );
      })}
    </p>
  );
}
