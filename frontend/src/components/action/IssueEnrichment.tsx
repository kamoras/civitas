"use client";

/**
 * The enrichment an Action Center issue carries beyond its own prose: the
 * monitors tracking it, the representatives involved, the legislation and
 * federal documents it points at, and where the reporting came from.
 *
 * Shared deliberately. These blocks render in two places — the Action Center's
 * top-issue card and the standalone /issue/{id} full-story page — and the full
 * story page is a *cold entry point* (Bluesky posts link straight to it), so a
 * visitor who lands there must get the same links a visitor who started at the
 * Action Center gets. Keeping one copy is what stops the two from drifting
 * apart again.
 *
 * The compact accordion variant inside the Action Center's secondary issues
 * renders the same data at a much tighter density and stays inline there on
 * purpose; only the full-size presentation lives here.
 */

import Link from "next/link";
import { safeHref } from "@/lib/formatting";
import { PARTY_COLORS, PARTY_BORDER } from "@/lib/partyStyles";
import { ACTION_CENTER_MONITORS_HREF } from "@/lib/routes";
import type { ActionIssue, ActionItem, RelatedBill } from "@/types/action";

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
 *  oversight.
 *
 *  Design: font-mono/tracked-uppercase/text-ink-min matches every other
 *  small label on this page (SOURCES:, KEY FACTS) — this is that same
 *  register, not a one-off caption style. */
export function IssueImage({ issue, size = "full" }: { issue: ActionIssue; size?: "full" | "thumbnail" }) {
  if (!issue.imageUrl) return null;
  const alt = issue.imageAlt || issue.title;

  if (size === "thumbnail") {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- external, varied source-article hosts; not worth per-host next/image remotePatterns
      <img
        src={issue.imageUrl}
        alt={alt}
        className="h-16 w-16 shrink-0 border border-white/[0.07] object-cover sm:h-20 sm:w-20"
      />
    );
  }

  return (
    <figure className="mb-6">
      {/* eslint-disable-next-line @next/next/no-img-element -- external, varied source-article hosts; not worth per-host next/image remotePatterns */}
      <img
        src={issue.imageUrl}
        alt={alt}
        className="max-h-96 w-full border border-white/[0.07] object-cover"
      />
      {(issue.imageAlt || issue.imageCredit) && (
        <figcaption className="mt-1.5 font-mono text-[10px] tracking-wide text-ink-min">
          {issue.imageAlt}
          {issue.imageAlt && issue.imageCredit && " — "}
          {issue.imageCredit && `PHOTO: ${issue.imageCredit}`}
        </figcaption>
      )}
    </figure>
  );
}

export function PolicyBadge({ area }: { area: string }) {
  return (
    <span className="text-xs px-2 py-0.5 border font-mono tracking-wide border-signal-amber/40 text-signal-amber bg-signal-amber/10">
      {area}
    </span>
  );
}

/** Phosphor, matching "TOP ISSUE": this is a computed figure (today's view
 *  count clearing app/trending.py's traction bar), not chrome — the rule
 *  established during the rebrand is phosphor lands on data, never
 *  decoration, and a badge that's silently wrong more often than right
 *  would be worse than no badge, so this only ever renders when the
 *  backend has already done that judgment. */
export function TrendingBadge() {
  return (
    <span className="border border-phos/40 px-2 py-0.5 font-mono text-xs tracking-[0.14em] text-phos-mid">
      ▲ TRENDING
    </span>
  );
}

/** Amber, matching MonitorStatus's non-"active" badge — this flags lower
 *  confidence (a primary-source-only draft, not yet corroborated by press
 *  coverage), the opposite of phosphor's "computed positive figure"
 *  meaning elsewhere on this page. See backend early_signal.py. */
export function DevelopingBadge() {
  return (
    <span className="border border-signal-amber/40 px-2 py-0.5 font-mono text-xs tracking-[0.14em] text-signal-amber">
      DEVELOPING
    </span>
  );
}

/** One-line disclosure paired with DevelopingBadge — explains what
 *  "developing" means rather than leaving readers to guess. */
export function DevelopingDisclosure() {
  return (
    <p className="mb-4 font-mono text-xs text-ink-min">
      Based on a primary-source vote record; broader news coverage has not
      yet confirmed this story.
    </p>
  );
}

/** Marks a fact added since this issue's last genuine content change (see
 *  backend/app/fact_diff.py). Phosphor, matching TrendingBadge/"TOP ISSUE" —
 *  this is a computed fact about the data, not decoration. `[NEW]` rather
 *  than a bare dot: the site already uses bracketed tags for exactly this
 *  register (the navbar's [BSKY]). */
export function NewFactTag() {
  return <span className="ml-2 align-middle font-mono text-[10px] text-phos-mid">[NEW]</span>;
}

