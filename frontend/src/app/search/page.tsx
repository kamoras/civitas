import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SearchCombobox from "@/components/search/SearchCombobox";
import DocumentResults, { IndexCounts } from "@/components/search/DocumentResults";
import { pageMetadata } from "@/lib/site";
import { documentQuery, exploreParams, asksForDocuments } from "@/lib/documentSearch";
import { documentsHref, SEARCH_DOCUMENTS_HREF } from "@/lib/routes";
import { DOCUMENTS_DESCRIPTION, DOCUMENTS_TITLE } from "@/lib/seo";
import type { ExploreResponse, ExploreStats } from "@/lib/api";
import type { SearchItem, SearchResults } from "@/types/search";

// Per request, and the backend answer is never put in Next's data cache:
// that cache is a file on disk, and it would keep what readers searched
// for (AGENTS.md §8).
export const dynamic = "force-dynamic";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

type Params = Promise<Record<string, string | string[] | undefined>>;

function one(v: string | string[] | undefined): string {
  return (Array.isArray(v) ? v[0] : v)?.trim().slice(0, 300) ?? "";
}

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Params;
}): Promise<Metadata> {
  const params = await searchParams;
  const q = one(params.q);
  if (one(params.tab) === DOCUMENTS) {
    // The Documents filter with nothing asked is a standing page (what
    // Explore's index was): indexable, under its own canonical. Any query
    // or filter is a results page, never indexed.
    const bare = Object.keys(params).every((k) => k === "tab");
    return pageMetadata({
      title: q ? `Documents: ${q}` : DOCUMENTS_TITLE,
      description: DOCUMENTS_DESCRIPTION,
      path: SEARCH_DOCUMENTS_HREF,
      noindex: !bare,
    });
  }
  return pageMetadata({
    title: q ? `Search: ${q}` : "Search",
    description:
      "Search the whole Civitas record: members of Congress, bills, roll calls, agencies, committees, districts, races, ballot measures, issues, nominations, documents and pages.",
    path: "/search",
    // A results page is a different page for every query: never indexed.
    noindex: true,
  });
}

// The filter that is Explore's document list, with Explore's filters.
const DOCUMENTS = "documents";
const DOCUMENTS_LEDE = (
  <>
    Floor speeches, executive orders, federal rules and Supreme Court opinions, searched by topic or
    by exact name, number or &ldquo;quoted phrase&rdquo;. Many rules are open for public comment.
  </>
);
// Links back to /search never prefetch: each prefetch would be a search,
// with a query encode for the documents (about a second on the server).
// The merged list's first screenful; a kind's chip lists all of that kind.
const ALL_SHOWN = 25;

