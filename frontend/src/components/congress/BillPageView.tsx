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
import { CongressTabs } from "./CongressNav";
import BillSources from "./BillSources";
import CommitteeEntries from "./CommitteeEntries";
import StageLadder from "./StageLadder";
import VotePanel from "./VotePanel";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { ordinal } from "@/components/scorecard/format";

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
    <div className="flex items-baseline justify-between gap-3">
      <h2 id={id} className="scroll-mt-28 font-display text-2xl font-extrabold text-ink-hi">
        {title}
        {count !== undefined && (
          <span className="ml-2 font-mono text-sm font-normal text-ink-min">{count}</span>
        )}
      </h2>
      {shareable && <ShareSectionButton label={title} />}
    </div>
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
  const title = record?.title ?? detail?.title ?? "";
  const reportedDays = new Set((record?.days ?? []).map((d) => d.date));
  const unavailable = record?.unavailable ?? [];
  const stage = detail ? billStageStyle(detail.stage) : null;
  // What the bill does, in the CRS's words: the summary's first paragraph
  // after its title line (the paragraph the page's description uses too).
  const summary = record?.summary;
  const lead = summary ? (summary.paragraphs[1] ?? summary.paragraphs[0]) : null;
  // What a shared image of any section says it is from.
  const shareSubject = {
    title: title ? `${label}: ${title}` : label,
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
          <div className="mx-auto max-w-6xl">
            <CongressTabs active="bills" />
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
              className="grid scroll-mt-28 gap-10 lg:grid-cols-[minmax(0,1fr)_22rem]"
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
                <h1 className="font-display text-2xl font-extrabold leading-tight text-ink-hi sm:text-4xl">
                  {title}
                </h1>
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

              <aside
                aria-label="Sponsor and facts"
                className="flex flex-col gap-4 border border-white/15 bg-surface p-4 text-sm"
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
                <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-2">
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
                    className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos"
                  >
                    On Congress.gov
                  </a>
                )}
              </aside>
            </div>

            {/* Below the bill, not above it: a reader opened this page for
                the bill, and on a phone the strip pushed its title down by a
                third of the screen. */}
            {strip && <div className="mt-10">{strip}</div>}

            <nav
              aria-label="On this page"
              className="mt-10 flex flex-wrap gap-6 border-b border-white/15"
            >
              {[
                ["summary", "Summary"],
                ["votes", "Votes"],
                ["history", "History"],
                ["committee", "In committee"],
                ["text", "Text"],
                ["sources", "Data & citation"],
              ].map(([id, name]) => (
                <a
                  key={id}
                  href={`#${id}`}
                  className="py-3 font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-ink-hi"
                >
                  {name}
                </a>
              ))}
            </nav>

            <div className="mt-8 grid gap-12 lg:grid-cols-[minmax(0,1fr)_22rem]">
              <div className="flex min-w-0 flex-col gap-12">
                <section className="flex flex-col gap-3" {...shareSection("summary")}>
                  <SectionHead id="summary" title="Summary" shareable />
                  {record?.summary ? (
                    <>
                      <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                        Congressional Research Service · {record.summary.actionDesc},{" "}
                        {longDate(record.summary.actionDate)}
                      </p>
                      {record.summary.paragraphs.map((p, i) => (
                        <p
                          key={i}
                          className={`max-w-3xl leading-relaxed ${i === 0 ? "font-bold text-ink-hi" : "text-ink"}`}
                        >
                          {p}
                        </p>
                      ))}
                    </>
                  ) : (
                    <p className="text-ink-lo">
                      {unavailable.includes("summaries")
                        ? "The summary could not be loaded just now."
                        : "The Congressional Research Service has not published a summary of this bill yet."}
                    </p>
                  )}
                </section>

                <section className="flex flex-col gap-3" {...shareSection("votes")}>
                  <SectionHead
                    id="votes"
                    title="Votes"
                    count={record ? `${record.votes.length} recorded` : undefined}
                    shareable
                  />
                  <VotePanel
                    votes={record?.votes ?? []}
                    congress={record?.congress ?? detail?.congress ?? 0}
                  />
                </section>

                <section className="flex flex-col gap-3">
                  <SectionHead
                    id="history"
                    title="History"
                    count={record ? `${record.actions.length} actions` : undefined}
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
                          <span className="text-sm leading-relaxed text-ink">{a.text}</span>
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
                    count={committee ? `${committee.total} in the Daily Digest` : undefined}
                  />
                  {committee && committee.entries.length > 0 ? (
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
                    className="font-mono text-xs tracking-[0.1em] text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
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
                          <span className="flex gap-4 font-mono text-xs uppercase tracking-[0.1em]">
                            {Object.entries(t.formats).map(([fmt, url]) => (
                              <a
                                key={fmt}
                                href={url}
                                className="text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
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
