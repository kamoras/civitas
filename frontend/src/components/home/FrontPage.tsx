import Link from "next/link";
import { connection } from "next/server";
import type { ReactNode } from "react";
import type { FrontPage as FrontPageData, FrontPageEntry, FrontPageVote } from "@/types/frontPage";
import { billHref, rollCallHref, shortDate, weekHref } from "@/lib/congress";
import { nominationHref } from "@/lib/nominations";
import { exploreHref } from "@/lib/exploreHref";
import { ACTION_CENTER_HREF, OPEN_COMMENTS_HREF, agencyHref, monitorHref } from "@/lib/routes";
import { INLINE_LINK } from "@/lib/controlStyles";
import { SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import AmendmentPurpose from "@/components/congress/AmendmentPurpose";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import RecordFigure, { InlineRecordFigure, daysLeftWords } from "@/components/shared/RecordFigure";
import { CHIP, Glyph, Kicker as BoxKicker } from "@/components/shared/RecordBox";
import type { BoxTone } from "@/types/figure";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import YourMembersLink from "./YourMembersLink";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/notable.py).
const REVALIDATE_S = 300;

const SHARE_SOURCE =
  "House and Senate roll calls, Congress.gov, the Federal Register and the newsrooms named";

/**
 * "Entered into the record": the homepage as a newspaper front page. A
 * nameplate with the dateline, one lead story with a big figure, a second
 * tier of two, then three ruled columns (Congress, Agencies, In the news).
 *
 * Which entry goes where is the backend's fixed rule (api/notable.py's
 * docstring), and every sentence and figure is the backend's: this draws
 * them. Headlines are templates around the record or the record's own words
 * (a law's title, a rule's title, an outlet's headline); nothing is a
 * model's wording.
 *
 * Rendered on the server with the page. With nothing on record at all (or
 * a backend that can't answer) it renders nothing rather than an empty box.
 */
export default async function FrontPage() {
  await connection();
  const data = await fetchFrontPage();
  if (!data?.lead) return null;
  return <FrontPageView data={data} />;
}

async function fetchFrontPage(): Promise<FrontPageData | null> {
  try {
    const res = await fetch(`${BACKEND}/api/front-page`, { next: { revalidate: REVALIDATE_S } });
    return res.ok ? ((await res.json()) as FrontPageData) : null;
  } catch {
    return null;
  }
}

const MORE = `${INLINE_LINK} mt-3 inline-flex min-h-6 items-center text-sm`;
const RULE = "border-white/25";

const CHAMBER = { senate: "Senate", house: "House" } as const;

const KIND: Record<FrontPageEntry["kind"], { label: string; glyph: string; tone: BoxTone }> = {
  closest: { label: "Closest vote", glyph: "=", tone: "record" },
  loneBreak: { label: "Lone break", glyph: "1", tone: "record" },
  law: { label: "Became law", glyph: "§", tone: "record" },
  confirmed: { label: "Confirmed", glyph: "✓", tone: "record" },
  comment: { label: "Comment deadline", glyph: "✎", tone: "done" },
  issue: { label: "In the news", glyph: "¶", tone: "neutral" },
};

function kicker(e: FrontPageEntry, lead: boolean): string {
  const parts: string[] = [];
  if (e.kind === "closest") {
    parts.push(
      e.vote.tieBreaker ? "Tie broken" : lead ? "Closest vote of the week" : "Closest vote"
    );
    parts.push(CHAMBER[e.vote.chamber]);
  } else if (e.kind === "comment") {
    parts.push(KIND.comment.label, e.agencyName);
    return parts.join(" · ");
  } else if (e.kind === "issue") {
    parts.push(e.outlet ?? KIND.issue.label);
  } else {
    parts.push(KIND[e.kind].label);
  }
  parts.push(shortDate(e.date));
  return parts.join(" · ");
}

function Kicker({ entry, lead = false }: { entry: FrontPageEntry; lead?: boolean }) {
  const k = KIND[entry.kind];
  return (
    <BoxKicker mark={k.glyph} tone={k.tone}>
      {kicker(entry, lead)}
    </BoxKicker>
  );
}

/** Where an entry's headline goes. */
function primaryHref(e: FrontPageEntry): string {
  switch (e.kind) {
    case "closest":
    case "loneBreak":
      return rollCallHref(e.vote);
    case "law":
      return billHref(e.billId, e.congress);
    case "confirmed":
      return nominationHref(e);
    case "comment":
      return exploreHref({ id: e.id, key: e.key });
    case "issue":
      return `/issue/${e.publicId}`;
  }
}

