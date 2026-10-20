"use client";

import { useEffect, useState } from "react";
import { fetchJusticeCases } from "@/lib/api";
import { splitLabel, termsPhrase } from "@/lib/court";
import { CaseSources } from "@/components/court/RecentDecisions";
import type { JusticeCasePage } from "@/types/court";

const OPINION: Record<string, string> = {
  majority: "Wrote the opinion of the Court",
  dissent: "Wrote a dissent",
  concurrence: "Wrote a concurrence",
};

const BUTTON =
  "inline-flex min-h-11 items-center px-2 font-mono text-sm text-ink-hi underline underline-offset-4 hover:text-phos disabled:cursor-default disabled:text-ink-min disabled:no-underline";

/**
 * Every case on a justice's record, newest first, twenty at a time: the
 * justice's side, the opinion they wrote, the Court's split and the case's
 * sources. Every value is the API's (court_service.justice_cases).
 */
export default function JusticeCases({ justiceId }: { justiceId: string }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<JusticeCasePage | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    fetchJusticeCases(justiceId, page)
      .then((d) => live && (setData(d), setError(false)))
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
  }, [justiceId, page]);

  const pages = data ? Math.max(1, Math.ceil(data.total / data.perPage)) : 1;

  return (
    <section
      id="cases"
      aria-labelledby="cases-heading"
      className="flex flex-col gap-4 border border-white/25 bg-surface px-5 py-5 font-sans"
    >
      <div>
        <h2 id="cases-heading" className="text-[19px] font-semibold text-ink-hi">
          Every case on the record
        </h2>
        <p className="mt-1 font-mono text-xs text-ink-min">
          {data && data.total > 0
            ? `${data.total} orally argued cases · ${termsPhrase(data.terms)}`
            : "Orally argued cases, newest first"}
        </p>
      </div>
      {error ? (
        <p className="text-base text-ink">The case list could not be loaded. Try again shortly.</p>
      ) : !data ? (
        <p className="text-base text-ink-lo" role="status">
          Loading the cases&hellip;
        </p>
      ) : data.total === 0 ? (
        <p className="text-base text-ink">No decided case is on this justice&apos;s record yet.</p>
      ) : (
        <>
          <div
            className="overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label="Cases on the record"
          >
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                Cases on the record, newest first, page {data.page} of {pages}
              </caption>
              <thead>
                <tr className="border-b border-white/15 text-left font-mono text-xs text-ink-min">
                  <th scope="col" className="py-2 pr-3 font-normal">
                    Case
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    Vote
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-normal">
                    Court
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.cases.map((c) => (
                  <tr key={c.caseId} className="border-b border-white/[0.07] align-top">
                    <th scope="row" className="py-2.5 pr-3 text-left font-normal">
                      <span className="block text-ink-hi">{c.name}</span>
                      <span className="block font-mono text-xs text-ink-lo">
                        {c.term} term{c.decided ? ` · decided ${c.decided}` : ""}
                      </span>
                      <CaseSources links={c.links} />
                    </th>
                    <td className="py-2.5 pr-3 text-ink">
                      <span className={c.vote === "minority" ? "text-signal-red" : "text-ink-hi"}>
                        {c.vote === "minority"
                          ? "Dissent"
                          : c.vote === "majority"
                            ? "Majority"
                            : c.vote}
                      </span>
                      {OPINION[c.opinion] && (
                        <span className="block text-xs text-ink-lo">{OPINION[c.opinion]}</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap py-2.5 text-right font-mono tabular-nums text-ink">
                      {splitLabel(c.split)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <nav
              aria-label="Case list pages"
              className="flex flex-wrap items-center justify-between gap-3"
            >
              <button
                type="button"
                className={BUTTON}
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                &larr; Newer
              </button>
              <span className="font-mono text-sm text-ink-lo" aria-live="polite">
                Page {data.page} of {pages}
              </span>
              <button
                type="button"
                className={BUTTON}
                disabled={page >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Older &rarr;
              </button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
