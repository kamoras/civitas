import { Fragment, type ReactNode } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import type { BillDetail } from "@/types/bill";
import type { BillPerson, BillRecord, CommitteeRecordPage } from "@/types/congress";
import { policyAreaLabel } from "@/lib/formatting";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { CHAMBER_NAME, dayHref, longDate, rollCallHref, shortDate } from "@/lib/congress";
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
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import { onThisPage } from "@/lib/onThisPage";
import { INLINE_LINK } from "@/lib/controlStyles";
import { ordinal } from "@/components/scorecard/format";
import { exploreHref } from "@/lib/exploreHref";

/** Cosponsors listed before the "All N cosponsors" disclosure. */
const COSPONSORS_SHOWN = 8;
/** Days in the daily reports shown as chips; the rest sit behind "More". */
const DAYS_SHOWN = 3;

const dayLabel = (d: { date: string; chamber: keyof typeof CHAMBER_NAME }) =>
  `${shortDate(d.date)}, ${d.date.slice(0, 4)} · ${CHAMBER_NAME[d.chamber]}`;

const PART_NAME: Record<string, string> = {
  bill: "the bill's details",
  summaries: "the summary",
  actions: "the action history",
  cosponsors: "the cosponsors",
  text: "the text versions",
};

// A row of a list inside a box's "More".
const ROW = "border-b border-white/[0.09] py-2.5 last:border-b-0";
const DATE = "font-mono text-xs tabular-nums text-ink-lo";
const NOTE = "text-sm text-ink-lo";

function Person({ p }: { p: BillPerson }) {
  const label = `${p.party}-${p.state}${p.district ? `-${p.district}` : ""}`;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      {p.page ? (
        <Link href={p.page} className="text-ink-hi hover:text-phos hover:underline">
          {p.name}
        </Link>
      ) : (
        <span className="text-ink-hi">{p.name}</span>
      )}
      <span
        className={`whitespace-nowrap font-mono text-sm ${PARTY_COLORS[p.party] ?? "text-ink-lo"}`}
      >
        {label}
      </span>
    </span>
  );
}

/** "Sep 24, 2026": a dated row's date. */
const rowDate = (iso: string) => `${shortDate(iso).slice(5)}, ${iso.slice(0, 4)}`;

/** A section's share-image attributes: the id is the section's anchor. */
const shareSection = (id: string) => ({ [SHARE_SECTION_ATTR]: id });

