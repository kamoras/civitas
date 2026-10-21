import Link from "next/link";
import type { ReactNode } from "react";
import type { BoxTone, Figure } from "@/types/figure";
import { INLINE_LINK } from "@/lib/controlStyles";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import RecordFigure from "./RecordFigure";

/**
 * One box of the record: a kicker, a title, one short line, an optional
 * figure, and the full text behind "More". The homepage's front page is
 * the model; BoxGrid lays boxes out in ruled columns.
 *
 * The writing rule (AGENTS.md, "Boxes, not blocks"): a box's line is either
 * `dek`, a template the backend filled from stored fields, or `quote`, the
 * source's own words with the source named. Never model text, and never a
 * paraphrase written here. Nothing is dropped to make a box short: what
 * the line doesn't say goes in `more`, a native disclosure that
 * find-in-page still searches and opens.
 */

/** The kicker's colour: an event on the record, a step completed, a date
 *  that closes, or a plain label. Every one is a text token that reads on
 *  both themes' grounds (wcagContrast.test.ts). */
export const TONE: Record<BoxTone, string> = {
  record: "text-signal-orange",
  done: "text-phos",
  deadline: "text-signal-amber",
  neutral: "text-ink-lo",
};

/** One command, set by weight, not a fill: the palette keeps fills off
 *  commands. A 32px target. */
export const ACTION =
  "inline-flex min-h-8 items-center border-2 border-ink-hi px-3 text-sm font-semibold text-ink-hi hover:border-phos hover:text-phos";

/** A bordered command, the front page's chip, a 32px target. */
export const CHIP =
  "inline-flex min-h-8 items-center border border-ink-hi/60 px-2.5 text-sm text-ink-hi hover:border-phos hover:text-phos";

export interface BoxLink {
  label: string;
  href: string;
  /** A fuller name for a screen reader, where the label leans on the box. */
  ariaLabel?: string;
}

/** The front page's boxed glyph beside a kicker. */
export function Glyph({ children }: { children: ReactNode }) {
  return (
    <span
      aria-hidden="true"
      className="grid h-5 w-5 flex-none place-items-center border-[1.5px] border-current font-id text-[0.6875rem] leading-none"
    >
      {children}
    </span>
  );
}

export function Kicker({
  children,
  mark,
  tone = "neutral",
}: {
  children: ReactNode;
  mark?: string;
  tone?: BoxTone;
}) {
  return (
    <div className={`mb-1.5 flex items-center gap-2 text-sm font-semibold ${TONE[tone]}`}>
      {mark && <Glyph>{mark}</Glyph>}
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </div>
  );
}

const isExternal = (href: string) => /^https?:\/\//.test(href);

/** A link that is a page of this site (next/link), an anchor on this page,
 *  or another site (marked, and opened without a referrer). */
export function BoxAnchor({
  href,
  className,
  ariaLabel,
  children,
}: {
  href: string;
  className?: string;
  ariaLabel?: string;
  children: ReactNode;
}) {
  if (isExternal(href))
    return (
      <a href={href} className={className} aria-label={ariaLabel} rel="noopener noreferrer">
        {children}
        <span aria-hidden="true">&nbsp;↗</span>
      </a>
    );
  if (href.startsWith("#"))
    return (
      <a href={href} className={className} aria-label={ariaLabel}>
        {children}
      </a>
    );
  return (
    <Link href={href} className={className} aria-label={ariaLabel}>
      {children}
    </Link>
  );
}

type Line =
  | { dek?: ReactNode; quote?: undefined }
  | { quote: { text: string; source: ReactNode }; dek?: undefined };

export type RecordBoxProps = Line & {
  /** A short label over the title; a compact box may go without. */
  kicker?: ReactNode;
  mark?: string;
  tone?: BoxTone;
  title: ReactNode;
  href?: string;
  /** The title's heading level: 3 under a page section's h2 (the default). */
  level?: 2 | 3 | 4;
  /** Attributes for the heading, such as an "On this page" entry. */
  headingAttrs?: Record<string, string>;
  /** The box's anchor, for links from elsewhere on the page. */
  id?: string;
  /** Offer this box as a shared image under this anchor id. */
  share?: string;
  figure?: Figure | null;
  /** Chips under the line: the evidence behind it. */
  links?: BoxLink[];
  /** One command (call, comment, see a sample ballot). */
  action?: BoxLink;
  /** The full text, behind "More: <label>". `open` opens it on arrival
   *  (a deep link); `onToggle` hears the reader open or close it. */
  more?: {
    label: string;
    children: ReactNode;
    open?: boolean;
    onToggle?: (open: boolean) => void;
  } | null;
  /** Spans the grid, with a bigger title and figure. */
  lead?: boolean;
  /** Tighter: less padding and a narrower figure column, for a page whose
   *  boxes are a list a reader scans (the ballot's contests). */
  compact?: boolean;
  /** Anything else the box draws (a ladder, a share button), under the line. */
  children?: ReactNode;
  /** What the box draws across its full width, under the line and the
   *  figure (a list of candidates, a row of commands): beside a figure,
   *  `children` share the text's narrower column. */
  below?: ReactNode;
};

