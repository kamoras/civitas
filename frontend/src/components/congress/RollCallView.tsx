import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import CiteThis from "@/components/shared/CiteThis";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { ordinal } from "@/components/scorecard/format";
import type { VoteDetail } from "@/types/congress";
import { CHAMBER_NAME, billHref, dayHref, longDate } from "@/lib/congress";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { CongressTabs } from "./CongressNav";
import { ResultLabel } from "./ReportParts";
import VoteMembers, { PartyBar } from "./VoteMembers";
import { STATUS_LABEL, nominationHref } from "@/lib/nominations";
import type { NominationVote, RollCallNomination } from "@/types/nomination";

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
  const counts: [string, number][] = [
    ["Yea", vote.yeas],
    ["Nay", vote.nays],
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
          <div className="mx-auto flex max-w-6xl flex-col gap-10">
            <div>
              <CongressTabs active="reports" />
              <nav aria-label="Breadcrumb" className="text-sm text-ink-lo">
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
              className="flex scroll-mt-28 flex-col gap-6"
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
                  className="max-w-4xl font-display text-2xl font-extrabold leading-tight text-ink-hi sm:text-4xl"
                >
                  {subject}
                </h1>
                <p className="text-base text-ink">
                  {vote.question}
                  {vote.billId && vote.billLabel && (
                    <>
                      {" · "}
                      <Link href={billHref(vote.billId, vote.congress)} className={LINK}>
                        {vote.billLabel}
                        {vote.billShortTitle ? `, ${vote.billShortTitle}` : ""}
                      </Link>
                    </>
                  )}
                </p>
                {vote.nominationVote && <NominationLinks nv={vote.nominationVote} />}
              </header>

              <div className="grid gap-8 border border-white/15 bg-surface p-4 sm:p-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
                <div className="flex flex-col gap-3">
                  <ResultLabel result={vote.result} rejected={vote.rejected} />
                  <p className="font-mono text-5xl leading-none tabular-nums text-ink-hi">
                    {vote.yeas}–{vote.nays}
                  </p>
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs uppercase tracking-[0.1em]">
                    {counts.map(([label, n]) => (
                      <div key={label} className="contents">
                        <dt className="text-ink-min">{label}</dt>
                        <dd className="tabular-nums text-ink-lo">{n}</dd>
                      </div>
                    ))}
                    {vote.majorityRequirement && (
                      <div className="contents">
                        <dt className="text-ink-min">Required</dt>
                        <dd className="text-ink-lo">{vote.majorityRequirement}</dd>
                      </div>
                    )}
                  </dl>
                </div>
                <div className="flex flex-col gap-3">
                  <h2 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                    By party
                  </h2>
                  {vote.parties.map((p) => (
                    <PartyBar key={p.party} split={p} />
                  ))}
                </div>
              </div>
            </section>

            <section aria-labelledby="members" className="flex flex-col gap-4">
              <h2
                id="members"
                className="scroll-mt-28 font-display text-2xl font-extrabold text-ink-hi"
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
                className="scroll-mt-28 font-display text-2xl font-extrabold text-ink-hi"
              >
                Source and citation
              </h2>
              <div className="grid gap-8 lg:grid-cols-2">
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
                    title={`${chamber} roll call ${vote.number}, ${ordinal(vote.congress)} Congress: ${subject}`}
                    date={vote.date}
                    path={path}
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
