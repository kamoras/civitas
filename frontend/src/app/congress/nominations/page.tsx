import Link from "next/link";
import { connection } from "next/server";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import ConfirmTimeChart from "@/components/nominations/ConfirmTimeChart";
import NominationRow from "@/components/nominations/NominationRow";
import { ordinal } from "@/components/scorecard/format";
import { calendarDate } from "@/lib/congress";
import {
  KIND_LABEL,
  KIND_NOTE,
  NOMINATIONS_HREF,
  STATUS_LABEL,
  STATUS_NOTE,
  days,
} from "@/lib/nominations";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { fetchRecord } from "@/lib/ssrPayload";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import type {
  NominationKind,
  NominationList,
  NominationStats,
  NominationStatus,
} from "@/types/nomination";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const STATUSES: NominationStatus[] = ["pending", "confirmed", "returned", "withdrawn"];
const KINDS: NominationKind[] = ["civilian", "judicial", "military"];
const PER_PAGE = 25;

type Search = { status?: string; kind?: string; agency?: string; q?: string; page?: string };

/** Only values the backend accepts; anything else is dropped, not sent. */
function filters(sp: Search) {
  const status = STATUSES.find((s) => s === sp.status);
  const kind = KINDS.find((k) => k === sp.kind);
  const agency = (sp.agency ?? "").slice(0, 300) || undefined;
  const q = (sp.q ?? "").trim().slice(0, 200) || undefined;
  const page = Math.min(Math.max(parseInt(sp.page ?? "1", 10) || 1, 1), 1000);
  return { status, kind, agency, q, page };
}

type Filters = ReturnType<typeof filters>;

function href(f: Partial<Filters>): string {
  const p = new URLSearchParams();
  if (f.kind) p.set("kind", f.kind);
  if (f.status) p.set("status", f.status);
  if (f.agency) p.set("agency", f.agency);
  if (f.q) p.set("q", f.q);
  if (f.page && f.page > 1) p.set("page", String(f.page));
  const qs = p.toString();
  return qs ? `${NOMINATIONS_HREF}?${qs}#list` : NOMINATIONS_HREF;
}

export async function generateMetadata({ searchParams }: { searchParams: Promise<Search> }) {
  const f = filters(await searchParams);
  return pageMetadata({
    title: "Presidential Nominations and Confirmations in the Senate",
    description:
      "Every presidential nomination sent to the Senate this Congress (cabinet, agency, ambassador, judge and military) with its status, the committee, the hearings, how long confirmation took and how each senator voted.",
    path: NOMINATIONS_HREF,
    // A filtered list is the same page narrowed: one URL in the index.
    noindex: Boolean(f.status || f.kind || f.agency || f.q || f.page > 1),
  });
}