/** What the vote was on, as the chamber titled it. */
function voteSubject(v: FrontPageVote): string {
  return [v.billLabel, v.question, v.title && v.title !== v.question ? v.title : null]
    .filter(Boolean)
    .join(" · ");
}

function Dek({ entry, lead }: { entry: FrontPageEntry; lead: boolean }) {
  const cls = lead ? "mb-3 max-w-2xl text-base text-ink-lo" : "text-sm text-ink-lo";
  switch (entry.kind) {
    case "closest":
    case "loneBreak":
      return (
        <>
          <div className={`${cls} line-clamp-3`}>{voteSubject(entry.vote)}</div>
          {entry.vote.amendment && (
            <div className={lead ? "mb-3" : "mt-1"}>
              <AmendmentPurpose vote={entry.vote} chamber={entry.vote.chamber} compact={!lead} />
            </div>
          )}
        </>
      );
    case "law":
      return (
        <div className={cls}>
          {entry.dek} · {entry.action}
        </div>
      );
    case "confirmed":
      return entry.dek ? <div className={cls}>{entry.dek}</div> : null;
    case "comment":
      return <div className={cls}>{entry.docType}</div>;
    case "issue":
      return <IssueMeta entry={entry} className={cls} />;
  }
}

function IssueMeta({
  entry,
  className,
}: {
  entry: Extract<FrontPageEntry, { kind: "issue" }>;
  className: string;
}) {
  const others = entry.outlets.filter((o) => o !== entry.outlet);
  return (
    <div className={`${className} flex flex-wrap items-center gap-x-1.5`}>
      {others.length > 0 && <span>Also reported by {others.join(", ")}</span>}
      {entry.alsoDates.length > 0 && (
        <span>
          {others.length > 0 ? "· " : ""}
          Also on {entry.alsoDates.map(shortDate).join(", ")}
        </span>
      )}
      {entry.monitor && <OngoingTag monitor={entry.monitor} />}
    </div>
  );
}

function OngoingTag({ monitor }: { monitor: { slug: string; title: string } }) {
  return (
    <Link
      href={monitorHref(monitor.slug)}
      aria-label={`Ongoing story: ${monitor.title}`}
      className="inline-flex min-h-6 items-center bg-surface-raised px-1.5 text-xs text-ink-hi hover:text-phos"
    >
      Ongoing story
    </Link>
  );
}

/** The commands under a lead or tier story: the evidence behind it. */
function Links({ entry }: { entry: FrontPageEntry }) {
  const items: ReactNode[] = [];
  const vote = "vote" in entry ? entry.vote : null;
  if (vote) {
    items.push(
      <YourMembersLink
        key="yours"
        href={rollCallHref(vote)}
        chamber={vote.chamber}
        className={CHIP}
      />,
      <Link
        key="roll"
        href={rollCallHref(vote)}
        aria-label={`The roll call: ${CHAMBER[vote.chamber]} roll call ${vote.number}`}
        className={CHIP}
      >
        The roll call
      </Link>
    );
    if (vote.billId && vote.billLabel)
      items.push(
        <Link key="bill" href={billHref(vote.billId, vote.congress)} className={CHIP}>
          {vote.billLabel}
        </Link>
      );
  }
  if (entry.kind === "loneBreak" && entry.memberId) {
    items.push(
      <Link key="member" href={`/politicians/${entry.memberId}`} className={CHIP}>
        {entry.memberName}
      </Link>
    );
    if (entry.compareWithId)
      items.push(
        <Link
          key="compare"
          href={`/compare?leftId=${entry.memberId}&leftChamber=senate&rightId=${entry.compareWithId}&rightChamber=senate`}
          className={CHIP}
        >
          {entry.memberName} vs. {entry.compareWithName ?? "the state's other senator"}
        </Link>
      );
  }
  if (entry.kind === "law" && entry.billLabel)
    items.push(
      <Link key="bill" href={billHref(entry.billId, entry.congress)} className={CHIP}>
        {entry.billLabel}
      </Link>
    );
  if (entry.kind === "confirmed")
    items.push(
      <Link key="nom" href={nominationHref(entry)} className={CHIP}>
        The nomination
      </Link>
    );
  if (entry.kind === "comment") {
    items.push(
      <a key="comment" href={entry.commentUrl} className={CHIP} rel="noopener noreferrer">
        Comment on regulations.gov<span aria-hidden="true">&nbsp;↗</span>
      </a>
    );
    if (entry.agencySlug)
      items.push(
        <Link key="agency" href={agencyHref(entry.agencySlug)} className={CHIP}>
          {entry.agencyName}
        </Link>
      );
  }
  if (entry.kind === "issue")
    items.push(
      <Link key="issue" href={primaryHref(entry)} className={CHIP}>
        The sources
      </Link>
    );
  return <div className="mt-3 flex flex-wrap gap-2">{items}</div>;
}

