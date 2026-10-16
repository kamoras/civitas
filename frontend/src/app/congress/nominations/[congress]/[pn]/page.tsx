import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import CiteThis from "@/components/shared/CiteThis";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import SectionTabs from "@/components/layout/SectionTabs";
import VoteMembers, { PartyBar } from "@/components/congress/VoteMembers";
import { VoteCount } from "@/components/congress/ReportParts";
import AgencyLink from "@/components/agencies/AgencyLink";
import { StatusBadge } from "@/components/nominations/NominationRow";
import { ordinal } from "@/components/scorecard/format";
import { calendarDate, rollCallHref, shortDate, voteResult, voteTally } from "@/lib/congress";
import {
  CITATION,
  KIND_LABEL,
  NOMINATIONS_HREF,
  STATUS_NOTE,
  days,
  nominationHref,
} from "@/lib/nominations";
import { describeNomination } from "@/lib/seo";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { TOPIC_FEEDS, absoluteUrl, pageMetadata } from "@/lib/site";
import { fetchRecord } from "@/lib/ssrPayload";
import type { NominationDetail } from "@/types/nomination";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const LINK = "text-ink underline decoration-white/30 underline-offset-4 hover:text-phos";
// Names listed before the rest fold away: a military list names hundreds.
const SHOWN_NAMES = 12;

type Params = { congress: string; pn: string };
type PageProps = { params: Promise<Params> };

/** The nomination the address names, or null for one that can't name one or
 * isn't recorded. An outage throws, and congress/error.tsx says so. */
async function load({ congress, pn }: Params): Promise<NominationDetail | null> {
  if (!/^[1-9]\d{1,2}$/.test(congress) || !CITATION.test(pn)) return null;
  // Per request: `next build` can't reach the backend.
  await connection();
  return fetchRecord<NominationDetail>(
    `${BACKEND}/api/nominations/${congress}/${pn}`,
    { next: { revalidate: 600 } },
    "citation",
    "actions"
  );
}

function heading(n: NominationDetail): string {
  const first = n.nominees[0];
  if (n.nomineeCount > 1)
    return `${n.nomineeCount.toLocaleString()} nominees: ${[n.positionTitle, n.organization].filter(Boolean).join(", ")}`;
  const name = first?.names[0];
  const who = name
    ? [name.firstName, name.middleName, name.lastName].filter(Boolean).join(" ")
    : "";
  const office = n.positionTitle || n.organization;
  return who ? `${who}: ${office}` : office || n.description || `Nomination ${n.citation}`;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const p = await params;
  const n = await load(p);
  if (!n)
    return pageMetadata({
      title: "Nomination not found",
      description: "No nomination recorded at this address.",
      path: `${NOMINATIONS_HREF}/${p.congress}/${p.pn}`,
      noindex: true,
    });
  return pageMetadata({
    ...describeNomination(n, heading(n)),
    path: nominationHref(n),
    feed: TOPIC_FEEDS.congress,
    type: "article",
  });
}