export function SourceBadge({ name, url }: { name: string; url?: string }) {
  if (url) {
    return (
      <a
        href={safeHref(url) || "#"}
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs px-1.5 py-0.5 border border-white/[0.07] text-ink-lo hover:text-phos hover:border-white/15 transition-colors"
      >
        {name} <span aria-hidden="true">↗</span>
      </a>
    );
  }
  return (
    <span className="text-xs px-1.5 py-0.5 border border-white/[0.07] text-ink-lo">{name}</span>
  );
}

const MONITOR_CHIP_CLASS =
  "text-xs font-mono tracking-wide px-2 py-0.5 border border-signal-amber/40 text-signal-amber hover:text-signal-amber hover:border-signal-amber/40 transition-colors bg-signal-amber/10";

function monitorLabel(slug: string) {
  return `${slug.replace(/-/g, " ").slice(0, 40)}${slug.length > 40 ? "…" : ""}`;
}

/**
 * `onSelect` is for callers that already live on /action and can just switch
 * tabs. Without it — the full-story page — each chip links to the monitors tab
 * instead, so the enrichment stays reachable rather than becoming inert text.
 */
export function MonitorChips({
  slugs,
  onSelect,
  className = "mb-4",
}: {
  slugs?: string[];
  onSelect?: () => void;
  className?: string;
}) {
  if (!slugs || slugs.length === 0) return null;
  return (
    <div className={`flex items-center gap-2 flex-wrap ${className}`}>
      <span className="font-mono text-xs tracking-widest text-signal-amber">TRACKING</span>
      {slugs.map((slug) =>
        onSelect ? (
          <button key={slug} onClick={onSelect} className={MONITOR_CHIP_CLASS}>
            {monitorLabel(slug)}
          </button>
        ) : (
          <Link key={slug} href={ACTION_CENTER_MONITORS_HREF} className={MONITOR_CHIP_CLASS}>
            {monitorLabel(slug)}
          </Link>
        )
      )}
    </div>
  );
}