// ── Layout ──────────────────────────────────────────────────────────

function Headline({
  entry,
  className,
  as: Tag,
}: {
  entry: FrontPageEntry;
  className: string;
  as: "h3" | "h4";
}) {
  return (
    <Tag className={`font-display font-semibold text-ink-hi ${className}`}>
      <Link href={primaryHref(entry)} className="hover:text-phos hover:underline">
        {entry.headline}
      </Link>
    </Tag>
  );
}

function Lead({ entry }: { entry: FrontPageEntry }) {
  return (
    <article
      aria-label="Lead story"
      className="grid grid-cols-[minmax(0,1fr)] border-b border-ink-hi pb-5 md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]"
    >
      <div className={`border-b pb-4 md:border-b-0 md:border-r md:pb-0 md:pr-6 ${RULE}`}>
        <Kicker entry={entry} lead />
        <Headline
          entry={entry}
          as="h3"
          className="mb-2.5 text-[1.5625rem] leading-tight sm:text-[1.875rem]"
        />
        <Dek entry={entry} lead />
        <Links entry={entry} />
      </div>
      {entry.figure && (
        <div className="self-center pt-4 md:pl-6 md:pt-0">
          <RecordFigure f={entry.figure} size="lead" />
        </div>
      )}
    </article>
  );
}

// The second of a pair, ruled off from the first: beside it on a wide
// screen, under it on a phone.
const SECOND = `border-t md:border-l md:border-t-0 md:pl-6 ${RULE}`;

function TierStory({ entry, second }: { entry: FrontPageEntry; second: boolean }) {
  return (
    <article className={`@container py-4 ${second ? SECOND : "md:pr-6"}`}>
      <div className="grid grid-cols-[minmax(0,1fr)] items-center gap-4 @min-[26rem]:grid-cols-[minmax(0,1fr)_9.5rem]">
        <div className="min-w-0">
          <Kicker entry={entry} />
          <Headline entry={entry} as="h3" className="mb-1 text-[1.1875rem] leading-snug" />
          <Dek entry={entry} lead={false} />
          <Links entry={entry} />
        </div>
        {entry.figure && <RecordFigure f={entry.figure} size="tier" />}
      </div>
    </article>
  );
}

/** The small line under a column item: its date, and what its figure says. */
function itemMeta(e: FrontPageEntry): string {
  switch (e.kind) {
    case "closest":
      return `${shortDate(e.date)} · ${CHAMBER[e.vote.chamber]} · ${e.figure.caption}`;
    case "loneBreak":
      return `${shortDate(e.date)} · ${e.figure.caption}`;
    case "law":
      return `${shortDate(e.date)} · ${e.action}`;
    case "confirmed":
      return [shortDate(e.date), e.figure?.type === "split" ? e.figure.caption : null, e.dek]
        .filter(Boolean)
        .join(" · ");
    case "comment":
      return [
        e.agencyName,
        `Comments close ${shortDate(e.date)}`,
        e.figure ? daysLeftWords(e.figure) : null,
      ]
        .filter(Boolean)
        .join(" · ");
    case "issue":
      return [e.outlet, shortDate(e.date)].filter(Boolean).join(" · ");
  }
}

function ColumnItem({ entry }: { entry: FrontPageEntry }) {
  return (
    <li className={`border-b py-2.5 last:border-b-0 ${RULE}`}>
      <Headline entry={entry} as="h4" className="mb-1 text-base leading-snug" />
      {entry.kind === "law" && <div className="mt-0.5 text-sm text-ink-lo">{entry.dek}</div>}
      <InlineRecordFigure f={entry.figure} />
      <div className="mt-0.5 text-sm text-ink-min">
        {itemMeta(entry)}
        {entry.kind === "issue" && entry.alsoDates.length > 0 && (
          <> · also {entry.alsoDates.map(shortDate).join(", ")}</>
        )}
        {entry.kind === "issue" && entry.monitor && (
          <>
            {" · "}
            <OngoingTag monitor={entry.monitor} />
          </>
        )}
      </div>
    </li>
  );
}

