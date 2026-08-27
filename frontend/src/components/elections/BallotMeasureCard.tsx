import { measureStatusLabel } from "@/lib/elections";
import { safeHref } from "@/lib/formatting";
import type { BallotMeasure } from "@/types/election";

/** One ballot measure.
 *
 * Everything rendered here is verbatim source text with its author named
 * beside it. There is deliberately no plain-language rewrite: the local
 * model cannot be checked for the failure that matters most on a ballot
 * — saying a YES vote does the opposite of what it does — because the
 * platform's grounding checks verify that tokens came from the source,
 * not that the claim points the same direction (AGENTS.md principle 7).
 */
export default function BallotMeasureCard({ measure }: { measure: BallotMeasure }) {
  const removed = measure.status === "removed" || measure.status === "withdrawn";
  // Vote Smart-sourced, not user-controlled, but still external data
  // rendered as an href — same guard CoverageFeed.tsx uses for article
  // URLs from the news-feed pipeline, for the same reason (reject
  // javascript:/data: before it reaches a real <a>).
  const sourceHref = safeHref(measure.sourceUrl);

  return (
    <article
      className={`border p-4 ${
        removed ? "border-signal-red/40 bg-signal-red/10" : "border-white/[0.09] bg-surface"
      }`}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap mb-2">
        <h3 className="font-display font-semibold text-sm text-ink-hi">
          {measure.number || measure.title}
        </h3>
        <span
          className={`text-[9px] font-mono tracking-[0.1em] px-1.5 py-0.5 border ${
            removed ? "border-signal-red/40 text-signal-red" : "border-white/15 text-ink-lo"
          }`}
        >
          {measureStatusLabel(measure.status)}
        </span>
      </div>

      {removed && (
        // Shown, not hidden. Someone who saw this measure last week needs
        // to be told it was struck; a card that simply disappeared cannot
        // say that.
        <p className="text-xs text-signal-red mb-3">
          This measure is no longer on the ballot. It is kept here so a
          change since your last visit is visible rather than silent.
        </p>
      )}

      {measure.number && measure.title && (
        <p className="text-sm text-ink mb-3">{measure.title}</p>
      )}

      {measure.officialTitle && (
        <section className="mb-3">
          <h4 className="font-mono text-[10px] text-ink-lo tracking-widest mb-1">
            OFFICIAL BALLOT TITLE
          </h4>
          <blockquote className="text-xs text-ink border-l-2 border-white/15 pl-3">
            {measure.officialTitle}
          </blockquote>
          {measure.titleAuthority && (
            // Naming the drafter is MORE neutral than the bare quote:
            // ballot titles are routinely litigated as slanted, and who
            // wrote one is what tells a reader how to weigh it.
            <p className="text-[10px] text-ink-min mt-1">
              Drafted by {measure.titleAuthority}
            </p>
          )}
        </section>
      )}

      {measure.officialSummary && (
        <section className="mb-3">
          <h4 className="font-mono text-[10px] text-ink-lo tracking-widest mb-1">
            OFFICIAL SUMMARY
          </h4>
          <p className="text-xs text-ink whitespace-pre-line">{measure.officialSummary}</p>
        </section>
      )}

      {(measure.yesMeans || measure.noMeans) && (
        <section className="mb-3 grid gap-2 sm:grid-cols-2">
          {measure.yesMeans && (
            <div className="border border-white/[0.09] p-2.5">
              <h4 className="font-mono text-[10px] text-signal-cyan tracking-widest mb-1">
                A YES VOTE
              </h4>
              <p className="text-xs text-ink">{measure.yesMeans}</p>
            </div>
          )}
          {measure.noMeans && (
            <div className="border border-white/[0.09] p-2.5">
              <h4 className="font-mono text-[10px] text-signal-cyan tracking-widest mb-1">
                A NO VOTE
              </h4>
              <p className="text-xs text-ink">{measure.noMeans}</p>
            </div>
          )}
        </section>
      )}

      {measure.fiscalImpact && (
        <section className="mb-3">
          <h4 className="font-mono text-[10px] text-ink-lo tracking-widest mb-1">
            FISCAL IMPACT
          </h4>
          <p className="text-xs text-ink whitespace-pre-line">{measure.fiscalImpact}</p>
          {measure.fiscalAuthority && (
            <p className="text-[10px] text-ink-min mt-1">Prepared by {measure.fiscalAuthority}</p>
          )}
        </section>
      )}

      <footer className="flex items-center justify-between gap-3 flex-wrap pt-2 border-t border-white/[0.07]">
        <p className="text-[10px] text-ink-min">
          Quoted verbatim from {measure.sourceName || "the source"}
          {measure.asOf ? ` · as of ${measure.asOf.slice(0, 10)}` : ""}
        </p>
        {sourceHref && (
          <a
            href={sourceHref}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Read the full text of ${measure.number || measure.title} at the source (opens in new tab)`}
            className="text-[10px] font-mono text-signal-cyan hover:text-phos"
          >
            FULL TEXT AT SOURCE ↗
          </a>
        )}
      </footer>
    </article>
  );
}
