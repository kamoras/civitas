import Link from "next/link";
import type { CaseLinks, CourtDecisionPage, CourtJustice } from "@/types/court";
import { splitLabel, surname } from "@/lib/court";

const LINK =
  "inline-flex min-h-6 items-center font-mono text-xs uppercase tracking-[0.1em] text-ink-lo underline decoration-white/25 underline-offset-4 hover:text-phos";

function longDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
}

/** A case's sources: Oyez's case page, the Court's slip opinion, and the
 *  opinion's page in Explore, each only where it exists. */
export function CaseSources({ links }: { links: CaseLinks }) {
  const items: [string, string, boolean][] = [];
  if (links.explore) items.push(["In Explore", links.explore, true]);
  if (links.opinion) items.push(["Opinion (PDF)", links.opinion, false]);
  if (links.oyez) items.push(["Oyez", links.oyez, false]);
  if (items.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-x-4">
      {items.map(([label, href, internal]) =>
        internal ? (
          <Link key={label} href={href} className={LINK}>
            {label}
          </Link>
        ) : (
          <a key={label} href={href} className={LINK}>
            {label}
          </a>
        )
      )}
    </span>
  );
}

function names(ids: string[], byId: Map<string, CourtJustice>): string {
  return ids
    .map((id) => byId.get(id))
    .filter((j): j is CourtJustice => !!j)
    .map(surname)
    .join(", ");
}

/**
 * The latest decisions, each with its lineup: one cell per sitting justice
 * in Martin-Quinn order, filled for the majority and outlined for a
 * dissent, the three letters of each surname under it; then the same in
 * words. Paginated by URL (`?page=`), so every page is a link that works
 * without JavaScript.
 */
export default function RecentDecisions({
  page,
  justices,
  hrefFor,
}: {
  page: CourtDecisionPage;
  justices: CourtJustice[];
  hrefFor: (page: number) => string;
}) {
  const byId = new Map(justices.map((j) => [j.id, j]));
  const pages = Math.max(1, Math.ceil(page.total / page.perPage));
  if (page.total === 0) {
    return <p className="text-base text-ink">No decision is stored yet.</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <ol className="flex flex-col divide-y divide-white/[0.07] border-y border-white/[0.07]">
        {page.decisions.map((d) => {
          const majority = new Set(d.majority);
          const minority = new Set(d.minority);
          const author = d.author ? byId.get(d.author) : null;
          const date = longDate(d.decided);
          return (
            <li key={d.caseId} className="flex flex-col gap-2 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h4 className="text-base font-semibold text-ink-hi">{d.name}</h4>
                <span className="font-mono text-sm tabular-nums text-ink-lo">
                  {splitLabel(d.split)}
                  {d.unanimous ? " · unanimous" : ""}
                  {date ? ` · ${date}` : ""}
                </span>
              </div>
              <div aria-hidden="true" className="flex gap-1">
                {justices.map((j) => {
                  const side = majority.has(j.id) ? "maj" : minority.has(j.id) ? "min" : "none";
                  return (
                    // Cells share the row and shrink with it (to the
                    // screen at 200% text), up to the old 2.5rem each.
                    <span
                      key={j.id}
                      className="flex min-w-0 max-w-10 flex-1 basis-0 flex-col items-center gap-0.5"
                    >
                      <span
                        className={`h-4 w-full ${
                          side === "maj"
                            ? "bg-ink-hi"
                            : side === "min"
                              ? "border-2 border-signal-red"
                              : "border border-dashed border-white/30"
                        }`}
                      />
                      <span className="font-mono text-[11px] text-ink-min">
                        {surname(j).slice(0, 3)}
                      </span>
                    </span>
                  );
                })}
              </div>
              <p className="text-sm leading-relaxed text-ink">
                <span className="text-ink-lo">Majority:</span>{" "}
                {names(d.majority, byId) || "none stored"}
                {d.minority.length > 0 && (
                  <>
                    {" · "}
                    <span className="text-ink-lo">Dissent:</span> {names(d.minority, byId)}
                  </>
                )}
                {author && (
                  <>
                    {" · "}
                    <span className="text-ink-lo">Opinion of the Court:</span> {surname(author)}
                  </>
                )}
              </p>
              <CaseSources links={d.links} />
            </li>
          );
        })}
      </ol>
      {pages > 1 && (
        <nav
          aria-label="Decisions pages"
          className="flex flex-wrap items-center justify-between gap-4 font-mono text-sm"
        >
          {page.page > 1 ? (
            <Link
              href={hrefFor(page.page - 1)}
              className="inline-flex min-h-11 items-center text-ink-hi underline underline-offset-4 hover:text-phos"
            >
              &larr; Newer
            </Link>
          ) : (
            <span />
          )}
          <span className="text-ink-lo">
            Page {page.page} of {pages}
          </span>
          {page.page < pages ? (
            <Link
              href={hrefFor(page.page + 1)}
              className="inline-flex min-h-11 items-center text-ink-hi underline underline-offset-4 hover:text-phos"
            >
              Older &rarr;
            </Link>
          ) : (
            <span />
          )}
        </nav>
      )}
    </div>
  );
}
