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
import { formatUtcDate, isNewFact, issueDateLabel, issueRef, safeHref } from "@/lib/formatting";
import {
  countIsOfficial,
  developingSource,
  factsAreTheCount,
  factsHeading,
} from "@/lib/developing";
import { formatEasternTime } from "@/lib/results";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { monitorHref } from "@/lib/routes";
import type { ActionIssue, ActionItem, RelatedBill } from "@/types/action";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";

/** Hairline section header: the one used by RecordIndex and /elections. */
export const SECTION_HEADING =
  "flex items-baseline justify-between gap-3 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min";

/** An underlined sentence-case link, the records pages' only link treatment. */
export const TEXT_LINK =
  "font-display text-sm text-ink-hi underline decoration-ink-min/60 underline-offset-4 transition-colors hover:decoration-ink-hi";

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
}: {
  issue: ActionIssue;
  size?: "full" | "thumbnail";
}) {
  if (!issue.imageUrl) return null;
  const alt = issue.imageAlt || issue.title;

  if (size === "thumbnail") {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- external, varied source-article hosts; not worth per-host next/image remotePatterns
      <img
        src={issue.imageUrl}
        alt={alt}
        // A shared image of the card leaves the photo out: it's on the
        // publisher's host, which a capture never requests (lib/shareImage.ts).
        {...{ [SHARE_EXCLUDE_ATTR]: "" }}
        className="h-16 w-16 shrink-0 border border-white/[0.07] object-cover sm:h-20 sm:w-20"
      />
    );
  }

  return (
    // Left out of shared images, as above.
    <figure className="mb-5" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
      {/* eslint-disable-next-line @next/next/no-img-element -- external, varied source-article hosts; not worth per-host next/image remotePatterns */}
      <img
        src={issue.imageUrl}
        alt={alt}
        className="max-h-96 w-full border border-white/[0.07] object-cover"
      />
      {(issue.imageAlt || issue.imageCredit) && (
        <figcaption className="mt-1.5 font-mono text-xs tracking-wide text-ink-min">
          {issue.imageAlt}
          {issue.imageAlt && issue.imageCredit && " — "}
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
      {issue.status === "developing" && <span className="text-signal-amber">Developing</span>}
      {issue.isTrending && <span className="text-signal-cyan">Trending</span>}
    </Tag>
  );
}

/** One-line disclosure paired with the "Developing" flag — explains what it
 *  means, and names what the story was drafted from, rather than leaving
 *  readers to guess. */
export function DevelopingDisclosure({
  sourceType,
  countOfficial = false,
}: {
  sourceType?: string | null;
  /** countIsOfficial(issue) — an official count must not read "not final". */
  countOfficial?: boolean;
}) {
  const source = developingSource(sourceType, { countOfficial });
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

function humanizeSlug(slug: string): string {
  const words = slug.replace(/-/g, " ");
  const clipped = words.length > 48 ? `${words.slice(0, 48)}…` : words;
  return clipped.charAt(0).toUpperCase() + clipped.slice(1);
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
  const slugs = issue.relatedMonitorSlugs ?? [];
  if (areas.length === 0 && slugs.length === 0) return null;
  return (
    <div
      className={`flex flex-wrap items-baseline gap-x-4 gap-y-1.5 font-mono text-xs tracking-[0.08em] text-ink-lo ${className}`}
    >
      {areas.map((area) => (
        <span key={area}>{area}</span>
      ))}
      {slugs.map((slug) => (
        <span key={slug}>
          Tracked in{" "}
          {onMonitor ? (
            <button onClick={() => onMonitor(slug)} className={TEXT_LINK}>
              {humanizeSlug(slug)} →
            </button>
          ) : (
            <Link href={monitorHref(slug)} className={TEXT_LINK}>
              {humanizeSlug(slug)} →
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

interface ActionRow {
  key: string;
  verb: string;
  what: React.ReactNode;
  detail?: React.ReactNode;
  href: string;
  internal: boolean;
  label: string;
}

/**
 * Every row a reader can act on, in one list: contact the members the coverage
 * names, follow the legislation, read or comment on the federal documents.
 *
 * Bills and track-legislation actions describe the same legislation (an action
 * is generated from a related bill), so a track action whose URL is already a
 * related bill's is not listed twice.
 *
 * With no member named in the coverage there is no one specific to contact, so
 * the row points at the directory, where a reader picks their own state. The
 * reader is never asked, and nothing remembers, where they live (AGENTS.md §8).
 *
 * `today` is passed in rather than read from the clock so a server-rendered page
 * and its hydration agree on whether a comment period is still open.
 */
function actionRows(issue: ActionIssue, today: string): ActionRow[] {
  const rows: ActionRow[] = [];

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
            aria-label={`${m.name}'s scorecard, overall score ${Math.round(m.overallScore)}`}
          >
            · {Math.round(m.overallScore)}
          </Link>
        </>
      ),
      detail: m.matchReason ? `Named in the coverage · ${m.matchReason}` : "Named in the coverage",
      href: url ?? `/politicians/${m.id}`,
      internal: !url,
      label: url ? "Contact ↗" : "Scorecard →",
    });
  }
  // No member to contact: point at the directory — except on a count issue
  // (a seat changing party on election night), where there is no coverage
  // to have named anyone and the useful action is the count itself.
  if (members.length === 0 && followResultsActions(issue).length === 0) {
    rows.push({
      key: "directory",
      verb: "Contact",
      what: "Find your senators and representative",
      detail: "No member was named in the coverage",
      href: "/politicians",
      internal: true,
      label: "Directory →",
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
      href,
      internal,
      label: internal ? "Bill page →" : "Congress.gov ↗",
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
      });
    });

  for (const doc of issue.relatedExploreDocs ?? []) {
    const commentOpen = !!(doc.commentUrl && doc.commentsCloseOn && doc.commentsCloseOn >= today);
    const kind = doc.docType.replace(/_/g, " ");
    rows.push({
      key: `doc-${doc.id}`,
      verb: commentOpen ? "Comment" : "Read",
      what: doc.title,
      detail: commentOpen
        ? `${kind} · comments close ${formatUtcDate(doc.commentsCloseOn!, { month: "short", day: "numeric" })}`
        : `${kind} · ${doc.date}`,
      href: commentOpen ? `/explore/${doc.id}#comment` : `/explore/${doc.id}`,
      internal: true,
      label: commentOpen ? "Comment →" : "Document →",
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
      <ul>
        {rows.map((row) => {
          const cls = `${TEXT_LINK} whitespace-nowrap`;
          return (
            <li
              key={row.key}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1 border-b border-white/[0.07] py-3 sm:grid-cols-[5.5rem_minmax(0,1fr)_auto]"
            >
              <span className="col-span-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-min sm:col-span-1">
                {row.verb}
              </span>
              <span className="min-w-0 font-display text-[15px] leading-snug text-ink-hi">
                {row.what}
                {row.detail && (
                  <span className="mt-0.5 block text-[13px] text-ink-lo">{row.detail}</span>
                )}
              </span>
              {row.internal ? (
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
            <p className="font-display text-[15px] leading-relaxed text-ink">
              {fact}
              {issue.factSources?.[i] && (
                <span className="ml-2 whitespace-nowrap font-mono text-xs text-ink-min">
                  {issue.factSources[i]}
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
 *  the figures — and whether the state calls it official, inside the facts
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
          <span key={name} className="flex items-baseline gap-2">
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
