import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import CiteThis from "@/components/shared/CiteThis";
import { rollCallCitedSource } from "@/lib/citedSources";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { ordinal } from "@/components/scorecard/format";
import type { VoteDetail } from "@/types/congress";
import { CHAMBER_NAME, billHref, dayHref, longDate, voteResult, voteTally } from "@/lib/congress";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import SectionTabs from "@/components/layout/SectionTabs";
import { ResultLabel, VoteCount } from "./ReportParts";
import VoteMembers, { PartyBar } from "./VoteMembers";
import AmendmentPurpose from "./AmendmentPurpose";
import SeatChart from "./SeatChart";
import { STATUS_LABEL, nominationHref } from "@/lib/nominations";
import type { NominationVote, RollCallNomination } from "@/types/nomination";
import OnThisPage from "@/components/shared/OnThisPage";
import { onThisPage } from "@/lib/onThisPage";

const LINK = "text-ink underline decoration-white/30 underline-offset-4 hover:text-phos";
const SOURCE_NAME = { senate: "Senate.gov", house: "the House Clerk" } as const;
const MEMBERS = { senate: "senator", house: "representative" } as const;

/**
 * One roll call: what was asked, what the chamber recorded as the result,
 * each party's split and every member's position, linked to the chamber's
 * own record of it. Every part is the record's (api/congress.py
 * vote_detail); the page adds no figure of its own. A nomination, a
 * procedural motion and a bill's passage all get the same page: half the
 * Senate's roll calls are on nominations, which have no bill page.
 */