/**
 * A bill's page, as boxes (AGENTS.md "Boxes, not blocks"): its header, then
 * one box per part of the record (where it is, what it does, how each
 * chamber decided, its votes, history, committee record, cost, sponsor,
 * text, floor debate), each a templated line from the backend
 * (bill_boxes.py) or a credited quote, with the full text behind "More";
 * then every recorded vote, and the sources.
 */
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
  const congress = record?.congress ?? detail?.congress ?? null;
  const reportedDays = new Set((record?.days ?? []).map((d) => d.date));
  // Where each glossary term first appears in the history, which is where
  // its meaning is shown.
  const explained = new Map<string, number>();
  (record?.actions ?? []).forEach((a, i) => {
    if (a.term && !explained.has(a.term)) explained.set(a.term, i);
  });
  const unavailable = record?.unavailable ?? [];
  const boxes = record?.boxes;
  const summary = record?.summary;
  const votesCount = record
    ? record.coverage?.votes.held === false
      ? "not held"
      : !record.votes.length &&
          record.passage?.length &&
          record.passage.every((p) => p.method !== "rollCall")
        ? "no roll call"
        : `${record.votes.length} recorded`
    : undefined;
  // The page's parts, listed under the title.
  const jumps: [string, string, string?][] = [
    ...(record?.journey ? [["journey", "Its path"] as [string, string]] : []),
    ["summary", "Summary"],
    ["history", "History"],
    ["committee", "In committee"],
    ["text", "Text"],
    ["votes", "Votes", votesCount],
    ["sources", "Data & citation"],
  ];
  // What a shared image of any section says it is from.
  const shareSubject = {
    title: title && title !== label ? `${label}: ${title}` : label,
    subtitle: [congress ? `${ordinal(congress)} Congress` : null, stageName ?? detail?.stage]
      .filter(Boolean)
      .join(" · "),
    url: absoluteUrl(canonicalPath),
    source:
      "Congress.gov; Congressional Research Service; Senate.gov and House Clerk roll calls; Congressional Record Daily Digest",
    // Read when the page loads.
    asOf: null,
  };
  const status = boxes?.status;
  const digestHeld = record?.coverage?.digest.held !== false;
  const floorHeld = record?.coverage?.floor.held !== false;
  const cosponsorsRead = record && !unavailable.includes("cosponsors");

  return (
    <ShareSubjectProvider subject={shareSubject}>
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <OnThisPage />
          <div className="mx-auto max-w-6xl">
            <SectionTabs section="congress" active="bills" />
            {/* Under 640px the section row above names where this page sits. */}
            <nav aria-label="Breadcrumb" className="hidden sm:block mb-4 text-sm text-ink-lo">
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

            <header
              id="overview"
              {...shareSection("overview")}
              className="flex min-w-0 flex-col gap-3 pb-5"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm font-semibold text-ink-lo">
                  <span className="font-id">{label}</span>
                  {record?.congress ? ` · ${ordinal(record.congress)} Congress` : ""}
                  {record?.originChamber ? ` · ${record.originChamber}` : ""}
                </p>
                <span className="flex items-center gap-3">
                  <a
                    href="#sources"
                    className="inline-flex min-h-6 items-center text-sm text-ink-lo underline underline-offset-2 hover:text-phos"
                  >
                    Data &amp; citation
                  </a>
                  {/* The header names the bill itself: no title strip. */}
                  <ShareSectionButton label="Bill overview" withStrip={false} />
                </span>
              </div>
              <h1 className="font-display text-[1.75rem] font-semibold leading-tight text-ink-hi sm:text-4xl">
                {title}
              </h1>
              {shortTitle && under && (
                <div className="flex flex-col gap-1">
                  {/* The title type exactly as Congress.gov's titles list
                      names it, so a reader can tell a short title as
                      enacted from a popular name. */}
                  <p className="text-sm text-ink-min">
                    {shortTitleType || "Short title"} · Congress.gov
                  </p>
                  <p className="max-w-3xl text-[0.9375rem] text-ink">
                    <span className="mr-2 text-sm text-ink-min">
                      {officialTitle ? "Official title" : "Listed as"}
                    </span>
                    {under}
                  </p>
                </div>
              )}
              <nav aria-label="On this page" className="text-sm text-ink-lo">
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
                        className="inline-flex min-h-6 items-center px-1 underline decoration-white/30 underline-offset-4 hover:text-phos"
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
              {congress && (
                // The bill's own feed (the <head> names it first): each
                // action and roll call. Left out of a shared picture.
                <div data-share-exclude>
                  <FollowFeed subject={label} feed={billFeed(congress, billId, label)} />
                </div>
              )}
            </header>

            <BoxGrid columns={3} label="The record" labelId="bill-record">
              {status ? (
                <RecordBox
                  lead
                  id="journey"
                  headingAttrs={onThisPage("Its path")}
                  kicker={status.kicker}
                  mark={status.tone === "done" ? "§" : undefined}
                  tone={status.tone}
                  title={status.headline}
                  dek={status.dek}
                  figure={status.figure}
                  more={
                    record?.journey
                      ? {
                          label: "every step, dated",
                          children: (
                            <div className="flex flex-col gap-3">
                              <BillJourney journey={record.journey} />
                              {record.latestAction?.text && (
                                <p className={NOTE}>
                                  Latest action
                                  {record.latestAction.actionDate
                                    ? `, ${longDate(record.latestAction.actionDate)}`
                                    : ""}
                                  : <span className="text-ink">{record.latestAction.text}</span>
                                </p>
                              )}
                            </div>
                          ),
                        }
                      : null
                  }
                />
              ) : detail ? (
                // The site's own record without Congress.gov's: its stage.
                <RecordBox
                  lead
                  kicker="Where it is"
                  title={stageName ?? detail.stage}
                  dek={detail.isLaw ? "Became law." : undefined}
                />
              ) : null}

              <RecordBox
                id="summary"
                headingAttrs={onThisPage("Summary")}
                kicker="Summary · Congressional Research Service"
                title="What it does"
                {...(boxes?.summary
                  ? { quote: { text: boxes.summary.text, source: boxes.summary.source } }
                  : {
                      dek: unavailable.includes("summaries")
                        ? "The summary could not be loaded just now."
                        : summary
                          ? undefined
                          : "The Congressional Research Service has not published a summary of this bill yet.",
                    })}
                more={
                  summary
                    ? {
                        label: `the full summary${boxes?.summary ? `, ${boxes.summary.words.toLocaleString("en-US")} words` : ""}`,
                        children: (
                          <div className="flex flex-col gap-3">
                            <p className="text-sm text-ink-min">
                              Congressional Research Service · {summary.actionDesc},{" "}
                              {longDate(summary.actionDate)}
                            </p>
                            {summary.paragraphs.map((p, i) => (
                              <p key={i} className="max-w-prose text-[0.9375rem] text-ink">
                                {p}
                              </p>
                            ))}
                          </div>
                        ),
                      }
                    : null
                }
              />

              {detail?.stageMilestones && (
                <RecordBox
                  kicker={
                    <>
                      How far bills get
                      {stageName ? ` · ${stageName}` : ""}
                    </>
                  }
                  title="Where it stands among this Congress's bills"
                  // The backend's base rate for the stage: what happens to
                  // bills here, never a forecast for this one.
                  dek={detail.stageBaseRate?.sentence}
                >
                  <StageLadder data={detail.stageMilestones} />
                </RecordBox>
              )}
              {detail?.stageBaseRate && !detail.stageMilestones && (
                <RecordBox
                  kicker="How far bills get"
                  title={stageName ?? detail.stage}
                  dek={detail.stageBaseRate.sentence}
                />
              )}

              {(boxes?.passage ?? []).map((p) => {
                const held = p.figure != null && p.rollCall && congress;
                const href = held
                  ? rollCallHref({ ...p.rollCall!, congress: congress! })
                  : (p.rollCall?.sourceUrl ?? undefined);
                return (
                  <RecordBox
                    key={`${p.chamber}-${p.date}`}
                    mark={p.chamber === "house" ? "H" : "S"}
                    tone="record"
                    kicker={p.kicker}
                    title={p.headline}
                    href={href}
                    dek={p.dek}
                    figure={p.figure}
                    more={{
                      label: "the record's words",
                      children: (
                        <div className="flex flex-col gap-2">
                          <p>{p.sentence}</p>
                          <p className={NOTE}>
                            Congress.gov&apos;s action, verbatim:{" "}
                            <span className="text-ink">{p.text}</span>
                          </p>
                        </div>
                      ),
                    }}
                  />
                );
              })}

              {boxes?.votes && (
                <RecordBox
                  kicker="Votes"
                  title={boxes.votes.headline}
                  href="#votes"
                  dek={boxes.votes.dek}
                  figure={boxes.votes.figure}
                  more={{
                    label: "each result, as the chamber recorded it",
                    children: (
                      <ul>
                        {boxes.votes.results.map((r) => (
                          <li key={r.label} className={`flex justify-between gap-3 ${ROW}`}>
                            <span>{r.label}</span>
                            <span className="font-mono tabular-nums text-ink-lo">{r.count}</span>
                          </li>
                        ))}
                      </ul>
                    ),
                  }}
                />
              )}

              <RecordBox
                id="history"
                headingAttrs={onThisPage(
                  "History",
                  record && !unavailable.includes("actions")
                    ? `${record.actions.length} actions`
                    : undefined
                )}
                kicker="History · Congress.gov"
                title={
                  boxes?.history?.headline ??
                  (unavailable.includes("actions")
                    ? "The action history could not be loaded just now"
                    : "No actions recorded")
                }
                dek={boxes?.history?.dek}
                figure={boxes?.history?.figure}
                more={
                  record && record.actions.length > 0
                    ? {
                        label: `all ${record.actions.length} actions`,
                        children: (
                          <ol>
                            {record.actions.map((a, i) => (
                              <li
                                key={i}
                                className={`grid grid-cols-[minmax(0,1fr)] gap-x-4 gap-y-1 @min-[30rem]:grid-cols-[6.5rem_minmax(0,1fr)] ${ROW}`}
                              >
                                <span className={DATE}>{rowDate(a.date)}</span>
                                <span className="flex min-w-0 flex-col gap-1">
                                  <span className="text-ink">{a.text}</span>
                                  {/* A procedural term's plain meaning, from
                                      the glossary keyed by the action's code
                                      (bill_journey), the first time it appears. */}
                                  {a.term && a.meaning && explained.get(a.term) === i && (
                                    <span className="text-ink-lo">
                                      <span className="font-semibold text-ink">{a.term}:</span>{" "}
                                      {a.meaning}
                                    </span>
                                  )}
                                  {reportedDays.has(a.date) && (
                                    <Link
                                      href={dayHref(a.date)}
                                      className={`${INLINE_LINK} inline-flex min-h-6 w-fit items-center`}
                                    >
                                      That day in Congress
                                    </Link>
                                  )}
                                </span>
                              </li>
                            ))}
                          </ol>
                        ),
                      }
                    : null
                }
              >
                {boxes?.history?.latest && (
                  <p className={NOTE}>
                    Latest
                    {boxes.history.latest.date
                      ? `, ${longDate(boxes.history.latest.date)}`
                      : ""}: <span className="text-ink">{boxes.history.latest.text}</span>
                  </p>
                )}
              </RecordBox>

              <RecordBox
                id="committee"
                headingAttrs={onThisPage(
                  "In committee",
                  !digestHeld ? "not held" : committee ? String(committee.total) : undefined
                )}
                kicker="In committee · Daily Digest"
                title={
                  !digestHeld
                    ? "Not held for this Congress"
                    : committee
                      ? (committee.headline ?? "Committee meetings and reports")
                      : "The committee record could not be loaded just now"
                }
                dek={!digestHeld ? record?.coverage?.digest.sentence : undefined}
                figure={
                  digestHeld && committee && committee.total > 0
                    ? { type: "stat", value: committee.total, caption: "Digest entries" }
                    : null
                }
                more={
                  digestHeld && committee && committee.entries.length > 0
                    ? {
                        label: "each entry, word for word",
                        children: <CommitteeEntries entries={committee.entries} />,
                      }
                    : null
                }
                links={[
                  { label: "Every committee meeting and report", href: "/congress/committees" },
                ]}
              />

              {boxes?.cost && record && (
                <RecordBox
                  kicker="Cost · Congressional Budget Office"
                  title={boxes.cost.headline}
                  dek={boxes.cost.dek}
                  figure={boxes.cost.figure}
                  more={{
                    label: "each estimate",
                    children: (
                      <ul>
                        {record.cboCostEstimates.map((c) => (
                          <li key={c.url} className={ROW}>
                            <a href={c.url} className={INLINE_LINK}>
                              CBO, {c.pubDate.slice(0, 10)}
                            </a>
                            {c.title && <span className="block text-ink-lo">{c.title}</span>}
                          </li>
                        ))}
                      </ul>
                    ),
                  }}
                />
              )}

              {record && (
                <RecordBox
                  kicker={record.sponsors.length > 1 ? "Sponsors" : "Sponsor"}
                  title={
                    record.sponsors.length ? (
                      <span className="flex flex-col gap-1">
                        {record.sponsors.map((p) => (
                          <Person key={p.bioguideId ?? p.name} p={p} />
                        ))}
                      </span>
                    ) : (
                      "No sponsor on record"
                    )
                  }
                  dek={boxes?.sponsor.dek}
                  more={
                    cosponsorsRead && record.cosponsors.length
                      ? {
                          label: `the ${record.cosponsors.length} cosponsor${record.cosponsors.length === 1 ? "" : "s"}`,
                          children: (
                            // The first few, and the rest behind a native
                            // disclosure: ninety links between a keyboard
                            // reader and the votes was 124 Tab presses.
                            <>
                              <ul className="flex flex-col gap-1">
                                {record.cosponsors.slice(0, COSPONSORS_SHOWN).map((p) => (
                                  <li key={p.bioguideId ?? p.name}>
                                    <Person p={p} />
                                  </li>
                                ))}
                              </ul>
                              {record.cosponsors.length > COSPONSORS_SHOWN && (
                                <details className="mt-2">
                                  <summary
                                    className={`${INLINE_LINK} inline-flex min-h-6 cursor-pointer items-center`}
                                  >
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
                          ),
                        }
                      : null
                  }
                />
              )}

              <RecordBox
                id="text"
                headingAttrs={onThisPage("Text")}
                kicker="Text · Government Publishing Office"
                title={
                  boxes?.text?.headline ??
                  (unavailable.includes("text")
                    ? "The text versions could not be loaded just now"
                    : "No text published yet")
                }
                dek={boxes?.text?.dek}
                figure={boxes?.text?.figure}
                more={
                  record && record.textVersions.length > 0
                    ? {
                        label: "read each version, PDF or XML",
                        children: (
                          <>
                            <ul>
                              {record.textVersions.map((t) => (
                                <li
                                  key={`${t.type}-${t.date}`}
                                  className={`flex flex-wrap items-baseline justify-between gap-x-3 ${ROW}`}
                                >
                                  <span className="text-ink">
                                    {t.type}
                                    {t.date && (
                                      <span className={`ml-2 ${DATE}`}>{rowDate(t.date)}</span>
                                    )}
                                  </span>
                                  <span className="flex flex-wrap gap-x-4">
                                    {Object.entries(t.formats).map(([fmt, url]) => (
                                      <a
                                        key={fmt}
                                        href={url}
                                        className={`${INLINE_LINK} inline-flex min-h-6 min-w-6 items-center justify-center`}
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
                            <p className={`mt-2 ${NOTE}`}>
                              Text as published by the Government Publishing Office, on
                              Congress.gov.
                            </p>
                          </>
                        ),
                      }
                    : null
                }
              />

              {record?.floorDebate && (
                <RecordBox
                  id="floor"
                  headingAttrs={onThisPage(
                    "Floor debate",
                    floorHeld ? `${record.floorSpeechCount ?? 0} speeches` : "not held"
                  )}
                  kicker="Floor debate · Congressional Record"
                  title={
                    floorHeld
                      ? (boxes?.floor?.headline ?? "Floor debate")
                      : "Not held for this Congress"
                  }
                  dek={
                    floorHeld
                      ? "Speeches are matched by the bill number printed in the Record's heading, never by their words."
                      : record.coverage?.floor.sentence
                  }
                  figure={boxes?.floor?.figure}
                  more={
                    floorHeld && record.floorDebate.length > 0
                      ? {
                          label: "each heading and who spoke",
                          children: (
                            <>
                              <ol>
                                {record.floorDebate.map((d) => (
                                  <li key={`${d.date}-${d.chamber}-${d.heading}`} className={ROW}>
                                    <span className={DATE}>
                                      {rowDate(d.date)} · {CHAMBER_NAME[d.chamber]}
                                    </span>
                                    <p className="break-words text-ink">{d.heading}</p>
                                    <p className="mt-1 text-ink-lo">
                                      {d.speakers.map((s, i) => (
                                        <Fragment key={s.speechId}>
                                          {i > 0 && ", "}
                                          <Link
                                            href={exploreHref({ id: s.speechId, key: s.speechKey })}
                                            className={INLINE_LINK}
                                          >
                                            {s.speaker || "Unnamed speaker"}
                                          </Link>
                                          {s.speeches > 1 && (
                                            <span className="text-ink-min"> ({s.speeches})</span>
                                          )}
                                        </Fragment>
                                      ))}
                                    </p>
                                  </li>
                                ))}
                              </ol>
                              <p className={`mt-2 ${NOTE}`}>
                                Each name opens that member&apos;s first speech under the heading; a
                                number is how many times they spoke.
                              </p>
                            </>
                          ),
                        }
                      : null
                  }
                />
              )}

              {record && record.days.length > 0 && (
                <RecordBox
                  kicker="In the daily reports"
                  title="Days this bill was on the record"
                  // The latest few as chips, every other day behind "More".
                  links={record.days.slice(0, DAYS_SHOWN).map((d) => ({
                    label: dayLabel(d),
                    href: dayHref(d.date),
                  }))}
                  more={
                    record.days.length > DAYS_SHOWN
                      ? {
                          label: "every day",
                          children: (
                            <ul>
                              {record.days.slice(DAYS_SHOWN).map((d) => (
                                <li key={`${d.date}-${d.chamber}`} className={ROW}>
                                  <Link
                                    href={dayHref(d.date)}
                                    className={`${INLINE_LINK} inline-flex min-h-6 items-center`}
                                  >
                                    {dayLabel(d)}
                                  </Link>
                                </li>
                              ))}
                            </ul>
                          ),
                        }
                      : null
                  }
                />
              )}

              {detail && detail.relatedIssues.length > 0 && (
                <RecordBox kicker="In the Action Center" title="Issues that name this bill">
                  <ul className="flex flex-col gap-1">
                    {detail.relatedIssues.map((issue) => (
                      <li key={issue.id}>
                        <Link
                          href={
                            issue.publicId
                              ? `/issue/${issue.publicId}`
                              : `/action?date=${issue.date}`
                          }
                          className={`${INLINE_LINK} inline-flex min-h-6 items-center`}
                        >
                          {issue.title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </RecordBox>
              )}

              {detail && detail.policyAreas.length > 0 && (
                <RecordBox kicker="Policy areas · Civitas" title="What it touches">
                  <ul className="flex flex-col gap-1 text-[0.9375rem] text-ink">
                    {detail.policyAreas.map((a) => (
                      <li key={a.area}>{policyAreaLabel(a.area)}</li>
                    ))}
                  </ul>
                </RecordBox>
              )}
            </BoxGrid>

            {/* Every recorded vote, with each party's split and every
                member's position a pick away. Its own shared picture. */}
            <section className="mt-10 flex flex-col gap-3" {...shareSection("votes")}>
              <div className="flex flex-wrap items-baseline justify-between gap-3 border-b-2 border-ink-hi pb-1.5">
                <h2
                  id="votes"
                  {...onThisPage("Votes", votesCount)}
                  className="font-display text-xl font-semibold text-ink-hi"
                >
                  Votes
                  {votesCount !== undefined && (
                    // The comma is for a screen reader, which ran the two
                    // together ("Votesnot held").
                    <span className="ml-2 font-mono text-sm font-normal text-ink-min">
                      <span className="sr-only">, </span>
                      {votesCount}
                    </span>
                  )}
                </h2>
                <ShareSectionButton label="Votes" />
              </div>
              <VotePanel
                votes={record?.votes ?? []}
                congress={congress ?? 0}
                unreadable={!record}
                coverage={record?.coverage?.votes}
                named={record?.actions.flatMap((a) =>
                  a.rollCalls.map((rc) => ({ ...rc, date: a.date }))
                )}
                passage={record?.passage}
              />
            </section>

            {/* After the record, not above it: a reader opened this page for
                the bill. */}
            {strip && <div className="mt-12">{strip}</div>}

            <div className="mt-12">
              <BillSources
                billId={billId}
                label={label}
                title={title}
                congress={congress}
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