async function fetchResults(q: string): Promise<SearchResults | null> {
  try {
    const res = await fetch(`${BACKEND}/api/search?q=${encodeURIComponent(q)}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data?.items) ? (data as SearchResults) : null;
  } catch {
    return null;
  }
}

/** Counts by type and agency: no query in it, so Next may keep it a few
 * minutes (the backend's own lifetime). */
async function fetchStats(): Promise<ExploreStats | null> {
  try {
    const res = await fetch(`${BACKEND}/api/explore/stats`, { next: { revalidate: 300 } });
    return res.ok ? ((await res.json()) as ExploreStats) : null;
  } catch {
    return null;
  }
}

/** Explore's hybrid ranking for the Documents filter. `indexEmpty` comes
 * back as a 503 with a body saying so. */
async function fetchDocuments(params: URLSearchParams): Promise<ExploreResponse | null> {
  try {
    const res = await fetch(`${BACKEND}/api/explore?${params}`, { cache: "no-store" });
    if (res.status === 503) {
      const body = await res.json().catch(() => null);
      return body?.indexEmpty ? { query: "", results: [], count: 0, indexEmpty: true } : null;
    }
    return res.ok ? ((await res.json()) as ExploreResponse) : null;
  } catch {
    return null;
  }
}

async function fetchCommitteeTotal(q: string): Promise<number> {
  try {
    const res = await fetch(
      `${BACKEND}/api/congress/committee-record?q=${encodeURIComponent(q)}&per_page=1`,
      { cache: "no-store" }
    );
    const total = res.ok ? (await res.json())?.total : 0;
    return typeof total === "number" ? total : 0;
  } catch {
    return 0;
  }
}

/** A filter chip's address: the merged list, one kind of it, or the
 * Documents filter. */
function chipHref(q: string, tab: string): string {
  if (tab === DOCUMENTS) return documentsHref({ q });
  const p = new URLSearchParams({ q });
  if (tab) p.set("tab", tab);
  return `/search?${p}`;
}

// The current filter drawn inverted, a change of fill rather than of colour alone.
const CHIP_ON = "border-ink-hi bg-ink-hi text-surface-base";
const CHIP_OFF = "border-white/25 text-ink-hi hover:border-ink-hi";

function Chips({ q, tab, data }: { q: string; tab: string; data: SearchResults | null }) {
  const kinds = (data?.kinds ?? []).filter((k) => k.kind !== "document");
  const chips: { tab: string; label: string; count?: number }[] = [
    { tab: "", label: "All", count: data?.items.length },
    ...kinds.map((k) => ({ tab: k.kind, label: k.label, count: k.count })),
    { tab: DOCUMENTS, label: "Documents" },
  ];
  return (
    <nav aria-label="Filter the results" className="mt-6">
      <ul className="flex flex-wrap gap-2">
        {chips.map((c) => {
          const current = c.tab === tab;
          return (
            <li key={c.tab || "all"}>
              <Link
                prefetch={false}
                href={chipHref(q, c.tab)}
                aria-current={current ? "page" : undefined}
                className={`inline-flex min-h-11 items-center gap-2 border px-3 font-mono text-xs ${current ? CHIP_ON : CHIP_OFF}`}
              >
                {c.label}
                {c.count !== undefined && c.count > 0 && (
                  <span className={current ? "" : "text-ink-lo"}>{c.count}</span>
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function ItemLink({ item, showGroup = false }: { item: SearchItem; showGroup?: boolean }) {
  return (
    <Link
      href={item.url}
      className="group flex min-h-11 flex-col justify-center gap-0.5 border-b border-white/15 py-2 hover:bg-white/[0.04]"
    >
      <span className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-sans text-base font-semibold text-ink-hi underline decoration-ink-min underline-offset-4 group-hover:decoration-ink-hi">
          {item.label}
        </span>
        {showGroup && <span className="font-mono text-xs text-ink-lo">{item.group}</span>}
      </span>
      {item.context && <span className="font-mono text-xs text-ink-min">{item.context}</span>}
    </Link>
  );
}

function matches(n: number): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? "match" : "matches"}`;
}

/**
 * Site search's results: one list, members, bills, agencies, pages and
 * federal documents ranked together (backend app/site_search.fuse, which
 * fuses site search's matches with Explore's hybrid document ranking by
 * rank), each labelled with its kind, and filter chips over it. The
 * Documents chip is Explore's document list with its own filters
 * (`tab=documents`, where /explore redirects). A query that names one page
 * clearly (an exact identifier, or a match well clear of the next) never
 * sees this page: the server sends the reader straight there, JavaScript
 * or not. `list=1` shows the list anyway.
 */
export default async function SearchPage({ searchParams }: { searchParams: Params }) {
  const params = await searchParams;
  const q = one(params.q);
  const tab = one(params.tab);

  if (tab === DOCUMENTS) {
    const d = documentQuery(params);
    const stats = await fetchStats();
    // A type the index doesn't hold would be refused (422): the filter drops it.
    if (d.type && stats && !(d.type in (stats.byType ?? {}))) d.type = "";
    const asked = asksForDocuments(d);
    const [resp, committeeTotal] = await Promise.all([
      asked ? fetchDocuments(exploreParams(d)) : Promise.resolve(null),
      d.q.length >= 2 && !d.politicianId ? fetchCommitteeTotal(d.q) : Promise.resolve(0),
    ]);
    const carried: Record<string, string> = { tab: DOCUMENTS };
    if (d.chamber) carried.chamber = d.chamber;
    if (d.type) carried.type = d.type;
    if (d.agency) carried.agency = d.agency;
    if (d.politicianId) carried.politician_id = d.politicianId;
    if (d.commentOpen) carried.comment = "open";
    return (
      <div className="min-h-screen bg-surface-base text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto max-w-4xl">
            <PageMasthead
              eyebrow="Search · every branch"
              title="Documents"
              aside={<IndexCounts stats={stats} />}
              // Folded once something is asked, so the first result starts
              // on a phone's first screen.
              more={asked ? DOCUMENTS_LEDE : undefined}
            >
              {asked ? null : DOCUMENTS_LEDE}
            </PageMasthead>
            <div className="mt-6">
              <SearchCombobox
                initialQuery={q}
                autoFocus={false}
                label="Search documents"
                hidden={carried}
              />
            </div>
            <Chips q={q} tab={DOCUMENTS} data={null} />
            <div className="mt-6">
              <DocumentResults
                d={d}
                stats={stats}
                resp={resp}
                failed={asked && resp === null}
                committeeTotal={committeeTotal}
              />
            </div>
          </div>
        </main>
        <Footer />
      </div>
    );
  }

  const data = q ? await fetchResults(q) : null;
  if (data?.direct && one(params.list) !== "1") redirect(data.direct);

  const shown = tab
    ? (data?.items ?? []).filter((i) => i.kind === tab)
    : (data?.items ?? []).slice(0, ALL_SHOWN);
  const total = data?.items.length ?? 0;
  const tabLabel = data?.kinds.find((k) => k.kind === tab)?.label;
  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-4xl">
          <PageMasthead eyebrow="The whole record" title="Search">
            One box for everything Civitas keeps: members, bills, roll calls, agencies, committees,
            districts, races, ballot measures, issues, nominations, documents and the site&apos;s
            own pages.
          </PageMasthead>

          <div className="mt-6">
            <SearchCombobox initialQuery={q} autoFocus={!q} hidden={tab ? { tab } : undefined} />
          </div>

          {q && <Chips q={q} tab={tab} data={data} />}

          {q && !data && (
            <p
              role="alert"
              className="mt-8 border-2 border-signal-amber/60 p-4 font-mono text-sm text-ink-hi"
            >
              Search didn&apos;t answer just now. Try again, or{" "}
              <Link prefetch={false} href={documentsHref({ q })} className="underline">
                search the documents alone
              </Link>
              .
            </p>
          )}

          {data?.unavailable && (
            <p
              role="status"
              className="mt-8 border-2 border-white/25 p-4 font-mono text-sm text-ink-hi"
            >
              The search index is still being built (it takes a few minutes after an install).
              Meanwhile,{" "}
              <Link prefetch={false} href={documentsHref({ q })} className="underline">
                search the documents
              </Link>
              .
            </p>
          )}

          {data && !data.unavailable && (
            <div className="mt-8 flex flex-col gap-8">
              {data.alternatives.length > 0 && !tab && (
                <section
                  aria-labelledby="close-call"
                  className="border-2 border-signal-amber/60 p-4"
                >
                  <h2 id="close-call" className="font-mono text-xs text-signal-amber">
                    Close call. Which one did you mean?
                  </h2>
                  <div className="mt-2">
                    {data.alternatives.map((a) => (
                      <ItemLink key={a.url} item={a} showGroup />
                    ))}
                  </div>
                </section>
              )}

              {(data.documentsUnavailable || data.documentsPartial) && (
                <p
                  role="status"
                  className="border border-signal-amber/30 bg-signal-amber/5 px-3 py-2 text-xs text-signal-amber"
                >
                  {data.documentsUnavailable
                    ? "Documents couldn't be searched just now, so this list has members, bills and the site's other pages only."
                    : "Documents are matched by their words only: the meaning-based index is rebuilding after a data refresh."}
                </p>
              )}

              <section aria-labelledby="results-heading">
                <h2
                  id="results-heading"
                  className="flex flex-wrap items-baseline justify-between gap-2 border-b-2 border-ink-lo pb-1 font-mono text-xs text-ink-hi"
                >
                  <span>
                    {total === 0
                      ? `Nothing on file for “${q}”`
                      : tab
                        ? `${tabLabel ?? "Results"}: ${matches(shown.length)} for “${q}”`
                        : `${matches(total)} for “${q}”, best first`}
                  </span>
                  {!tab && total > shown.length && (
                    <span className="text-ink-lo">showing the first {shown.length}</span>
                  )}
                </h2>
                <div>
                  {shown.map((item) => (
                    <ItemLink key={item.url} item={item} showGroup={!tab} />
                  ))}
                </div>
                {!tab && data.kinds.some((k) => k.kind === "document") && (
                  <Link
                    prefetch={false}
                    href={documentsHref({ q })}
                    className="mt-3 inline-flex min-h-11 items-center font-mono text-xs text-ink-hi underline underline-offset-4"
                  >
                    Every document, with filters &rarr;
                  </Link>
                )}
              </section>

              {total === 0 && (
                <section aria-label="Closest names" className="border-2 border-white/25 p-4">
                  {data.near.length > 0 ? (
                    <>
                      <p className="font-mono text-sm text-ink-hi">Closest by spelling:</p>
                      <div className="mt-2">
                        {data.near.map((a) => (
                          <ItemLink key={a.url} item={a} showGroup />
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="font-mono text-sm text-ink-hi">
                      No name on the site is spelled anything like that.
                    </p>
                  )}
                  <p className="mt-3 font-mono text-xs text-ink-lo">
                    Searched: member, president and justice names, bill numbers and titles, roll
                    calls, agencies, committees, states, districts, races, ballot measures, issues,
                    nominations, page names, and every federal document by its words and its
                    meaning.
                  </p>
                </section>
              )}
            </div>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
