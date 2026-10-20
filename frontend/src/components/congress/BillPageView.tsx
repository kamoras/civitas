import { Fragment, type ReactNode } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import type { BillDetail } from "@/types/bill";
import type { BillPerson, BillRecord, CommitteeRecordPage } from "@/types/congress";
import { billStageStyle } from "@/lib/billStages";
import { policyAreaLabel } from "@/lib/formatting";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { CHAMBER_NAME, dayHref, longDate, shortDate } from "@/lib/congress";
import SectionTabs from "@/components/layout/SectionTabs";
import BillSources from "./BillSources";
import CommitteeEntries from "./CommitteeEntries";
import StageLadder from "./StageLadder";
import BillJourney from "./BillJourney";
import VotePanel from "./VotePanel";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl, billFeed } from "@/lib/site";
import FollowFeed from "@/components/shared/FollowFeed";
import OnThisPage from "@/components/shared/OnThisPage";
import { onThisPage } from "@/lib/onThisPage";
import { ordinal } from "@/components/scorecard/format";
import { exploreHref } from "@/lib/exploreHref";

/** Cosponsors listed before the "All N cosponsors" disclosure. */
const COSPONSORS_SHOWN = 8;

const PART_NAME: Record<string, string> = {
  bill: "the bill's details",
  summaries: "the summary",
  actions: "the action history",
  cosponsors: "the cosponsors",
  text: "the text versions",
};

function Person({ p }: { p: BillPerson }) {
  const label = `${p.party}-${p.state}${p.district ? `-${p.district}` : ""}`;
  return (
    <span className="flex items-baseline gap-2">
      {p.page ? (
        <Link href={p.page} className="text-ink hover:text-phos">
          {p.name}
        </Link>
      ) : (
        <span className="text-ink">{p.name}</span>
      )}
      <span
        className={`whitespace-nowrap font-mono text-xs ${PARTY_COLORS[p.party] ?? "text-ink-lo"}`}
      >
        {label}
      </span>
    </span>
  );
}

function SectionHead({
  id,
  title,
  count,
  shareable = false,
}: {
  id: string;
  title: string;
  count?: string | number;
  /** Offer this section as a shared image (the section must carry `data-share-section`). */
  shareable?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-3">
      <h2
        id={id}
        {...onThisPage(title, count)}
        className="font-display text-2xl font-semibold text-ink-hi"
      >
        {title}
        {count !== undefined && (
          // The comma is for a screen reader, which ran the two together
          // ("Votesnot held"); on screen the gap separates them.
          <span className="ml-2 font-mono text-sm font-normal text-ink-min">
            <span className="sr-only">, </span>
            {count}
          </span>
        )}
      </h2>
      {shareable && <ShareSectionButton label={title} />}
    </div>
  );
}

/** A CRS summary longer than this many words shows its title line and
 *  first paragraph, and the rest behind a disclosure. About twelve lines
 *  of the summary column at 1280 px: H.R. 1's whole summary put its votes
 *  55.6 screens down. */
const SUMMARY_OPEN_WORDS = 200;

const words = (text: string) => text.split(/\s+/).filter(Boolean).length;

/** The summary's paragraphs, the first one (the title line) bold. A long
 *  one keeps its title line and first paragraph open and the rest in a
 *  native disclosure, which find-in-page still searches and opens. */
function SummaryBody({ paragraphs }: { paragraphs: string[] }) {
  const para = (p: string, i: number) => (
    <p
      key={i}
      className={`max-w-3xl leading-relaxed ${i === 0 ? "font-semibold text-ink-hi" : "text-ink"}`}
    >
      {p}
    </p>
  );
  const total = paragraphs.reduce((n, p) => n + words(p), 0);
  const open = paragraphs.length > 2 && total > SUMMARY_OPEN_WORDS ? 2 : paragraphs.length;
  return (
    <>
      {paragraphs.slice(0, open).map(para)}
      {open < paragraphs.length && (
        <details className="group flex flex-col gap-3">
          <summary className="inline-flex min-h-6 w-fit cursor-pointer list-none items-center font-mono text-xs uppercase tracking-[0.12em] text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos [&::-webkit-details-marker]:hidden">
            <span className="group-open:hidden">
              Read the full CRS summary ({total.toLocaleString("en-US")} words)
            </span>
            <span className="hidden group-open:inline">Hide the rest of the summary</span>
          </summary>
          <div className="mt-3 flex flex-col gap-3">
            {paragraphs.slice(open).map((p, i) => para(p, i + open))}
          </div>
        </details>
      )}
    </>
  );
}

