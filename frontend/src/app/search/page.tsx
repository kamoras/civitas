import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SearchCombobox from "@/components/search/SearchCombobox";
import { pageMetadata } from "@/lib/site";
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
  const q = one((await searchParams).q);
  return pageMetadata({
    title: q ? `Search: ${q}` : "Search",
    description:
      "Search the whole Civitas record: members of Congress, bills, roll calls, agencies, committees, districts, races, ballot measures, issues, nominations, documents and pages.",
    path: "/search",
    // A results page is a different page for every query: never indexed.
    noindex: true,
  });
}

async function fetchResults(q: string): Promise<SearchResults | null> {
  try {
    const res = await fetch(`${BACKEND}/api/search?q=${encodeURIComponent(q)}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data?.groups) ? (data as SearchResults) : null;
  } catch {
    return null;
  }
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
        {showGroup && (
          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-lo">
            {item.group}
          </span>
        )}
      </span>
      {item.context && <span className="font-mono text-xs text-ink-min">{item.context}</span>}
    </Link>
  );
}

function matches(n: number): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? "match" : "matches"}`;
}

/**
 * Site search's results: every match grouped by kind, with counts, each
 * linking to its page. A query that names one page clearly (an exact
 * identifier, or a match well clear of the next) never sees this page: the
 * server sends the reader straight there, JavaScript or not. `list=1`
 * shows the list anyway.
 */
export default async function SearchPage({ searchParams }: { searchParams: Params }) {
  const params = await searchParams;
  const q = one(params.q);
  const data = q ? await fetchResults(q) : null;
  if (data?.direct && one(params.list) !== "1") redirect(data.direct);

  const total = data ? data.groups.reduce((n, g) => n + g.count, 0) : 0;
  const exploreCount = data?.explore.count ?? null;

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
            <SearchCombobox initialQuery={q} autoFocus={!q} />
          </div>

          {q && !data && (
            <p
              role="alert"
              className="mt-8 border-2 border-signal-amber/60 p-4 font-mono text-sm text-ink-hi"
            >
              Search didn&apos;t answer just now. Try again, or search the documents in{" "}
              <Link href={`/explore?q=${encodeURIComponent(q)}`} className="underline">
                Explore
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
              <Link href={`/explore?q=${encodeURIComponent(q)}`} className="underline">
                search the documents in Explore
              </Link>
              .
            </p>
          )}

          {data && !data.unavailable && (
            <div className="mt-8 flex flex-col gap-8">
              <h2 className="font-mono text-sm uppercase tracking-[0.14em] text-ink-lo">
                {total > 0 ? `${matches(total)} for “${q}”` : `Nothing on file for “${q}”`}
              </h2>

              {data.alternatives.length > 0 && (
                <section
                  aria-labelledby="close-call"
                  className="border-2 border-signal-amber/60 p-4"
                >
                  <h3
                    id="close-call"
                    className="font-mono text-xs uppercase tracking-[0.14em] text-signal-amber"
                  >
                    Close call. Which one did you mean?
                  </h3>
                  <div className="mt-2">
                    {data.alternatives.map((a) => (
                      <ItemLink key={a.url} item={a} showGroup />
                    ))}
                  </div>
                </section>
              )}

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
                    nominations, document identifiers and titles, and page names.
                  </p>
                </section>
              )}

              {data.groups.map((g) => (
                <section key={g.kind} aria-labelledby={`group-${g.kind}`}>
                  <h3
                    id={`group-${g.kind}`}
                    className="flex items-baseline justify-between border-b-2 border-ink-lo pb-1 font-mono text-xs uppercase tracking-[0.14em] text-ink-hi"
                  >
                    <span>{g.label}</span>
                    <span className="text-ink-lo">
                      {g.count > g.items.length
                        ? `${g.items.length} of ${g.count.toLocaleString("en-US")}`
                        : g.count}
                    </span>
                  </h3>
                  <div>
                    {g.items.map((item) => (
                      <ItemLink key={item.url} item={item} />
                    ))}
                  </div>
                </section>
              ))}

              <section aria-labelledby="group-explore" className="border-2 border-white/25 p-4">
                <h3
                  id="group-explore"
                  className="font-mono text-xs uppercase tracking-[0.14em] text-ink-hi"
                >
                  Full text, in Explore
                </h3>
                <p className="mt-2 font-sans text-base text-ink-lo">
                  {exploreCount === null
                    ? "Explore searches the words inside every document, speech and opinion."
                    : exploreCount === 0
                      ? "No document, speech or opinion uses these words."
                      : `${exploreCount > 1000 ? "More than 1,000" : exploreCount.toLocaleString("en-US")} ${
                          exploreCount === 1 ? "document uses" : "documents use"
                        } these words.`}
                </p>
                <Link
                  href={data.explore.url}
                  className="mt-2 inline-flex min-h-11 items-center font-mono text-xs uppercase tracking-[0.14em] text-ink-hi underline underline-offset-4"
                >
                  More in Explore &rarr;
                </Link>
              </section>
            </div>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