export default function RollCallView({ vote, path }: { vote: VoteDetail; path: string }) {
  const chamber = CHAMBER_NAME[vote.chamber];
  // What the vote was on: the nominee or the measure, as the chamber titles
  // it; the question alone when the record names nothing else.
  const subject = vote.title || vote.question;
  const eyebrow = `${chamber} roll call ${vote.number} · ${ordinal(vote.congress)} Congress, ${ordinal(vote.session)} session`;
  // A quorum call or a Speaker election puts no question: no yeas, nays or
  // threshold to show (roll_call_shape).
  const isQuestion = !vote.kind || vote.kind === "question";
  // The public API's record of this roll call (api/public.py get_roll_call).
  const positionsApi = `/api/public/v1/roll-calls/${vote.chamber}/${vote.congress}/${vote.session}/${vote.number}`;
  const counts: [string, number][] = [
    ...(isQuestion
      ? ([
          ["Yea", vote.yeas],
          ["Nay", vote.nays],
        ] as [string, number][])
      : []),
    ["Present", vote.present],
    ["Not voting", vote.notVoting],
  ];
  return (
    <ShareSubjectProvider
      subject={{
        title: subject,
        subtitle: `${eyebrow} · ${longDate(vote.date)}`,
        url: absoluteUrl(path),
        source:
          vote.chamber === "senate"
            ? "U.S. Senate roll-call vote record"
            : "Clerk of the House roll-call vote record",
        // A recorded vote doesn't change after the day it was cast.
        asOf: vote.date,
      }}
    >
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <OnThisPage />
          <div className="mx-auto flex max-w-6xl flex-col gap-10">
            <div>
              <SectionTabs section="congress" active="reports" />
              {/* Under 640px the section row above names where this page sits. */}
              <nav aria-label="Breadcrumb" className="hidden sm:block text-sm text-ink-lo">
                <Link href="/congress" className="hover:text-phos">
                  Congress
                </Link>
                {" / "}
                <Link href={dayHref(vote.date)} className="hover:text-phos">
                  {longDate(vote.date)}
                </Link>
                {" / "}
                <span aria-current="page">
                  {chamber} roll call {vote.number}
                </span>
              </nav>
            </div>

            <section
              {...{ [SHARE_SECTION_ATTR]: "vote" }}
              id="vote"
              aria-labelledby="vote-title"
              {...onThisPage("The vote", voteTally(vote))}
              className="flex flex-col gap-6"
            >
              <header className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-lo">
                    {eyebrow}
                  </p>
                  <ShareSectionButton label="This vote" withStrip={false} />
                </div>
                <h1
                  id="vote-title"
                  className="max-w-4xl font-display text-2xl font-semibold leading-tight text-ink-hi sm:text-4xl"
                >
                  {subject}
                </h1>
                {(vote.question !== subject || (vote.billId && vote.billLabel)) && (
                  <p className="text-base text-ink">
                    {/* An election of the Speaker has no title: the question is the h1. */}
                    {vote.question !== subject && vote.question}
                    {vote.billId && vote.billLabel && (
                      <>
                        {vote.question !== subject && " · "}
                        <Link href={billHref(vote.billId, vote.congress)} className={LINK}>
                          {vote.billLabel}
                          {vote.billShortTitle ? `, ${vote.billShortTitle}` : ""}
                        </Link>
                      </>
                    )}
                  </p>
                )}
                {vote.nominationVote && <NominationLinks nv={vote.nominationVote} />}
                <AmendmentPurpose vote={vote} chamber={vote.chamber} />
              </header>

              <div className="grid grid-cols-[minmax(0,1fr)] gap-8 border border-white/15 bg-surface p-4 sm:p-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
                <div className="flex flex-col gap-3">
                  <ResultLabel result={voteResult(vote)} rejected={vote.rejected} />
                  {vote.kind === "election" && vote.candidates?.length ? (
                    <dl className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1">
                      {vote.candidates.map((c) => (
                        <div key={c.name} className="contents">
                          <dt className="text-base text-ink">{c.name}</dt>
                          <dd className="font-mono text-3xl leading-none tabular-nums text-ink-hi">
                            {c.votes}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : vote.kind === "quorum" ? (
                    <p className="font-mono text-3xl leading-none tabular-nums text-ink-hi">
                      {voteTally(vote)}
                    </p>
                  ) : (
                    <p className="flex flex-col gap-2">
                      <VoteCount vote={vote} size="text-5xl" />
                    </p>
                  )}
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs uppercase tracking-[0.1em]">
                    {counts.map(([label, n]) => (
                      <div key={label} className="contents">
                        <dt className="text-ink-min">{label}</dt>
                        <dd className="tabular-nums text-ink-lo">{n}</dd>
                      </div>
                    ))}
                    {isQuestion && vote.majorityRequirement && (
                      <div className="contents">
                        <dt className="text-ink-min">Required</dt>
                        <dd className="text-ink-lo">{vote.majorityRequirement}</dd>
                      </div>
                    )}
                  </dl>
                </div>
                {vote.kind === "election" ? (
                  // An election of the Speaker is votes for names, not yea
                  // and nay: each party's candidates, as the bars show them.
                  <div className="flex flex-col gap-3">
                    <h2 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                      By party
                    </h2>
                    {vote.parties.map((p) => (
                      <PartyBar key={p.party} split={p} />
                    ))}
                  </div>
                ) : (
                  <SeatChart members={vote.members} parties={vote.parties} chamberName={chamber} />
                )}
              </div>
            </section>

            <section aria-labelledby="members" className="flex flex-col gap-4">
              <h2
                id="members"
                {...onThisPage(`How each ${MEMBERS[vote.chamber]} voted`, vote.members.length)}
                className="font-display text-2xl font-semibold text-ink-hi"
              >
                How each {MEMBERS[vote.chamber]} voted
                <span className="ml-2 font-mono text-sm font-normal text-ink-min">
                  {vote.members.length}
                </span>
              </h2>
              <VoteMembers members={vote.members} />
            </section>

            <section
              aria-labelledby="sources"
              className="flex flex-col gap-6 border border-white/25 bg-surface p-5"
            >
              <h2
                id="sources"
                {...onThisPage("Source and citation")}
                className="font-display text-2xl font-semibold text-ink-hi"
              >
                Source and citation
              </h2>
              <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
                <ul className="flex flex-col text-sm">
                  {vote.sourceUrl && (
                    <li className="border-b border-white/[0.07] py-1.5 text-ink-lo">
                      The roll call as {SOURCE_NAME[vote.chamber]} records it:{" "}
                      <a href={vote.sourceUrl} className={LINK}>
                        the official record (XML)
                      </a>
                    </li>
                  )}
                  <li className="border-b border-white/[0.07] py-1.5">
                    <Link href={dayHref(vote.date)} className={LINK}>
                      Everything Congress did on {longDate(vote.date)}
                    </Link>
                  </li>
                  {vote.billId && vote.billLabel && (
                    <li className="border-b border-white/[0.07] py-1.5">
                      <Link href={billHref(vote.billId, vote.congress)} className={LINK}>
                        {vote.billLabel}: its text, sponsor and every vote on it
                      </Link>
                    </li>
                  )}
                  <li className="border-b border-white/[0.07] py-1.5 text-ink-lo">
                    Every member&apos;s position as data:{" "}
                    <a href={`${positionsApi}.csv`} download className={LINK}>
                      spreadsheet (CSV)
                    </a>{" "}
                    ·{" "}
                    <a href={positionsApi} className={LINK}>
                      JSON
                    </a>
                  </li>
                  <li className="py-1.5 text-ink-lo">
                    <Link href="/about/data" className={LINK}>
                      Every data source
                    </Link>
                  </li>
                </ul>
                <div className="flex flex-col gap-2">
                  <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                    Cite this vote
                  </h3>
                  <CiteThis
                    title={`Roll call vote ${vote.number}, ${ordinal(vote.congress)} Congress, ${ordinal(vote.session)} session: ${subject}`}
                    date={vote.date}
                    path={path}
                    source={rollCallCitedSource(vote.chamber, vote.sourceUrl)}
                  />
                </div>
              </div>
            </section>
          </div>
        </main>
        <BackToTop />
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}

/** The nominations the vote's question names, each linked to its record:
 *  one for a single nomination, a folded list for an en bloc vote, counted
 *  from the question, so a vote whose nominations aren't all read yet still
 *  says how many it decided. */
function NominationLinks({ nv }: { nv: NominationVote }) {
  const label = (n: RollCallNomination) =>
    `${n.citation}${n.nominees.length === 1 && n.nomineeCount === 1 ? `: ${n.nominees[0]}` : ""}${
      n.positionTitle ? `, ${n.positionTitle}` : ""
    }`;
  if (nv.citationCount === 1) {
    const n = nv.nominations[0];
    if (!n)
      return (
        <p className="text-sm text-ink-lo">
          {nv.label}. The nomination&apos;s record isn&apos;t read yet.
        </p>
      );
    return (
      <p className="text-sm text-ink-lo">
        {nv.label}. The nomination&apos;s record, with its committee, hearings and every action:{" "}
        <Link href={nominationHref(n)} className={LINK}>
          {label(n)}
        </Link>
        {nv.kind === "confirm" && (
          <>
            {" "}
            <span className="font-mono text-xs text-ink-min">{STATUS_LABEL[n.status]}</span>
          </>
        )}
      </p>
    );
  }
  const unread = nv.citationCount - nv.nominations.length;
  return (
    <details className="text-sm text-ink-lo">
      <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.12em] hover:text-phos">
        [{nv.label} on {nv.citationCount} nominations at once, each with its record]
      </summary>
      <ul className="mt-2 flex flex-col gap-1">
        {nv.nominations.map((n) => (
          <li key={n.citation}>
            <Link href={nominationHref(n)} className={LINK}>
              {label(n)}
            </Link>
          </li>
        ))}
      </ul>
      {unread > 0 && (
        <p className="mt-2">
          {unread} of the {nv.citationCount} aren&apos;t read yet; they appear here as the daily
          sync reads them.
        </p>
      )}
    </details>
  );
}