export default async function NominationPage({ params }: PageProps) {
  const n = await load(await params);
  if (!n) notFound();
  const path = nominationHref(n);
  const title = heading(n);
  const eyebrow = `${n.citation} · ${ordinal(n.congress)} Congress · ${KIND_LABEL[n.kind]}`;
  const final = n.finalVote
    ? n.rollCalls.find(
        (rc) => rc.session === n.finalVote!.session && rc.number === n.finalVote!.number
      )
    : undefined;
  const names = n.nominees.flatMap((p) =>
    p.names.map((x) => ({ ...x, position: p.positionTitle }))
  );
  const facts: [string, React.ReactNode][] = [
    ["Received", n.receivedDate ? calendarDate(n.receivedDate) : "—"],
    [
      "Agency or body",
      n.organization ? (
        <AgencyLink name={n.organization} slug={n.agencySlug} className={LINK} />
      ) : (
        "—"
      ),
    ],
    ["Committee", n.committee?.name || "None named"],
    [
      "Hearings",
      n.hearings.length
        ? n.hearings.map((h, i) => (
            <span key={`${h.citation}-${i}`}>
              {i > 0 && ", "}
              {h.url ? (
                <a href={h.url} className={LINK}>
                  {h.citation || `Hearing ${h.jacketNumber}`}
                </a>
              ) : (
                h.citation
              )}
              {h.date ? ` (${shortDate(h.date)})` : ""}
            </span>
          ))
        : n.hearingHeld
          ? "Held; Congress.gov lists no transcript yet"
          : "None recorded",
    ],
    [
      n.status === "confirmed" ? "Confirmed" : "Status since",
      n.statusDate ? calendarDate(n.statusDate) : "—",
    ],
    ["Days to confirm", n.daysToConfirm != null ? days(n.daysToConfirm) : "—"],
  ];
  if (n.fjc)
    facts.push([
      "Judge's record",
      <a key="fjc" href={n.fjc.url} className={LINK}>
        Federal Judicial Center biography
      </a>,
    ]);

  return (
    <ShareSubjectProvider
      subject={{
        title,
        subtitle: eyebrow,
        url: absoluteUrl(path),
        source: "Congress.gov nomination record; U.S. Senate roll-call vote record",
        asOf: n.updateDate.slice(0, 10) || null,
      }}
    >
      <JsonLd
        data={breadcrumbList([
          { name: "Home", url: absoluteUrl("/") },
          { name: "Congress", url: absoluteUrl("/congress") },
          { name: "Nominations", url: absoluteUrl(NOMINATIONS_HREF) },
          { name: n.citation, url: absoluteUrl(path) },
        ])}
      />
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto flex max-w-6xl flex-col gap-10">
            <div>
              <SectionTabs section="congress" active="nominations" />
              <nav aria-label="Breadcrumb" className="text-sm text-ink-lo">
                <Link href={NOMINATIONS_HREF} className="hover:text-phos">
                  Nominations
                </Link>
                {" / "}
                <span aria-current="page">{n.citation}</span>
              </nav>
            </div>

            <section
              {...{ [SHARE_SECTION_ATTR]: "nomination" }}
              id="nomination"
              aria-labelledby="nomination-title"
              className="flex scroll-mt-28 flex-col gap-6"
            >
              <header className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-lo">
                    {eyebrow}
                  </p>
                  <ShareSectionButton label="This nomination" withStrip={false} />
                </div>
                <h1
                  id="nomination-title"
                  className="max-w-4xl font-display text-2xl font-extrabold leading-tight text-ink-hi sm:text-4xl"
                >
                  {title}
                </h1>
                <div className="flex flex-wrap items-center gap-3">
                  <StatusBadge status={n.status} />
                  <span className="text-sm text-ink-lo">{STATUS_NOTE[n.status]}</span>
                </div>
                {n.description && (
                  <blockquote className="max-w-4xl border-l-2 border-white/20 pl-3 text-base text-ink">
                    {n.description}
                    <footer className="mt-1 font-mono text-xs text-ink-min">
                      The nomination as Congress.gov records it
                    </footer>
                  </blockquote>
                )}
              </header>

              <dl className="grid gap-px border border-white/15 bg-white/15 sm:grid-cols-2 lg:grid-cols-3">
                {facts.map(([label, value]) => (
                  <div key={label} className="flex flex-col gap-1 bg-surface p-3">
                    <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                      {label}
                    </dt>
                    <dd className="text-base text-ink">{value}</dd>
                  </div>
                ))}
              </dl>

              {final && (
                <div className="border border-white/15 bg-surface p-4 sm:p-5">
                  <h2 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                    The vote to confirm · roll call {final.number}, {shortDate(final.date)}
                  </h2>
                  <p className="mt-2 flex flex-col gap-1">
                    <VoteCount vote={final} size="text-4xl" />
                  </p>
                  <p className="mt-1 text-sm text-ink-lo">{voteResult(final)}</p>
                  <div className="mt-4 flex flex-col gap-2">
                    {(final.parties ?? []).map((p) => (
                      <PartyBar key={p.party} split={p} />
                    ))}
                  </div>
                </div>
              )}
            </section>

            {n.finalVote && (
              <section aria-labelledby="senators" className="flex flex-col gap-4">
                <h2
                  id="senators"
                  className="scroll-mt-28 font-display text-2xl font-extrabold text-ink-hi"
                >
                  How each senator voted
                </h2>
                <p className="text-sm text-ink-lo">
                  On{" "}
                  <Link
                    href={rollCallHref({ chamber: "senate", congress: n.congress, ...n.finalVote })}
                    className={LINK}
                  >
                    roll call {n.finalVote.number}
                  </Link>
                  , from the Senate&apos;s own record. Pick a state to see its two senators.
                </p>
                <VoteMembers members={n.finalVote.members} />
              </section>
            )}

            {n.rollCalls.length > 0 && (
              <section aria-labelledby="votes" className="flex flex-col gap-3">
                <h2 id="votes" className="font-display text-2xl font-extrabold text-ink-hi">
                  Every recorded vote on it
                </h2>
                <ul className="border-t border-white/[0.09]">
                  {n.rollCalls.map((rc) => (
                    <li
                      key={`${rc.session}-${rc.number}`}
                      className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-white/[0.09] py-2.5"
                    >
                      <Link href={rollCallHref(rc)} className={LINK}>
                        {rc.label}
                        {rc.citations > 1 &&
                          ` (with ${rc.citations - 1} other nominations, en bloc)`}
                      </Link>
                      <span className="font-mono text-xs text-ink-lo">
                        {shortDate(rc.date)} · {voteResult(rc)} · {voteTally(rc)}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {names.length > 1 && (
              <section aria-labelledby="nominees" className="flex flex-col gap-3">
                <h2 id="nominees" className="font-display text-2xl font-extrabold text-ink-hi">
                  The {n.nomineeCount.toLocaleString()} nominees
                </h2>
                {n.nominees[0]?.introText && (
                  <p className="text-sm text-ink-lo">{n.nominees[0].introText}</p>
                )}
                <NameList names={names.slice(0, SHOWN_NAMES)} />
                {names.length > SHOWN_NAMES && (
                  <details>
                    <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos">
                      [The other {(names.length - SHOWN_NAMES).toLocaleString()}]
                    </summary>
                    <NameList names={names.slice(SHOWN_NAMES)} />
                  </details>
                )}
              </section>
            )}

            <section aria-labelledby="actions" className="flex flex-col gap-3">
              <h2 id="actions" className="font-display text-2xl font-extrabold text-ink-hi">
                Every action, newest first
              </h2>
              <ol className="border-t border-white/[0.09]">
                {n.actions.map((a, i) => (
                  <li
                    key={`${a.date}-${i}`}
                    className="grid gap-x-4 border-b border-white/[0.09] py-2 sm:grid-cols-[8rem_minmax(0,1fr)]"
                  >
                    <span className="font-mono text-xs text-ink-min">{shortDate(a.date)}</span>
                    <span className="text-sm text-ink">{a.text}</span>
                  </li>
                ))}
              </ol>
              <p className="text-sm text-ink-lo">
                Source:{" "}
                <a href={n.congressGovUrl} className={LINK}>
                  {n.citation} on Congress.gov
                </a>
                . Status is read from Congress.gov&apos;s action codes; the words are its own.
              </p>
            </section>

            <CiteThis title={title} date={n.receivedDate} path={path} />
          </div>
        </main>
        <BackToTop />
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}

function NameList({
  names,
}: {
  names: { firstName: string; middleName: string; lastName: string; state: string }[];
}) {
  return (
    <ul className="grid gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
      {names.map((x, i) => (
        <li
          key={`${x.lastName}-${i}`}
          className="border-b border-white/[0.07] py-1.5 text-sm text-ink"
        >
          {[x.firstName, x.middleName, x.lastName].filter(Boolean).join(" ")}
          {x.state && <span className="font-mono text-xs text-ink-min"> {x.state}</span>}
        </li>
      ))}
    </ul>
  );
}