export function RepresentativeContacts({
  issue,
  userState,
}: {
  issue: ActionIssue;
  userState: string | null;
}) {
  const senators = issue.relatedSenators ?? [];

  if (senators.length === 0 && !userState) return null;

  return (
    <div className="mb-6">
      <h3 className="font-mono text-xs tracking-widest text-ink-lo mb-3 uppercase">
        {senators.length > 0 ? "Contact Representatives" : "Contact Your Representatives"}
      </h3>

      {senators.length > 0 ? (
        <div className="space-y-2">
          {senators.map((s) => {
            const url = s.contactFormUrl || s.websiteUrl || null;
            return (
              <div
                key={s.id}
                className={`flex items-center gap-3 px-3 py-2.5 border ${PARTY_BORDER[s.party]} bg-white/[0.03]`}
              >
                <span className={`font-mono text-xs shrink-0 ${PARTY_COLORS[s.party]}`}>
                  {s.party}-{s.state}
                </span>
                <span className="text-sm text-ink flex-1 min-w-0 truncate">{s.name}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <a
                    href={url || "https://www.senate.gov/senators/senators-contact.htm"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={
                      url
                        ? "text-xs font-mono tracking-widest text-signal-cyan border border-signal-cyan/40 hover:border-signal-cyan/40 hover:bg-signal-cyan/10 px-2 py-1 transition-colors"
                        : "text-xs font-mono tracking-widest text-ink-lo border border-white/15 hover:border-signal-cyan/40 px-2 py-1 transition-colors"
                    }
                  >
                    CONTACT ↗
                  </a>
                  <Link
                    href={`/politicians/${s.id}`}
                    className="text-xs font-mono tracking-wide text-ink-lo hover:text-phos transition-colors"
                  >
                    SCORE: {Math.round(s.overallScore)}
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      ) : userState ? (
        <a
          href={`/politicians?branch=senate&state=${userState}`}
          className="inline-flex items-center gap-2 text-xs font-mono tracking-widest text-signal-cyan border border-signal-cyan/40 hover:border-signal-cyan/40 hover:bg-signal-cyan/10 px-3 py-1.5 transition-colors"
        >
          VIEW {userState} SENATORS &amp; CONTACT INFO →
        </a>
      ) : (
        <a
          href="https://www.senate.gov/senators/senators-contact.htm"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-xs font-mono tracking-widest text-signal-cyan border border-signal-cyan/40 hover:border-signal-cyan/40 hover:bg-signal-cyan/10 px-3 py-1.5 transition-colors"
        >
          FIND YOUR SENATORS ↗
        </a>
      )}
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
  return issue.actions.filter((a) => a.type === "track_legislation" && a.url);
}

export function TrackLegislation({ issue }: { issue: ActionIssue }) {
  const actions = trackableActions(issue);
  if (actions.length === 0) return null;

  return (
    <div className="mb-6">
      <h3 className="font-mono text-xs tracking-widest text-ink-lo mb-3 uppercase">
        Track Legislation
      </h3>
      <div className="space-y-2">
        {actions.map((action, i) => {
          const { href, internal } = trackActionLink(issue, action);
          const linkClass =
            "flex items-center gap-3 p-3 border border-white/15 bg-signal-cyan/10 hover:border-signal-cyan/40 hover:bg-signal-cyan/10 transition-all group";
          const inner = (
            <>
              <span className="text-sm text-ink group-hover:text-phos flex-1">
                {trackActionText(action, internal)}
              </span>
              <span className="text-xs font-mono tracking-wide text-ink-lo shrink-0">
                {internal ? "VIEW BILL →" : "CONGRESS.GOV ↗"}
              </span>
            </>
          );
          return internal ? (
            <Link key={i} href={href} className={linkClass}>
              {inner}
            </Link>
          ) : (
            <a key={i} href={href} target="_blank" rel="noopener noreferrer" className={linkClass}>
              {inner}
            </a>
          );
        })}
      </div>
    </div>
  );
}

export function OfficialLegislation({ issue }: { issue: ActionIssue }) {
  if (!issue.relatedBills || issue.relatedBills.length === 0) return null;

  return (
    <div className="mb-6">
      <h3 className="font-mono text-xs tracking-widest text-ink-lo mb-3 uppercase">
        Official Legislation
      </h3>
      <div className="space-y-2">
        {issue.relatedBills.map((bill) => {
          const { href, internal } = billLink(bill);
          const linkClass =
            "flex items-center gap-3 p-3 border border-signal-amber/40 bg-signal-amber/10 hover:border-signal-amber/40 hover:bg-signal-amber/10 transition-all group";
          const inner = (
            <>
              <span className="text-xs font-mono tracking-wide text-ink-lo border border-signal-amber/40 px-1.5 py-0.5 shrink-0">
                {bill.id}
              </span>
              <span className="text-sm text-ink group-hover:text-phos truncate">{bill.name}</span>
              <span className="text-xs font-mono tracking-wide text-ink-lo shrink-0 ml-auto">
                {internal ? "VIEW BILL →" : "CONGRESS.GOV ↗"}
              </span>
            </>
          );
          return internal ? (
            <Link key={bill.id} href={href} className={linkClass}>
              {inner}
            </Link>
          ) : (
            <a
              key={bill.id}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className={linkClass}
            >
              {inner}
            </a>
          );
        })}
      </div>
    </div>
  );
}

/**
 * `today` is passed in rather than read from the clock here so a server-rendered
 * page and its hydration agree on whether a comment period is still open.
 */
export function RelatedDocuments({
  issue,
  today,
  className = "mb-4",
}: {
  issue: ActionIssue;
  today: string;
  className?: string;
}) {
  if (!issue.relatedExploreDocs || issue.relatedExploreDocs.length === 0) return null;

  return (
    <div className={className}>
      <h3 className="font-mono text-xs tracking-widest text-ink-min mb-3 uppercase">
        Related Documents
      </h3>
      <div className="space-y-2">
        {issue.relatedExploreDocs.map((doc) => {
          const commentOpen = !!(
            doc.commentUrl &&
            doc.commentsCloseOn &&
            doc.commentsCloseOn >= today
          );
          return (
            <div key={doc.id} className="space-y-1">
              <div className="flex items-center gap-2 text-sm">
                <span className="text-xs px-1 py-0.5 border border-white/[0.07] text-ink-min font-mono tracking-wide shrink-0">
                  {doc.docType.replace(/_/g, " ")}
                </span>
                <Link
                  href={`/explore/${doc.id}`}
                  className="text-signal-cyan hover:text-phos transition-colors truncate"
                >
                  {doc.title}
                </Link>
                <span className="text-ink-min text-xs shrink-0">{doc.date}</span>
              </div>
              {commentOpen && (
                <Link
                  href={`/explore/${doc.id}#comment`}
                  className="inline-flex items-center gap-1.5 text-xs font-mono tracking-wide text-signal-cyan hover:text-phos border border-white/15 hover:border-signal-cyan/40
                             px-2 py-0.5 transition-colors"
                >
                  → SUBMIT COMMENT
                </Link>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SourceList({
  issue,
  className = "flex items-center gap-2 flex-wrap pt-4 border-t border-white/[0.07]",
}: {
  issue: ActionIssue;
  className?: string;
}) {
  if (!issue.sourceNames || issue.sourceNames.length === 0) return null;
  return (
    <div className={className}>
      <span className="text-xs text-ink-min">SOURCES:</span>
      {issue.sourceNames.map((name, i) => (
        <SourceBadge key={name} name={name} url={issue.sourceUrls?.[i]} />
      ))}
    </div>
  );
}