const COLUMNS = [
  {
    key: "congress",
    title: "Congress",
    glyph: "§",
    more: "The week in Congress",
    none: "No other record votes, laws or confirmations to show.",
  },
  {
    key: "agencies",
    title: "Agencies",
    glyph: "✎",
    more: "Every open comment period",
    none: "No other comment periods open.",
  },
  {
    key: "news",
    title: "In the news",
    glyph: "¶",
    more: "The Action Center today",
    none: "No other stories in the record.",
  },
] as const;

function Column({
  column,
  entries,
  href,
  className,
}: {
  column: (typeof COLUMNS)[number];
  entries: FrontPageEntry[];
  href: string;
  className: string;
}) {
  const id = `front-page-${column.key}`;
  return (
    <section aria-labelledby={id} className={`min-w-0 pt-3 ${className}`}>
      <h3
        id={id}
        className="mb-1 flex items-center gap-2 border-b-2 border-ink-hi pb-1.5 text-sm font-semibold text-ink-hi"
      >
        <Glyph>{column.glyph}</Glyph>
        {column.title}
      </h3>
      {entries.length > 0 ? (
        <ul>
          {entries.map((e) => (
            <ColumnItem key={`${e.kind}-${e.date}-${e.headline}`} entry={e} />
          ))}
        </ul>
      ) : (
        <div className="py-2.5 text-sm text-ink-lo">{column.none}</div>
      )}
      <Link href={href} className={MORE}>
        {column.more} &rarr;
      </Link>
    </section>
  );
}

export function FrontPageView({ data }: { data: FrontPageData }) {
  if (!data.lead) return null;
  const congressMore = data.voteWeek ?? data.edition.week;
  const moreHref = {
    congress: congressMore ? weekHref(congressMore) : "/congress",
    agencies: OPEN_COMMENTS_HREF,
    news: ACTION_CENTER_HREF,
  };
  return (
    <ShareSubjectProvider
      subject={{
        title: "Entered into the record",
        subtitle: data.dateline.join(" · "),
        url: absoluteUrl("/"),
        source: SHARE_SOURCE,
        asOf: data.asOf,
      }}
    >
      <section
        aria-labelledby="front-page"
        {...{ [SHARE_SECTION_ATTR]: "record" }}
        {...shareMetaAttrs({ source: SHARE_SOURCE, asOf: data.asOf })}
        id="record"
        className="mx-auto mt-10 max-w-7xl px-4 sm:px-6"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-y border-ink-hi py-2 [border-top-style:double] [border-top-width:4px]">
          <h2
            id="front-page"
            className="font-display text-[1.625rem] font-semibold leading-tight text-ink-hi sm:text-[1.875rem]"
          >
            Entered into the record
          </h2>
          <div className="text-sm text-ink-lo">{data.dateline.join(" · ")}</div>
        </div>
        <div className="mb-5 flex flex-wrap md:flex-nowrap items-center justify-between gap-x-4 gap-y-1 border-b-[3px] border-ink-hi pb-2 pt-1.5 text-sm text-ink-lo">
          <div className="min-w-0 md:flex-1">{data.edition.sentence}</div>
          <div className="mt-0 flex flex-wrap items-center gap-x-4" data-share-exclude>
            {data.edition.week && data.edition.weekLabel && (
              <Link
                href={weekHref(data.edition.week)}
                className={`${INLINE_LINK} inline-flex min-h-6 items-center`}
              >
                {data.edition.weekLabel} &rarr;
              </Link>
            )}
            <ShareSectionButton label="Entered into the record" withStrip={false} />
          </div>
        </div>

        <Lead entry={data.lead} />

        {data.tier.length > 0 && (
          <div className="grid grid-cols-[minmax(0,1fr)] border-b border-ink-hi md:grid-cols-2">
            {data.tier.map((e, i) => (
              <TierStory key={`${e.kind}-${e.date}-${e.headline}`} entry={e} second={i > 0} />
            ))}
          </div>
        )}

        <div className="mt-1.5 grid grid-cols-[minmax(0,1fr)] gap-y-4 md:grid-cols-3 md:gap-y-0">
          {COLUMNS.map((c, i) => (
            <Column
              key={c.key}
              column={c}
              entries={data.columns[c.key]}
              href={moreHref[c.key]}
              className={
                i === 0 ? "md:pr-6" : `md:border-l md:pl-6 ${RULE} ${i === 1 ? "md:pr-6" : ""}`
              }
            />
          ))}
        </div>
      </section>
    </ShareSubjectProvider>
  );
}