const RULE = "border-white/25";

export default function RecordBox(props: RecordBoxProps) {
  const {
    kicker,
    mark,
    tone,
    title,
    href,
    level = 3,
    headingAttrs,
    id,
    share,
    figure,
    links,
    action,
    more,
    lead = false,
    compact = false,
    children,
    below,
  } = props;
  const Heading = `h${level}` as const;
  return (
    <article
      id={id}
      {...(share ? { [SHARE_SECTION_ATTR]: share } : {})}
      // The rule left of a box sits in the middle of the grid's column gap;
      // BoxGrid clips the ones left of its first column.
      className={`@container relative min-w-0 border-b ${compact ? "py-2.5" : "py-4"} ${RULE} before:absolute before:inset-y-0 before:-left-5 before:w-px before:bg-white/25 ${
        lead ? "col-span-full" : ""
      }`}
    >
      <div
        className={`grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-3 ${
          figure
            ? lead
              ? "@min-[40rem]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] @min-[40rem]:items-center"
              : compact
                ? "@min-[20rem]:grid-cols-[minmax(0,1fr)_5.5rem] @min-[20rem]:items-center"
                : "@min-[20rem]:grid-cols-[minmax(0,1fr)_7rem] @min-[20rem]:items-center @min-[26rem]:grid-cols-[minmax(0,1fr)_9.5rem]"
            : ""
        }`}
      >
        <div className="min-w-0">
          {kicker && (
            <Kicker mark={mark} tone={tone}>
              {kicker}
            </Kicker>
          )}
          <Heading
            {...headingAttrs}
            className={`font-display font-semibold text-ink-hi [overflow-wrap:anywhere] ${
              lead
                ? "mb-2 text-[1.5rem] leading-tight @min-[40rem]:text-[1.875rem]"
                : compact
                  ? "mb-0.5 text-[1.0625rem] leading-snug"
                  : "mb-1 text-[1.1875rem] leading-snug"
            }`}
          >
            {href ? (
              <BoxAnchor href={href} className="hover:text-phos hover:underline">
                {title}
              </BoxAnchor>
            ) : (
              title
            )}
          </Heading>
          {props.quote ? (
            <figure className="max-w-prose">
              <blockquote className={`text-ink ${lead ? "text-base" : "text-[0.9375rem]"}`}>
                <p>&ldquo;{props.quote.text}&rdquo;</p>
              </blockquote>
              <figcaption className="mt-1 text-sm text-ink-min">{props.quote.source}</figcaption>
            </figure>
          ) : props.dek ? (
            <p className={`max-w-prose text-ink ${lead ? "text-base" : "text-[0.9375rem]"}`}>
              {props.dek}
            </p>
          ) : null}
          {children && <div className="mt-3">{children}</div>}
          {(links?.length || action) && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {action && (
                <BoxAnchor href={action.href} ariaLabel={action.ariaLabel} className={ACTION}>
                  {action.label}
                </BoxAnchor>
              )}
              {links?.map((l) => (
                <BoxAnchor
                  key={`${l.href} ${l.label}`}
                  href={l.href}
                  ariaLabel={l.ariaLabel}
                  className={CHIP}
                >
                  {l.label}
                </BoxAnchor>
              ))}
            </div>
          )}
        </div>
        {figure && (
          <div className="min-w-0">
            <RecordFigure f={figure} size={lead ? "boxLead" : "box"} />
          </div>
        )}
      </div>
      {below && <div className={compact ? "mt-2" : "mt-3"}>{below}</div>}
      {more && (
        <details
          open={more.open}
          onToggle={more.onToggle && ((e) => more.onToggle!(e.currentTarget.open))}
          className={`group/more text-sm ${compact ? "mt-1.5" : "mt-3"}`}
        >
          <summary
            className={`${INLINE_LINK} inline-flex min-h-6 cursor-pointer list-none items-center gap-1.5 [&::-webkit-details-marker]:hidden`}
          >
            <span aria-hidden="true" className="font-id no-underline group-open/more:hidden">
              +
            </span>
            <span aria-hidden="true" className="hidden font-id no-underline group-open/more:inline">
              −
            </span>
            More: {more.label}
          </summary>
          <div className={`mt-2 border-t pt-3 text-ink ${RULE}`}>{more.children}</div>
        </details>
      )}
    </article>
  );
}
