import Link from "next/link";
import { pageMetadata } from "@/lib/site";
import { fetchCommitteeRecord, fetchCommittees } from "@/lib/congressServer";
import { CHAMBER_NAME, currentCongress } from "@/lib/congress";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import CommitteeEntries from "@/components/congress/CommitteeEntries";
import type { CommitteeSummary } from "@/types/congress";

export const metadata = pageMetadata({
  title: "Committee Hearings, Markups & Reports in Congress",
  description:
    "Every Senate and House committee meeting and every committee report on a bill this Congress, as the Congressional Record's Daily Digest printed them, by committee, by word or by bill number.",
  path: "/congress/committees",
});

const PER_PAGE = 20;
// Where a search lands: on a phone the masthead and the form fill the first
// screen, and the first result sat below it (1.14 screens down, 2026-10-14).
const RESULTS = "#committee-results";

type Search = { committee?: string; q?: string; kind?: string; page?: string };

function one(v: string | string[] | undefined): string {
  return (Array.isArray(v) ? v[0] : v)?.trim() ?? "";
}

// A committee is chosen as "<chamber>|<name>": the same name ("Committee on
// the Judiciary") is a committee in each chamber.
function committeeValue(c: Pick<CommitteeSummary, "chamber" | "name">): string {
  return `${c.chamber}|${c.name}`;
}

function href(search: Search, page: number): string {
  const q = new URLSearchParams();
  for (const k of ["committee", "q", "kind"] as const) if (search[k]) q.set(k, search[k]!);
  if (page > 1) q.set("page", String(page));
  const s = q.toString();
  return s ? `/congress/committees?${s}${RESULTS}` : "/congress/committees";
}

const FIELD =
  "min-h-11 w-full border border-white/15 bg-surface-base px-3 font-mono text-sm text-ink-hi outline-none focus:border-phos/40";
const LABEL = "font-mono text-xs text-ink-min";

/**
 * Committee meetings and reports, findable without knowing the date: by
 * committee, by words (a topic, a witness, a nominee) or by bill number.
 * A plain GET form over the server's own render, so every search is a URL
 * and nothing waits for JavaScript.
 */
export default async function CommitteesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const raw = await searchParams;
  const search: Search = {
    committee: one(raw.committee),
    q: one(raw.q),
    kind: ["meeting", "report"].includes(one(raw.kind)) ? one(raw.kind) : "",
  };
  const page = Math.max(1, Number.parseInt(one(raw.page), 10) || 1);
  const [chamber, ...nameParts] = search.committee ? search.committee.split("|") : [];
  const name = nameParts.join("|");
  const congress = currentCongress();
  const [committees, record] = await Promise.all([
    fetchCommittees(),
    fetchCommitteeRecord({
      chamber: chamber === "senate" || chamber === "house" ? chamber : undefined,
      committee: name || undefined,
      q: search.q || undefined,
      kind: search.kind || undefined,
      congress,
      page,
      per_page: PER_PAGE,
    }),
  ]);
  const byChamber = (c: "senate" | "house") => committees.filter((x) => x.chamber === c);
  const total = record?.total ?? 0;
  const filtered = !!(search.committee || search.q || search.kind);

  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-5xl">
          <SectionTabs section="congress" active="committees" />
          <PageMasthead
            className="mb-8"
            eyebrow="Committees · meetings and reports"
            title="Committee record"
          >
            <p>
              Every committee hearing, markup and business meeting, and every report filed on a
              bill, as the Congressional Record&apos;s Daily Digest printed it. Find one by
              committee, by a word (a topic, a nominee, a witness) or by a bill number.
            </p>
          </PageMasthead>

          <form
            method="get"
            action={`/congress/committees${RESULTS}`}
            className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2"
          >
            <label className="flex flex-col gap-1 sm:col-span-2">
              <span className={LABEL}>Words or bill number</span>
              <input
                type="search"
                name="q"
                defaultValue={search.q}
                placeholder="e.g. veterans, a nominee, H.R. 1"
                className={FIELD}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={LABEL}>Committee</span>
              <select name="committee" defaultValue={search.committee} className={FIELD}>
                <option value="">Every committee</option>
                {(["senate", "house"] as const).map((c) => (
                  <optgroup key={c} label={CHAMBER_NAME[c]}>
                    {byChamber(c).map((x) => (
                      <option key={committeeValue(x)} value={committeeValue(x)}>
                        {x.name} ({x.meetings})
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={LABEL}>Show</span>
              <select name="kind" defaultValue={search.kind} className={FIELD}>
                <option value="">Meetings and reports</option>
                <option value="meeting">Meetings</option>
                <option value="report">Reports</option>
              </select>
            </label>
            <div className="flex flex-wrap items-center gap-4 sm:col-span-2">
              <button
                type="submit"
                className="min-h-11 border border-phos/40 px-5 font-mono text-sm text-phos hover:bg-phos/10"
              >
                [ Find ]
              </button>
              {filtered && (
                <Link
                  href="/congress/committees"
                  className="font-mono text-xs text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
                >
                  Clear
                </Link>
              )}
            </div>
          </form>

          <section aria-labelledby="committee-results" className="flex flex-col gap-3">
            <h2
              id="committee-results"
              className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-white/15 pb-2 font-mono text-xs text-ink-min"
            >
              <span>
                {total.toLocaleString("en-US")} {total === 1 ? "entry" : "entries"}
                {record?.bill ? ` naming ${record.bill.billLabel}` : ""}
                {name ? ` · ${CHAMBER_NAME[chamber as "senate" | "house"] ?? ""} ${name}` : ""}
              </span>
              {record && record.totalPages > 1 && (
                <span>
                  Page {record.page} of {record.totalPages}
                </span>
              )}
            </h2>
            {record && record.entries.length > 0 ? (
              <CommitteeEntries entries={record.entries} />
            ) : (
              <p className="text-ink-lo">
                Nothing in the committee record matches. Reports name no committee in the Digest, so
                a committee search shows its meetings only.
              </p>
            )}
            {record && record.totalPages > 1 && (
              <nav aria-label="Pages" className="flex flex-wrap gap-2 pt-2">
                {record.page > 1 && (
                  <Link
                    href={href(search, record.page - 1)}
                    className="flex min-h-11 items-center border border-white/15 px-3 font-mono text-sm text-ink-lo hover:border-white/30 hover:text-ink-hi"
                  >
                    ← Newer
                  </Link>
                )}
                {record.page < record.totalPages && (
                  <Link
                    href={href(search, record.page + 1)}
                    className="flex min-h-11 items-center border border-white/15 px-3 font-mono text-sm text-ink-lo hover:border-white/30 hover:text-ink-hi"
                  >
                    Older →
                  </Link>
                )}
              </nav>
            )}
          </section>
        </div>
      </main>
      <Footer />
    </div>
  );
}