/** A section's share-image attributes: the id is the section's anchor. */
const shareSection = (id: string) => ({ [SHARE_SECTION_ATTR]: id });

export default function BillPageView({
  billId,
  canonicalPath,
  record,
  detail,
  stageName,
  strip,
  committee,
}: {
  billId: string;
  /** The page's own URL path, with ?congress= for an earlier Congress's bill. */
  canonicalPath: string;
  record: BillRecord | null;
  detail: BillDetail | null;
  stageName: string | null;
  /** The "this week" strip, rendered by the server page. */
  strip?: ReactNode;
  /** The committee meetings and reports that name the bill, as the Daily
   *  Digest printed them; null when they couldn't be read just now. */
  committee?: CommitteeRecordPage | null;
}) {
  const label = record?.billLabel ?? detail?.billId ?? billId;
  const listed = record?.title ?? detail?.title ?? "";
  // Congress.gov's short or popular title leads when the listing title isn't
  // it (H.R. 1: "One Big Beautiful Bill Act" over "An act to provide for
  // reconciliation…"), with the official title right under it.
  const shortTitle = record?.shortTitle ?? detail?.shortTitle ?? null;
  const shortTitleType = record?.shortTitle
    ? record.shortTitleType
    : (detail?.shortTitleType ?? null);
  // The bill's number when Congress.gov couldn't give its title just now:
  // an empty h1 left the page with no heading to name it.
  const title = shortTitle || listed || label;
  // Under a short title, the official one from Congress.gov's same list; the
  // listing title only when the list gives none (it can be a short title the
  // bill no longer goes by, so it is not called official).
  const officialTitle = record?.shortTitle ? record.officialTitle : (detail?.officialTitle ?? null);
  const under = officialTitle || listed;
  const reportedDays = new Set((record?.days ?? []).map((d) => d.date));
  // Where each glossary term first appears in the history, which is where
  // its meaning is shown.
  const explained = new Map<string, number>();
  (record?.actions ?? []).forEach((a, i) => {
    if (a.term && !explained.has(a.term)) explained.set(a.term, i);
  });
  const unavailable = record?.unavailable ?? [];
  const stage = detail ? billStageStyle(detail.stage) : null;
  // What the bill does, in the CRS's words: the summary's first paragraph
  // after its title line (the paragraph the page's description uses too).
  const summary = record?.summary;
  const lead = summary ? (summary.paragraphs[1] ?? summary.paragraphs[0]) : null;
  const votesCount = record
    ? record.coverage?.votes.held === false
      ? "not held"
      : !record.votes.length &&
          record.passage?.length &&
          record.passage.every((p) => p.method !== "rollCall")
        ? "no roll call"
        : `${record.votes.length} recorded`
    : undefined;
  // The page's sections, listed under the title: a long bill's summary and
  // path put its votes many screens down.
  const jumps: [string, string, string?][] = [
    ...(record?.journey ? [["journey", "Its path"] as [string, string]] : []),
    ["votes", "Votes", votesCount],
    ["summary", "Summary"],
    ["history", "History"],
    ["committee", "In committee"],
    ["text", "Text"],
    ["sources", "Data & citation"],
  ];
  // What a shared image of any section says it is from.
  const shareSubject = {
    title: title && title !== label ? `${label}: ${title}` : label,
    subtitle: [
      record?.congress ? `${ordinal(record.congress)} Congress` : null,
      stageName ?? detail?.stage,
    ]
      .filter(Boolean)
      .join(" · "),
    url: absoluteUrl(canonicalPath),
    source:
      "Congress.gov; Congressional Research Service; Senate.gov and House Clerk roll calls; Congressional Record Daily Digest",
    // Read when the page loads.
    asOf: null,
  };

  return (
    <ShareSubjectProvider subject={shareSubject}>
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <OnThisPage />
          <div className="mx-auto max-w-6xl">
            <SectionTabs section="congress" active="bills" />
            <nav aria-label="Breadcrumb" className="mb-4 text-sm text-ink-lo">
              <Link href="/congress" className="hover:text-phos">
                Congress
              </Link>
              {" / "}
              <Link href="/congress/bills" className="hover:text-phos">
                Bills
              </Link>
              {" / "}
              <span aria-current="page">{label}</span>
            </nav>

            <div
              id="overview"
              {...shareSection("overview")}
              className="grid grid-cols-[minmax(0,1fr)] gap-10 lg:grid-cols-[minmax(0,1fr)_22rem]"
            >
              <header className="flex min-w-0 flex-col gap-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-lo">
                    {label}
                    {record?.congress ? ` · ${ordinal(record.congress)} Congress` : ""}
                    {record?.originChamber ? ` · ${record.originChamber}` : ""}
                  </p>
                  <span className="flex items-center gap-3">
                    <a
                      href="#sources"
                      className="inline-flex min-h-6 items-center font-mono text-xs uppercase tracking-[0.12em] text-ink-lo underline underline-offset-2 hover:text-phos"
                    >
                      Data &amp; citation
                    </a>
                    {/* The header names the bill itself: no title strip. */}
                    <ShareSectionButton label="Bill overview" withStrip={false} />
                  </span>
                </div>
                <h1 className="font-display text-2xl font-semibold leading-tight text-ink-hi sm:text-4xl">
                  {title}
                </h1>
                {shortTitle && under && (
                  <div className="-mt-2 flex flex-col gap-2">
                    {/* The title type exactly as Congress.gov's titles list
                        names it, so a reader can tell a short title as
                        enacted from a popular name. */}
                    <p className="font-mono text-xs text-ink-min">
                      {shortTitleType || "Short title"} · Congress.gov
                    </p>
                    <p className="max-w-3xl text-sm leading-relaxed text-ink">
                      <span className="mr-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                        {officialTitle ? "Official title" : "Listed as"}
                      </span>
                      {under}
                    </p>
                  </div>
                )}
                <nav aria-label="On this page" className="font-mono text-xs text-ink-lo">
                  <ul className="flex flex-wrap items-center gap-x-1">
                    {jumps.map(([id, name, count], i) => (
                      <li key={id} className="flex items-center gap-x-1">
                        {i > 0 && (
                          <span aria-hidden="true" className="text-ink-min">
                            ·
                          </span>
                        )}
                        <a
                          href={`#${id}`}
                          className="inline-flex min-h-6 items-center px-1 uppercase tracking-[0.12em] underline decoration-white/30 underline-offset-4 hover:text-phos"
                        >
                          {name}
                          {/* Text, not a margin: a margin left the link's
                              name as "Votes(47 recorded)". */}
                          {count && ` (${count})`}
                        </a>
                      </li>
                    ))}
                  </ul>
                </nav>
                {record?.sponsors.length ? (
                  <p className="flex flex-wrap items-baseline gap-x-2 text-sm lg:hidden">
                    <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      Sponsor
                    </span>
                    {record.sponsors.map((p) => (
                      <Person key={p.bioguideId ?? p.name} p={p} />
                    ))}
                  </p>
                ) : null}
                {(record?.congress ?? detail?.congress) && (
                  // The bill's own feed (the <head> names it first): each
                  // action and roll call. Left out of a shared picture.
                  <div data-share-exclude>
                    <FollowFeed
                      subject={label}
                      feed={billFeed(record?.congress ?? detail!.congress, billId, label)}
                    />
                  </div>
                )}
                {lead && summary && (
                  <div className="flex flex-col gap-1.5">
                    <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                      What it does
                    </p>
                    <p className="max-w-3xl text-base leading-relaxed text-ink">{lead}</p>
                    <p className="font-mono text-xs text-ink-min">
                      Congressional Research Service, {shortDate(summary.actionDate)},{" "}
                      {summary.actionDate.slice(0, 4)} ·{" "}
                      <a
                        href="#summary"
                        className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                      >
                        full summary
                      </a>
                    </p>
                  </div>
                )}
                {record?.journey && (
                  <div id="journey" {...onThisPage("Its path")}>
                    <BillJourney journey={record.journey} passage={record.passage} />
                  </div>
                )}
                {detail?.stageMilestones && <StageLadder data={detail.stageMilestones} />}
                <div className="flex flex-wrap items-center gap-2">
                  {detail && stage && (
                    <span
                      className={`border px-2 py-0.5 font-mono text-xs uppercase tracking-widest ${stage.text} ${stage.border} ${stage.bg}`}
                    >
                      {stageName ?? detail.stage}
                    </span>
                  )}
                  {detail?.isLaw && (
                    <span className="border border-phos/30 bg-phos/10 px-2 py-0.5 font-mono text-xs uppercase tracking-widest text-phos">
                      Became law
                    </span>
                  )}
                </div>
                {record?.latestAction?.text && (
                  <p className="text-[15px] leading-relaxed text-ink-lo">
                    Latest action
                    {record.latestAction.actionDate
                      ? `, ${longDate(record.latestAction.actionDate)}`
                      : ""}
                    : <span className="text-ink">{record.latestAction.text}</span>
                  </p>
                )}
                {/* A base rate for the stage, worded and counted by the
                    backend (bill_service.stage_base_rate): what happens to
                    bills here, never a forecast for this one. */}
                {detail?.stageBaseRate && (
                  <p className="max-w-3xl border-l-2 border-white/20 pl-3 text-sm leading-relaxed text-ink-lo">
                    {detail.stageBaseRate.sentence}
                  </p>
                )}
                {unavailable.length > 0 && (
                  <p className="text-sm text-signal-amber">
                    {/* Not asked, or not answered: the site's hourly share of
                        Congress.gov can be spent, and the page then shows
                        what it has stored (api/bills.py). */}
                    We could not get {unavailable.map((p) => PART_NAME[p] ?? p).join(", ")} from
                    Congress.gov just now; those parts are missing below, not empty. Reload in a few
                    minutes.
                  </p>
                )}
              </header>

              {/* The votes come straight after the bill's header, and before
                  the sponsor box on a narrower screen: on a phone they sat
                  after the cosponsors, the "this week" strip and the summary.
                  Left out of the overview's shared picture, which is the
                  header; the votes have their own. */}
              <div className="min-w-0 lg:col-start-1" data-share-exclude>
                <section className="flex flex-col gap-3" {...shareSection("votes")}>
                  <SectionHead id="votes" title="Votes" count={votesCount} shareable />
                  <VotePanel
                    votes={record?.votes ?? []}
                    congress={record?.congress ?? detail?.congress ?? 0}
                    unreadable={!record}
                    coverage={record?.coverage?.votes}
                    named={record?.actions.flatMap((a) =>
                      a.rollCalls.map((rc) => ({ ...rc, date: a.date }))
                    )}
                    passage={record?.passage}
                  />
                </section>
              </div>

              <aside
                aria-label="Sponsor and facts"
                className="@container flex flex-col gap-4 self-start border border-white/15 bg-surface p-4 text-sm lg:col-start-2 lg:row-span-2 lg:row-start-1"
              >
                {record?.sponsors.length ? (
                  // On a phone the sponsor is named under the title instead.
                  <div className="hidden flex-col gap-1 lg:flex">
                    <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      Sponsor
                    </span>
                    {record.sponsors.map((p) => (
                      <Person key={p.bioguideId ?? p.name} p={p} />
                    ))}
                  </div>
                ) : null}
                {record && !unavailable.includes("cosponsors") && (
                  <div className="flex flex-col gap-1">
                    <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      Cosponsors · {record.cosponsors.length}
                    </span>
                    {record.cosponsors.length ? (
                      // The first few, and the rest behind a native
                      // disclosure: a bill with ninety cosponsors put ninety
                      // links between a keyboard reader and the votes (124
                      // Tab presses to the vote's state filter).
                      <>
                        <ul className="flex flex-col gap-1">
                          {record.cosponsors.slice(0, COSPONSORS_SHOWN).map((p) => (
                            <li key={p.bioguideId ?? p.name}>
                              <Person p={p} />
                            </li>
                          ))}
                        </ul>
                        {record.cosponsors.length > COSPONSORS_SHOWN && (
                          <details>
                            <summary className="cursor-pointer font-mono text-xs text-ink-lo hover:text-phos">
                              All {record.cosponsors.length} cosponsors
                            </summary>
                            <ul className="mt-1 flex flex-col gap-1">
                              {record.cosponsors.slice(COSPONSORS_SHOWN).map((p) => (
                                <li key={p.bioguideId ?? p.name}>
                                  <Person p={p} />
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </>
                    ) : (
                      <span className="text-ink-lo">None</span>
                    )}
                  </div>
                )}
                {/* Label beside value where the box has 18rem, else above it: at 200%
                    text the 7rem label column left "CBO, 2025-06-04" no room. */}
                <dl className="grid grid-cols-1 gap-x-3 gap-y-1 @min-[18rem]:grid-cols-[7rem_minmax(0,1fr)] @min-[18rem]:gap-y-2">
                  {record?.introducedDate && (
                    <>
                      <dt className="text-ink-min">Introduced</dt>
                      <dd className="text-ink">{longDate(record.introducedDate)}</dd>
                    </>
                  )}
                  {record?.policyArea && (
                    <>
                      <dt className="text-ink-min">Policy area</dt>
                      <dd className="text-ink">{record.policyArea}</dd>
                    </>
                  )}
                  {record?.cboCostEstimates.map((c) => (
                    <Fragment key={c.url}>
                      <dt className="text-ink-min">Cost estimate</dt>
                      <dd>
                        <a
                          href={c.url}
                          className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                        >
                          CBO, {c.pubDate.slice(0, 10)}
                        </a>
                      </dd>
                    </Fragment>
                  ))}
                </dl>
                {record && (
                  <a
                    href={record.congressGovUrl}
                    className="inline-flex min-h-6 w-fit items-center font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos"
                  >
                    On Congress.gov
                  </a>
                )}
              </aside>
            </div>

            <div className="mt-8 grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_22rem]">
              <div className="flex min-w-0 flex-col gap-12">
                <section className="flex flex-col gap-3" {...shareSection("summary")}>
                  <SectionHead id="summary" title="Summary" shareable />
                  {record?.summary ? (
                    <>
                      <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                        Congressional Research Service · {record.summary.actionDesc},{" "}
                        {longDate(record.summary.actionDate)}
                      </p>
                      <SummaryBody paragraphs={record.summary.paragraphs} />
                    </>
                  ) : (
                    <p className="text-ink-lo">
                      {unavailable.includes("summaries")
                        ? "The summary could not be loaded just now."
                        : "The Congressional Research Service has not published a summary of this bill yet."}
                    </p>
                  )}
                </section>

                {record?.floorDebate && (
                  <section className="flex flex-col gap-3">
                    <SectionHead
                      id="floor"
                      title="Floor debate"
                      count={
                        record.coverage?.floor.held === false
                          ? "not held"
                          : `${record.floorSpeechCount ?? 0} speech${record.floorSpeechCount === 1 ? "" : "es"}`
                      }
                    />
                    {record.coverage?.floor.held === false ? (
                      <p className="text-ink-lo">{record.coverage.floor.sentence}</p>
                    ) : record.floorDebate.length > 0 ? (
                      <ol className="flex flex-col">
                        {record.floorDebate.map((d) => (
                          <li
                            key={`${d.date}-${d.chamber}-${d.heading}`}
                            className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-4 border-b border-white/[0.09] py-2.5"
                          >
                            <span className="font-mono text-xs tabular-nums text-ink-lo">
                              {shortDate(d.date).slice(5)}, {d.date.slice(0, 4)}
                              <span className="block">{CHAMBER_NAME[d.chamber]}</span>
                            </span>
                            <div className="min-w-0">
                              <p className="break-words text-sm leading-relaxed text-ink">
                                {d.heading}
                              </p>
                              <p className="mt-1 text-sm text-ink-lo">
                                {d.speakers.map((s, i) => (
                                  <Fragment key={s.speechId}>
                                    {i > 0 && ", "}
                                    <Link
                                      href={exploreHref({ id: s.speechId, key: s.speechKey })}
                                      className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                                    >
                                      {s.speaker || "Unnamed speaker"}
                                    </Link>
                                    {s.speeches > 1 && (
                                      <span className="text-ink-min"> ({s.speeches})</span>
                                    )}
                                  </Fragment>
                                ))}
                              </p>
                            </div>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="text-ink-lo">
                        No floor speech on file names this bill in its Congressional Record heading.
                      </p>
                    )}
                    {record.coverage?.floor.held !== false && (
                      <p className="text-xs text-ink-min">
                        Speeches are matched by the bill number printed in the Congressional
                        Record&apos;s heading, never by their words. Each name opens that
                        member&apos;s first speech under the heading; a number is how many times
                        they spoke.
                      </p>
                    )}
                  </section>
                )}

                <section className="flex flex-col gap-3">
                  <SectionHead
                    id="history"
                    title="History"
                    count={
                      record && !unavailable.includes("actions")
                        ? `${record.actions.length} actions`
                        : undefined
                    }
                  />
                  {record && record.actions.length > 0 ? (
                    <ol className="flex flex-col">
                      {record.actions.map((a, i) => (
                        <li
                          key={i}
                          className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-4 border-b border-white/[0.09] py-2.5 sm:grid-cols-[6.5rem_minmax(0,1fr)_9rem]"
                        >
                          <span className="font-mono text-xs tabular-nums text-ink-lo">
                            {shortDate(a.date).slice(5)}, {a.date.slice(0, 4)}
                          </span>
                          <span className="flex min-w-0 flex-col gap-1">
                            <span className="text-sm leading-relaxed text-ink">{a.text}</span>
                            {/* A procedural term's plain meaning, from the
                                glossary keyed by the action's code
                                (bill_journey), the first time it appears. */}
                            {a.term && a.meaning && explained.get(a.term) === i && (
                              <span className="text-xs leading-relaxed text-ink-lo">
                                <span className="font-semibold text-ink">{a.term}:</span>{" "}
                                {a.meaning}
                              </span>
                            )}
                          </span>
                          {reportedDays.has(a.date) ? (
                            <Link
                              href={dayHref(a.date)}
                              className="text-right text-sm text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
                            >
                              That day in Congress
                            </Link>
                          ) : (
                            <span className="hidden sm:block" />
                          )}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="text-ink-lo">
                      {unavailable.includes("actions")
                        ? "The action history could not be loaded just now."
                        : "No actions recorded."}
                    </p>
                  )}
                </section>

                <section className="flex flex-col gap-3">
                  <SectionHead
                    id="committee"
                    title="In committee"
                    count={
                      record?.coverage?.digest.held === false
                        ? "not held"
                        : committee
                          ? `${committee.total} in the Daily Digest`
                          : undefined
                    }
                  />
                  {record?.coverage?.digest.held === false ? (
                    <p className="text-ink-lo">{record.coverage.digest.sentence}</p>
                  ) : committee && committee.entries.length > 0 ? (
                    <CommitteeEntries entries={committee.entries} />
                  ) : (
                    <p className="text-ink-lo">
                      {committee
                        ? "No committee meeting or report in the Daily Digest names this bill."
                        : "The committee record could not be loaded just now."}
                    </p>
                  )}
                  <Link
                    href="/congress/committees"
                    className="inline-flex min-h-6 w-fit items-center font-mono text-xs tracking-[0.1em] text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
                  >
                    Search every committee meeting and report →
                  </Link>
                </section>

                <section className="flex flex-col gap-3">
                  <SectionHead id="text" title="Text" />
                  {record && record.textVersions.length > 0 ? (
                    <ul className="flex flex-col">
                      {record.textVersions.map((t) => (
                        <li
                          key={`${t.type}-${t.date}`}
                          className="flex flex-wrap items-baseline justify-between gap-3 border-b border-white/[0.09] py-2.5"
                        >
                          <span className="text-ink">
                            {t.type}
                            {t.date && (
                              <span className="ml-2 font-mono text-xs text-ink-min">
                                {shortDate(t.date)}, {t.date.slice(0, 4)}
                              </span>
                            )}
                          </span>
                          <span className="flex flex-wrap gap-x-4 font-mono text-xs uppercase tracking-[0.1em]">
                            {Object.entries(t.formats).map(([fmt, url]) => (
                              <a
                                key={fmt}
                                href={url}
                                className="inline-flex min-h-6 min-w-6 items-center justify-center text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
                              >
                                {fmt === "Formatted Text"
                                  ? "Read"
                                  : fmt === "Formatted XML"
                                    ? "XML"
                                    : fmt}
                              </a>
                            ))}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-ink-lo">
                      {unavailable.includes("text")
                        ? "The text versions could not be loaded just now."
                        : "No text published yet."}
                    </p>
                  )}
                  <p className="text-sm text-ink-min">
                    Text as published by the Government Publishing Office, on Congress.gov.
                  </p>
                </section>
              </div>

              <aside aria-label="Where this bill appears" className="flex flex-col gap-6">
                {record && record.days.length > 0 && (
                  <section className="flex flex-col gap-2 border border-white/15 p-4">
                    <h2 className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      In the daily reports
                    </h2>
                    <ul className="flex flex-col">
                      {record.days.map((d) => (
                        <li
                          key={`${d.date}-${d.chamber}`}
                          className="border-b border-white/[0.09] py-2 text-sm"
                        >
                          <Link href={dayHref(d.date)} className="text-ink hover:text-phos">
                            {shortDate(d.date)}, {d.date.slice(0, 4)}
                          </Link>
                          <span className="ml-2 text-ink-lo">{CHAMBER_NAME[d.chamber]}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {detail && detail.relatedIssues.length > 0 && (
                  <section className="flex flex-col gap-2 border border-white/15 p-4">
                    <h2 className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      In the Action Center
                    </h2>
                    <ul className="flex flex-col gap-2">
                      {detail.relatedIssues.map((issue) => (
                        <li key={issue.id}>
                          <Link
                            href={
                              issue.publicId
                                ? `/issue/${issue.publicId}`
                                : `/action?date=${issue.date}`
                            }
                            className="text-sm text-ink hover:text-phos"
                          >
                            {issue.title}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {detail && detail.policyAreas.length > 0 && (
                  <section className="flex flex-col gap-2 border border-white/15 p-4">
                    <h2 className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      Policy areas (Civitas)
                    </h2>
                    <ul className="flex flex-col gap-1 text-sm text-ink">
                      {detail.policyAreas.map((a) => (
                        <li key={a.area}>{policyAreaLabel(a.area)}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </aside>
            </div>

            {/* After the record, not above it: a reader opened this page for
                the bill, and on a phone the strip pushed its votes down by a
                screen. */}
            {strip && <div className="mt-12">{strip}</div>}

            <div className="mt-12">
              <BillSources
                billId={billId}
                label={label}
                title={title}
                congress={record?.congress ?? detail?.congress ?? null}
                canonicalPath={canonicalPath}
                record={record}
                detail={detail}
              />
            </div>
          </div>
        </main>
        <BackToTop />
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}