export default async function NominationsPage({ searchParams }: { searchParams: Promise<Search> }) {
  const f = filters(await searchParams);
  // Per request: `next build` can't reach the backend. The sync is daily.
  await connection();
  const query = new URLSearchParams({ page: String(f.page), per_page: String(PER_PAGE) });
  if (f.status) query.set("status", f.status);
  if (f.kind) query.set("kind", f.kind);
  if (f.agency) query.set("agency", f.agency);
  if (f.q) query.set("q", f.q);
  const init = { next: { revalidate: 600 } };
  const [stats, list] = await Promise.all([
    fetchRecord<NominationStats>(`${BACKEND}/api/nominations/stats`, init, "kinds", "counts"),
    fetchRecord<NominationList>(`${BACKEND}/api/nominations?${query}`, init, "items", "counts"),
  ]);
  if (!stats || !list) throw new Error("Nominations: the record could not be read");
  const congress = `${ordinal(stats.congress)} Congress`;
  const pages = Math.max(1, Math.ceil(list.total / PER_PAGE));
  // Until the sync has read every nomination, everything here is of the
  // oldest updates read so far: said plainly, and no median shown as the
  // Congress's.
  const { complete, read, expected, nightsLeft } = stats.backfill;
  const civilian = stats.kinds.civilian.timeToConfirm;
  const judicial = stats.kinds.judicial.timeToConfirm;

  return (
    <ShareSubjectProvider
      subject={{
        title: "Nominations",
        subtitle: `The Senate · ${congress}`,
        url: absoluteUrl(NOMINATIONS_HREF),
        source: "Congress.gov nominations, received date to the Confirmed action",
        asOf: stats.asOf,
      }}
    >
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto flex max-w-6xl flex-col gap-10">
            <div>
              <SectionTabs section="congress" active="nominations" />
              <PageMasthead eyebrow={`The Senate · ${congress}`} title="Nominations">
                <p>
                  The president nominates; the Senate confirms.{" "}
                  {complete
                    ? "Every nomination sent this Congress, from cabinet secretaries and judges to military promotions, as Congress.gov records it."
                    : "The nominations sent this Congress, from cabinet secretaries and judges to military promotions, as Congress.gov records them, read a few hundred a day."}
                </p>
              </PageMasthead>
            </div>

            {!complete && stats.total > 0 && (
              <p
                role="status"
                className="border border-signal-amber/40 bg-signal-amber/10 p-3 text-base text-ink sm:p-4"
              >
                <span className="font-mono text-xs uppercase tracking-[0.14em] text-signal-amber">
                  Still reading
                </span>
                <br />
                {expected != null
                  ? `${read.toLocaleString()} of about ${expected.toLocaleString()} nominations read so far.`
                  : `${read.toLocaleString()} nominations read so far.`}{" "}
                Counts will change until every one is read
                {nightsLeft != null
                  ? `, in about ${nightsLeft} more day${nightsLeft === 1 ? "" : "s"}`
                  : ""}
                , and confirmation times are shown once they are.
              </p>
            )}

            {stats.total === 0 ? (
              <p className="text-base text-ink-lo">
                Not read yet: the first daily sync after this page went live fills it in.
              </p>
            ) : (
              <section
                {...{ [SHARE_SECTION_ATTR]: "at-a-glance" }}
                id="at-a-glance"
                aria-labelledby="glance-title"
                className="flex scroll-mt-28 flex-col gap-6"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <h2
                    id="glance-title"
                    className="max-w-3xl font-display text-xl leading-snug text-ink sm:text-2xl"
                  >
                    {stats.total.toLocaleString()} nominations{complete ? "" : " read so far"},
                    naming {stats.people.toLocaleString()} people.{" "}
                    {complete && civilian.median != null && (
                      <>
                        A civilian nomination took a median of{" "}
                        <span className="text-ink-hi">{days(civilian.median)}</span> to confirm
                        {judicial.median != null && (
                          <>
                            ; a judge, <span className="text-ink-hi">{days(judicial.median)}</span>
                          </>
                        )}
                        .
                      </>
                    )}
                  </h2>
                  <ShareSectionButton label="Nominations at a glance" />
                </div>

                <ul className="grid grid-cols-2 gap-px border border-white/15 bg-white/15 sm:grid-cols-4">
                  {STATUSES.map((s) => (
                    <li key={s} className="bg-surface">
                      <Link
                        href={href({ status: s })}
                        className="flex h-full min-h-11 flex-col gap-1 p-3 hover:bg-surface-raised sm:p-4"
                      >
                        <span className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                          {STATUS_LABEL[s]}
                        </span>
                        <span className="font-mono text-3xl tabular-nums leading-none text-ink-hi sm:text-4xl">
                          {stats.counts[s].toLocaleString()}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
                <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                  {STATUSES.map((s) => (
                    <div key={s}>
                      <dt className="inline font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                        {STATUS_LABEL[s]}:
                      </dt>{" "}
                      <dd className="inline text-ink-lo">{STATUS_NOTE[s]}</dd>
                    </div>
                  ))}
                </dl>

                {complete ? (
                  <div className="border border-white/15 bg-surface p-4 sm:p-5">
                    <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                      Days from nomination to confirmation
                    </h3>
                    <p className="mt-1 text-sm text-ink-lo">
                      Each bar counts confirmations that took that many days, {stats.binDays} days
                      to a bar. The shaded band is the middle half; the line, the median.
                    </p>
                    <div className="mt-4">
                      <ConfirmTimeChart stats={stats} />
                    </div>
                    <p className="mt-4 font-mono text-xs text-ink-min">
                      Source: Congress.gov, received date to the &ldquo;Confirmed&rdquo; action ·{" "}
                      {congress}
                      {stats.asOf ? ` · as of ${calendarDate(stats.asOf)}` : ""}
                    </p>
                  </div>
                ) : (
                  <p className="border border-white/15 bg-surface p-4 text-sm text-ink-lo sm:p-5">
                    Days from nomination to confirmation are shown once every nomination is read.
                    The ones read so far are the oldest updates, so their median would not be this
                    Congress&apos;s.
                  </p>
                )}
              </section>
            )}

            <section
              id="list"
              aria-labelledby="list-title"
              className="flex scroll-mt-28 flex-col gap-4"
            >
              <h2 id="list-title" className="font-display text-2xl font-extrabold text-ink-hi">
                {complete ? "Every nomination" : "Nominations read so far"}
              </h2>
              <form
                method="get"
                action={NOMINATIONS_HREF}
                className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor="nom-q"
                    className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min"
                  >
                    Name, office or state
                  </label>
                  <input
                    id="nom-q"
                    name="q"
                    type="search"
                    defaultValue={f.q ?? ""}
                    placeholder="e.g. Secretary of Labor, or Texas judge"
                    className="min-h-11 border border-white/15 bg-surface-base px-3 text-base text-ink-hi placeholder:text-ink-min focus:border-ink-lo focus:outline-none"
                  />
                </div>
                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor="nom-kind"
                    className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min"
                  >
                    Kind
                  </label>
                  <select
                    id="nom-kind"
                    name="kind"
                    defaultValue={f.kind ?? ""}
                    className="min-h-11 w-full min-w-0 border border-white/15 bg-surface-base px-2 text-base text-ink-hi"
                  >
                    <option value="">All kinds</option>
                    {KINDS.map((k) => (
                      <option key={k} value={k}>
                        {KIND_LABEL[k]}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor="nom-agency"
                    className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min"
                  >
                    Agency or body
                  </label>
                  <select
                    id="nom-agency"
                    name="agency"
                    defaultValue={f.agency ?? ""}
                    className="min-h-11 w-full min-w-0 border border-white/15 bg-surface-base px-2 text-base text-ink-hi"
                  >
                    <option value="">All</option>
                    {stats.organizations.map((o) => (
                      <option key={o.name} value={o.name}>
                        {o.name} ({o.count})
                      </option>
                    ))}
                  </select>
                </div>
                {f.status && <input type="hidden" name="status" value={f.status} />}
                <button
                  type="submit"
                  className={`min-h-11 self-end border px-4 font-mono text-sm uppercase tracking-[0.12em] ${BOXED_CONTROL.unselected}`}
                >
                  Show
                </button>
              </form>

              <nav aria-label="Status" className="flex flex-wrap gap-2">
                <Link
                  href={href({ ...f, status: undefined, page: 1 })}
                  aria-current={!f.status ? "page" : undefined}
                  className={`flex min-h-11 items-center border px-3 font-mono text-xs uppercase tracking-[0.12em] ${
                    !f.status ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                  }`}
                >
                  All {list.allStatuses.toLocaleString()}
                </Link>
                {STATUSES.map((s) => (
                  <Link
                    key={s}
                    href={href({ ...f, status: s, page: 1 })}
                    aria-current={f.status === s ? "page" : undefined}
                    className={`flex min-h-11 items-center border px-3 font-mono text-xs uppercase tracking-[0.12em] ${
                      f.status === s ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                    }`}
                  >
                    {STATUS_LABEL[s]} {list.counts[s].toLocaleString()}
                  </Link>
                ))}
              </nav>
              {f.kind && <p className="text-sm text-ink-lo">{KIND_NOTE[f.kind]}</p>}

              {list.items.length ? (
                <ul className="border-t border-white/[0.09]">
                  {list.items.map((n) => (
                    <NominationRow key={n.citation} n={n} />
                  ))}
                </ul>
              ) : (
                <p className="text-base text-ink-lo">No nomination matches these filters.</p>
              )}

              {pages > 1 && (
                <nav
                  aria-label="Pages"
                  className="flex flex-wrap items-center justify-between gap-3"
                >
                  {f.page > 1 ? (
                    <Link
                      href={href({ ...f, page: f.page - 1 })}
                      className="flex min-h-11 items-center border border-white/15 px-3 font-mono text-sm text-ink-lo hover:text-ink-hi"
                    >
                      ← Newer
                    </Link>
                  ) : (
                    <span />
                  )}
                  <span className="font-mono text-xs text-ink-min">
                    Page {f.page} of {pages} · {list.total.toLocaleString()} nominations
                  </span>
                  {f.page < pages ? (
                    <Link
                      href={href({ ...f, page: f.page + 1 })}
                      className="flex min-h-11 items-center border border-white/15 px-3 font-mono text-sm text-ink-lo hover:text-ink-hi"
                    >
                      Older →
                    </Link>
                  ) : (
                    <span />
                  )}
                </nav>
              )}
            </section>
          </div>
        </main>
        <BackToTop />
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}
